"""Matrix orchestrator (spec §3).

Walks a stage matrix, sweeps offered load, gates every run, writes one ledger record per
measurement point, and resumes from the ledger after any failure. The scheduling rules
are not cosmetic:

  * runs are ordered BY BAND, because the hotspot serves one band at a time and the
    switch is manual (spec §3.6);
  * repeats of a cell are NOT consecutive, so phone thermal drift shows up as spread
    rather than as a convincing downward trend (plan §7.3c).
"""
from __future__ import annotations
import itertools
import json
import random
import threading
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import gates, rfmeta
from .control import ControlError
from .stress import StressRunner, schedule
from .telemetry import Sampler, summarize
from .keepalive import Keepalive, NullKeepalive
from .control import ControlClient
from .frame import HDR_BYTES
from .h7 import H7Error, clock_mark, ppm_between
from .ledger import Ledger, git_sha, new_run_id
from .receiver import Receiver

# Sources fed over a wire by the STM32H745 generator (Stage 2), not generated on the ESP.
WIRED_SOURCES = ("qspi", "sdio")
H7_TARGET = {"esp32s3": "s3", "esp32c5": "c5"}     # the H7's `cfg target=` per board

# Cell keys that shape how a run is taken rather than what is measured (Run.opts).
CELL_OPTS = ("hold_s", "discard_s", "device_cfg", "stress", "sample_s", "long_run",
             "window_s", "reconnect", "tcp_rcvbuf", "watchdog_s", "reset_after_stall")

DEFAULT_SWEEP = {"start_mbps": 1, "stop_mbps": 60, "steps": 8,
                 "hold_s": 60, "discard_s": 3}
LOSS_THRESHOLD_PCT = 0.1
HEAP_FLOOR_BYTES = 20000      # free internal heap a configuration must never go below


@dataclass
class Run:
    cell_id: str
    chip: str
    band: str
    source: str
    transport: str
    tune: str
    offered_bps: int
    repeat: int
    # Per-cell run options (campaign cells): hold_s, discard_s, device_cfg, stress,
    # sample_s, long_run, window_s, reconnect, tcp_rcvbuf. Not part of the run's identity.
    opts: dict = field(default_factory=dict, compare=False)

    @property
    def key(self) -> tuple:
        return (self.cell_id, self.offered_bps, self.repeat)


@dataclass
class Matrix:
    stage: int
    repeats: int
    sweep: dict
    cells: list[dict]
    ap: str = "iphone-hotspot"
    frame_bytes: int = 270
    expected_idf: str = "v6.0.2"
    # The Wi-Fi network this matrix must run on. When set it is ENFORCED: the boards'
    # firmware must be built for it, and this host must be associated to it, or nothing
    # is flashed and no run is taken.
    ssid: str | None = None
    # True for a rig where this host and the boards knowingly share one Wi-Fi band (a
    # single-band hotspot with the Mac on its Wi-Fi): the shared-band refusal becomes a
    # warning carried by every run.
    allow_shared_band: bool = False
    # Repeat numbers start here. A re-measurement of points already in the ledger takes
    # fresh numbers, or resume would treat it as done and return the old runs.
    first_repeat: int = 1
    # The network THIS HOST must be on, when it differs from the boards' `ssid`: a router
    # that offers one band-locked network per band for the host, so the host can sit on
    # the band a cell is not measuring (docs/CAMPAIGN.md). Default: the boards' network.
    host_ssid: str | None = None
    # Where each run's time series (receiver windows, device samples, stress log) is
    # written, one JSON file per run. None = not written.
    timeseries_dir: str | None = None
    # Shuffle the order of runs within each (repeat, band, chip, build) block, with this
    # seed: no reflash is added, and an effect of the PREVIOUS run (what it left in the
    # board's memory or the access point's queues) can no longer pass for an effect of
    # the load. None keeps the fixed order.
    shuffle_seed: int | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Matrix":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls(stage=raw.get("stage", 0), repeats=raw.get("repeats", 3),
                   first_repeat=int(raw.get("first_repeat", 1)),
                   sweep={**DEFAULT_SWEEP, **(raw.get("sweep") or {})},
                   cells=raw.get("cells") or [], ap=raw.get("ap", "iphone-hotspot"),
                   frame_bytes=raw.get("frame_bytes", 270),
                   expected_idf=raw.get("expected_idf", "v6.0.2"),
                   ssid=raw.get("ssid"),
                   allow_shared_band=bool(raw.get("allow_shared_band", False)),
                   host_ssid=raw.get("host_ssid"),
                   timeseries_dir=raw.get("timeseries_dir"))

    def offered_steps(self) -> list[int]:
        s, e, n = self.sweep["start_mbps"], self.sweep["stop_mbps"], self.sweep["steps"]
        if n <= 1:
            return [int(e * 1e6)]
        step = (e - s) / (n - 1)
        return [int((s + i * step) * 1e6) for i in range(n)]

    def expand(self, bands: list[str] | None = None,
               transports: list[str] | None = None, *,
               chips: list[str] | None = None,
               sources: list[str] | None = None) -> list[Run]:
        """bands / transports / chips / sources: restrict to these (the points left out
        stay pending for a later sweep).

        chips and sources exist for Stage 2: only a chip that is physically wired to the
        generator can run a wired cell, and that is a fact about the bench, not the matrix.

        The hotspot serves one band at a time and the switch is a manual toggle, so a
        sweep restricted to one band is the unit that can run unattended end to end.
        Two such blocks with one toggle between them replaces six blocking prompts
        scattered through the night.
        """
        runs: list[Run] = []
        sweep = self.offered_steps()
        for rep in range(self.first_repeat, self.first_repeat + self.repeats):  # OUTER loop
            for cell in self.cells:
                chip, band = cell["chip"], str(cell["band"])
                if bands and band not in bands:
                    continue
                # A cell may name its own loads (Mbit/s): a saturating TCP point needs
                # one load, not the whole sweep.
                steps = ([int(float(x) * 1e6) for x in cell["loads_mbps"]]
                         if cell.get("loads_mbps") else sweep)
                source = cell.get("source", "synth")
                if (chips and chip not in chips) or (sources and source not in sources):
                    continue
                for transport in _aslist(cell.get("transport", ["udp"])):
                    if transports and transport not in transports:
                        continue
                    for tune in _aslist(cell.get("tune", ["tuned"])):
                        cid = f"s{self.stage}-{chip}-{band}-{source}-{transport}-{tune}"
                        if cell.get("tag"):
                            cid += f"-{cell['tag']}"
                        opts = {k: cell[k] for k in CELL_OPTS if k in cell}
                        for off in steps:
                            runs.append(Run(cid, chip, band, source, transport,
                                            tune, off, rep, opts))
        # Band-ordered: minimise manual hotspot switches. Within a chip, group by tune
        # (the only axis that needs a reflash; transport and load are runtime) and
        # alternate its direction per repeat, so each repeat starts on the build the
        # previous one ended with: 2 flashes per chip in repeat 1, 1 per repeat after.
        tunes = sorted({r.tune for r in runs})

        def tune_rank(r: Run) -> int:
            i = tunes.index(r.tune)
            return i if r.repeat % 2 else -i
        runs.sort(key=lambda r: (r.repeat, str(r.band), r.chip, tune_rank(r),
                                 r.cell_id, r.offered_bps))
        if self.shuffle_seed is not None:
            out: list[Run] = []
            for key, grp in itertools.groupby(
                    runs, key=lambda r: (r.repeat, str(r.band), r.chip, r.tune)):
                g = list(grp)
                random.Random(int(self.shuffle_seed) * 1000 + key[0]).shuffle(g)
                out += g
            runs = out
        return runs


class Driver:
    """What the orchestrator needs from a device. Implemented for real hardware and for
    the loopback fake, so the whole pipeline runs with no boards attached."""

    def ensure_flashed(self, chip: str, source: str, tune: str) -> dict: ...
    def control(self, chip: str) -> ControlClient: ...
    def band_of(self, chip: str) -> str: ...
    def request_band(self, band: str) -> None: ...


class Orchestrator:
    def __init__(self, matrix: Matrix, driver: Driver, ledger: Ledger, *,
                 rig_ceilings: dict[str, float] | None = None,
                 parity_ok: bool = True, recv_port: int = 3333,
                 bands: list[str] | None = None, keepalive: bool = False,
                 recover: bool = True, on_event=None,
                 allow_shared_band: bool = False,
                 transports: list[str] | None = None,
                 rssi_drift_warn_only: bool = False,
                 deadline: float | None = None,
                 chips: list[str] | None = None, sources: list[str] | None = None,
                 link_opts: dict | None = None, stress_pf=None):
        self.m = matrix
        self.driver = driver
        self.ledger = ledger
        self.rig_ceilings = rig_ceilings or {}
        self.parity_ok = parity_ok
        # Downgrades "this host is on the same Wi-Fi band as the cell" from a failed
        # gate to a warning. Only for a rig where that is knowingly accepted.
        self.allow_shared_band = allow_shared_band or matrix.allow_shared_band
        self.recv_port = recv_port
        self.bands = [str(b) for b in bands] if bands else None
        self.transports = [str(t) for t in transports] if transports else None
        # Record an RSSI drift as a warning instead of refusing the run (see gates.py).
        self.rssi_drift_warn_only = rssi_drift_warn_only
        # time.monotonic() value after which no further run may be started (None = none).
        self.deadline = deadline
        self.chips = list(chips) if chips else None
        self.sources = list(sources) if sources else None
        # Extra generator settings for wired cells (e.g. qspi_hz), recorded with each run.
        self.link_opts = dict(link_opts or {})
        self.stress_pf = stress_pf        # stress.PfBlock stand-in (a rehearsal's no-op)
        self.want_keepalive = keepalive
        self.want_recover = recover
        self.on_event = on_event or (lambda *_a, **_k: None)
        self._first_rssi: dict[str, float] = {}
        self._current_band: str | None = None
        self._keepalives: dict[str, object] = {}

    def pending(self) -> list[Run]:
        done = self.ledger.completed_keys()
        return [r for r in self.m.expand(self.bands, self.transports, chips=self.chips,
                                         sources=self.sources)
                if r.key not in done]

    def _keepalive_for(self, chip: str):
        """One keepalive per chip, created lazily once the device is reachable."""
        if chip not in self._keepalives:
            if not self.want_keepalive:
                self._keepalives[chip] = NullKeepalive()
            else:
                # Keepalive starts paused; it must be resumed or it never polls, and the
                # idle board is dropped by the hotspot 90 s into the other chip's block.
                ka = Keepalive(self.driver.control(chip), on_event=self.on_event).start()
                ka.resume()
                self._keepalives[chip] = ka
        return self._keepalives[chip]

    def _hello_or_recover(self, chip: str, c):
        """A board that has wedged must not stall the whole sweep.

        One power-cycle attempt, then give up on this run and move on — the ledger
        records the failure and resume will retry the point later.
        """
        try:
            return c.hello(), False
        except (ControlError, OSError) as e:
            if not self.want_recover or not hasattr(self.driver, "recover"):
                raise
            self.on_event("recovering", chip=chip, error=str(e))
            if not self.driver.recover(chip):
                return None, True
            try:
                return c.hello(), True
            except (ControlError, OSError):
                return None, True

    # What a run costs in wall-clock time beyond its hold, and what a build + flash +
    # boot costs, for the time budget. Deliberately generous: the budget is a promise.
    RUN_OVERHEAD_S = 15.0
    FLASH_COST_S = 150.0

    def run_cost_s(self, r: Run) -> float:
        """Worst-case wall-clock seconds the next run needs, flash included."""
        cost = float(r.opts.get("hold_s", self.m.sweep["hold_s"])) + self.RUN_OVERHEAD_S
        needs_flash = getattr(self.driver, "needs_flash", None)
        if needs_flash is not None and needs_flash(r.chip, r.source, r.tune):
            cost += self.FLASH_COST_S
        return cost

    def run_all(self, limit: int | None = None) -> dict:
        todo = self.pending()
        if limit:
            todo = todo[:limit]
        ok = invalid = errors = 0
        stopped_for_time = False
        broken: dict[str, str] = {}     # cell_id -> why it was abandoned this pass
        # Boards that did not come back even through the recovery ladder: the rest of
        # their runs wait for the next pass instead of each paying for the ladder again.
        dead: set[str] = set()
        try:
            for r in todo:
                if r.cell_id in broken or r.chip in dead:
                    continue
                if str(r.band) != str(self._current_band):
                    self.driver.request_band(str(r.band))
                    self._current_band = str(r.band)
                    self.on_event("band", band=r.band)
                # A time budget is kept by not STARTING what cannot finish inside it: a
                # run (and the flash it may need) is never cut short part-way.
                if self.deadline is not None and \
                        time.monotonic() + self.run_cost_s(r) > self.deadline:
                    stopped_for_time = True
                    break
                try:
                    rec = self.run_one(r)
                except Exception as e:                       # noqa: BLE001
                    # A failed build/flash or an unreachable board costs this cell for
                    # the rest of the pass, not the night. Nothing is written to the
                    # ledger, so the next pass (resume) retries every skipped point.
                    broken[r.cell_id] = f"{type(e).__name__}: {e}"
                    errors += 1
                    self.on_event("run_error", run=r, error=broken[r.cell_id])
                    continue
                if rec.get("valid", True):
                    ok += 1
                else:
                    invalid += 1
                    if any(f.startswith("device did not answer the control plane")
                           for f in rec.get("gate_failures") or []):
                        dead.add(r.chip)
        finally:
            for ka in self._keepalives.values():
                ka.stop()
            self._keepalives.clear()    # a stopped keepalive never polls again
        return {"attempted": len(todo), "valid": ok, "invalid": invalid,
                "errors": errors, "abandoned_cells": broken,
                "remaining": len(self.pending()),
                "stopped_for_time": stopped_for_time}

    def _dead_record(self, r: Run, recovered: bool) -> dict:
        """The device did not answer even after a recovery attempt. Recorded as an
        invalid run rather than raising, so one wedged board costs one point instead of
        the remaining night."""
        return {
            "run_id": new_run_id(r.cell_id, r.offered_bps, r.repeat),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_sha": git_sha("."), "target": r.chip, "variant": r.tune,
            "source": r.source,
            "link": {"synth": "none", "qspi": "qspi", "sdio": "sdio4"}.get(r.source, r.source),
            "transport": r.transport, "offered_bps": r.offered_bps, "rate_hz": None,
            "duration_s": 0.0, "cell_id": r.cell_id, "repeat": r.repeat,
            "rung": None, "fw_sha": None, "parity": {"ok": self.parity_ok},
            "metrics": {"goodput_bps": 0.0, "frames": 0, "loss_pct": 0.0,
                        "duration_s": 0.0, "not_run": True},
            "rf": rfmeta.collect(band=str(r.band), ap=self.m.ap,
                                 rig_ceiling_mbps=self.rig_ceilings.get(str(r.band)),
                                 device_stat={}, idf_version=None,
                                 host_path_info=self._host_path(r.chip)),
            "valid": False, "reassociated": recovered, "source_limited": False,
            "gate_failures": ["device did not answer the control plane"
                              + (" even after a power cycle" if recovered else "")],
            "gate_warnings": [],
        }

    def _probe_link(self, h7, r: Run) -> None:
        """Refuse a wired cell whose link is not there BEFORE spending a hold on it.
        Raising abandons the cell for this pass, exactly like a failed flash."""
        h7.cfg(link=r.source, **self._h7_opts(r))
        last: Exception | None = None
        for _ in range(3):
            try:
                h7.probe()
                return
            except H7Error as e:
                last = e
                time.sleep(0.5)
        raise RuntimeError(f"no {r.source} link between the H7 and {r.chip}: {last}")

    def _write_timeseries(self, run_id: str, metrics, samples, stress_log) -> str | None:
        """One JSON file per run: what a soak is read from (windows, device samples,
        injected faults). The ledger keeps only the summary and this file's path."""
        try:
            d = Path(self.m.timeseries_dir)
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{run_id}.json"
            path.write_text(json.dumps({
                "run_id": run_id, "windows": getattr(metrics, "windows", []),
                "receiver_events": getattr(metrics, "events", []),
                "device": samples, "stress": stress_log}, separators=(",", ":")))
            return str(path)
        except OSError:
            return None

    def _h7_opts(self, r: Run) -> dict:
        """The generator settings for a wired run. `target` always follows the cell's
        chip: the H7 drives a different pin set per ESP (docs/CAMPAIGN_PROTOCOL.md §2),
        and a C5 cell opened on the S3's pins would measure the wrong board."""
        return {**self.link_opts, "target": H7_TARGET.get(r.chip, "s3")}

    def _host_path(self, chip: str) -> dict:
        """How this host reaches the board (the second hop). Drivers that know say so;
        the loopback fake has no second hop."""
        fn = getattr(self.driver, "host_path", None)
        if fn is None:
            return {"link": "loopback"}
        try:
            return fn(chip) or {"link": "unknown"}
        except Exception:                                    # noqa: BLE001
            return {"link": "unknown"}

    def run_one(self, r: Run) -> dict:
        wired = r.source in WIRED_SOURCES
        self.driver.ensure_flashed(r.chip, r.source, r.tune)
        h7 = None
        if wired:
            self.driver.ensure_h7()
            h7 = self.driver.h7()
            self._probe_link(h7, r)
        c = self.driver.control(r.chip)
        ka = self._keepalive_for(r.chip)
        reassociated = ka.take_reassociation()

        hello, recovered = self._hello_or_recover(r.chip, c)
        if hello is None:
            rec = self._dead_record(r, recovered)
            self.ledger.append(rec)
            self.on_event("run", run=r, valid=False, goodput_mbps=0.0, loss=None)
            return rec
        stat0 = c.stat()
        ceiling = self.rig_ceilings.get(str(r.band))
        host_path = self._host_path(r.chip)

        # The RSSI every later run of this cell is compared against is taken at the END
        # of the cell's first run, not before it. A reading taken moments after a board
        # boots and associates is not settled: on the first real sweep the S3 reported
        # -55 dBm right after its flash and -42 dBm from then on, and every later run of
        # the cell was refused as a 13 dB "drift". Until that reference exists there is
        # nothing to compare with, so the first run is not drift-checked.
        first_rssi = self._first_rssi.get(r.cell_id)
        pre_ctx = {
            "associated": True, "band": self.driver.band_of(r.chip),
            "host_link": host_path.get("link"), "host_band": host_path.get("band"),
            "allow_shared_band": self.allow_shared_band,
            "expected_ssid": self.m.host_ssid or self.m.ssid,
            "host_ssid": host_path.get("ssid"),
            "expected_band": r.band, "rig_ceiling_mbps": ceiling,
            "offered_bps": r.offered_bps,
            "rssi_dbm": stat0.get("rssi") if first_rssi is not None else None,
            "first_rssi_dbm": first_rssi, "ambient_ok": True,
            "rssi_drift_warn_only": self.rssi_drift_warn_only,
            "esp_reset_since_last": False, "idf_version": hello.get("idf"),
            "expected_idf": self.m.expected_idf, "proto_ok": True,
            "parity_ok": self.parity_ok,
        }
        pre = gates.pre_run(pre_ctx)

        payload_bytes = self.m.frame_bytes - HDR_BYTES
        opts = r.opts or {}
        hold = float(opts.get("hold_s", self.m.sweep["hold_s"]))
        discard = float(opts.get("discard_s", self.m.sweep["discard_s"]))
        samples: list[dict] = []
        stress_log: list[dict] = []
        # A run blocked by a pre-gate is still recorded, with zeroed metrics: the
        # ledger must show that the point was attempted and refused, or resume would
        # retry it forever and the report would silently under-count the matrix.
        metrics_d: dict = {"goodput_bps": 0.0, "frames": 0, "loss_pct": 0.0,
                           "duration_s": 0.0, "not_run": True}
        post = gates.GateResult(True, [], [])
        stat1: dict = {}
        heap_min: int | None = None

        host_drops: int | None = None      # datagrams THIS HOST discarded during the window
        h7_sum: dict = {}
        esp_ing: dict = {}
        h7_error: str | None = None
        clk_applied = int(round(getattr(self.driver, "h7_clk_ppm", 0.0))) if wired else 0
        clk_measured: float | None = None

        if pre.ok:
          with ka.paused():        # the instrument must not appear in its own number
            # UDP only, and not on loopback (the fake has no real network path): what
            # the host itself has dropped so far, to compare after the window closes.
            drops0 = rfmeta.host_udp_drops() \
                if r.transport == "udp" and host_path.get("link") != "loopback" else None
            rx = Receiver(r.transport, self.recv_port, payload_bytes,
                          duration_s=hold, discard_s=discard,
                          long_run=opts.get("long_run"),
                          window_s=float(opts.get("window_s", 10.0)),
                          reconnect=bool(opts.get("reconnect", False)),
                          tcp_rcvbuf=opts.get("tcp_rcvbuf"))
            rx.start()
            c.cfg(rate_bps=r.offered_bps, transport=r.transport,
                  dst=f"{self.driver.host_ip()}:{self.recv_port}",
                  dur_s=int(hold) + 5, frame_bytes=self.m.frame_bytes,
                  payload="rand", label=f"{r.cell_id}-r{r.repeat}",
                  **(opts.get("device_cfg") or {}))
            sampler = Sampler(_quick(c), float(opts["sample_s"])).start() \
                if opts.get("sample_s") else None
            c.start()
            stresser = StressRunner(schedule(opts.get("stress")), rx, c,
                                    device_ip=_addr_of(c), pf=self.stress_pf).start()
            dog = None
            if opts.get("watchdog_s") and sampler is not None and \
                    hasattr(self.driver, "recover"):
                restart = dict(rate_bps=r.offered_bps, transport=r.transport,
                               dst=f"{self.driver.host_ip()}:{self.recv_port}",
                               frame_bytes=self.m.frame_bytes, payload="rand",
                               label=f"{r.cell_id}-r{r.repeat}",
                               **(opts.get("device_cfg") or {}))
                dog = _Watchdog(rx, sampler, self.driver, r.chip, c, restart, hold,
                                float(opts["watchdog_s"]), self.on_event).start()
            if wired:
                # ESP first, then the generator: the slave must be listening before the
                # master offers load (guide §8). The commanded rate goes to the H7.
                mark_a = mark_b = None
                try:
                    h7.cfg(rate_bps=r.offered_bps, link=r.source, dur_s=int(hold) + 5,
                           frame_bytes=self.m.frame_bytes, clk_ppm=clk_applied,
                           **self._h7_opts(r))
                    mark_a = clock_mark(h7)
                    h7.start()
                except H7Error as e:
                    h7_error = str(e)
            metrics = rx.join()
            if dog is not None:
                dog.stop()
            stress_log = stresser.stop()
            if drops0 is not None:
                drops1 = rfmeta.host_udp_drops()
                host_drops = max(0, drops1 - drops0) if drops1 is not None else None
            # Sample the device WHILE IT IS STILL STREAMING, then stop it. The achieved
            # source rate and the CPU-idle figure describe the stream only while it runs:
            # the firmware zeroes its achieved rate the moment the run ends, and after
            # that the CPU is merely idle. Read from the `stop` summary — as this used
            # to — every run came back "achieved 0", was labelled source-limited, and
            # carried an idle figure for a board doing nothing. (dur_s leaves a few
            # seconds of margin so the board has not stopped on its own by now.)
            try:
                live = c.stat()
            except Exception:                                 # noqa: BLE001
                live = {}
            if wired:                                         # then stop the source first
                try:
                    mark_b = clock_mark(h7)
                    h7_sum = h7.stop()
                except H7Error as e:
                    h7_error = h7_error or str(e)
                # The run itself is the best clock calibration there is: a minute between
                # two marks. What it finds trims the NEXT run; this one used clk_applied.
                if mark_a and mark_b:
                    clk_measured = ppm_between(mark_a, mark_b)
                    if clk_measured is not None:
                        self.driver.h7_clk_ppm = clk_measured
            try:
                stat1 = c.stop().get("summary", {}) or c.stat()
            except Exception:                                 # noqa: BLE001
                stat1 = {}
            if sampler is not None:
                samples = sampler.stop()
            for k in ("achieved_bps", "idle_pct"):
                if live.get(k) is not None:
                    stat1[k] = live[k]
            if r.cell_id not in self._first_rssi:
                settled = live.get("rssi") or stat1.get("rssi")
                if settled:                       # 0 means "not associated", not 0 dBm
                    self._first_rssi[r.cell_id] = settled
            if wired:
                try:
                    esp_ing = self.driver.ingress_stat(r.chip)
                except (OSError, ValueError):
                    esp_ing = {}
                # The H7 is the source: its achieved rate is the offered load. The
                # ESP's own (live) figure is what came in over the wire, kept separately.
                stat1 = {**stat1, "esp_ingress_bps": stat1.get("achieved_bps"),
                         "achieved_bps": h7_sum.get("achieved_bps")}
            metrics_d = metrics.as_dict()
            # The device's low-water mark since boot (not the free heap after the run,
            # which had recovered by then and hid a 4 KB near-exhaustion).
            lows = [s.get(k) for s in (stat0, live, stat1, *samples)
                    for k in ("heap_min", "heap_win_min") if s.get(k) is not None]
            heap_min = min(lows) if lows else None
            post = gates.post_run({
                "commanded_bps": r.offered_bps,
                "achieved_bps": stat1.get("achieved_bps"),
                "esp_reset_during": False,
                "heap_min": heap_min, "heap_floor": HEAP_FLOOR_BYTES,
                "metrics": metrics_d, "rig_ceiling_mbps": ceiling,
                "host_udp_drops": host_drops,
            })
            if wired:
                ing = gates.ingress({"h7": h7_sum, "esp": esp_ing, "h7_error": h7_error,
                                     "clk_applied_ppm": clk_applied,
                                     "clk_measured_ppm": clk_measured, "hold_s": hold})
                post = gates.GateResult(post.ok and ing.ok, post.failures + ing.failures,
                                        post.warnings + ing.warnings)

        valid = pre.ok and post.ok
        rec = {
            "run_id": new_run_id(r.cell_id, r.offered_bps, r.repeat),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_sha": git_sha("."),
            "target": r.chip, "variant": r.tune, "source": r.source,
            "link": {"synth": "none", "qspi": "qspi", "sdio": "sdio4"}.get(r.source, r.source),
            "transport": r.transport, "offered_bps": r.offered_bps,
            "rate_hz": None, "duration_s": metrics_d.get("duration_s", 0.0),
            "cell_id": r.cell_id, "repeat": r.repeat,
            "rung": hello.get("rung"), "fw_sha": hello.get("fw_sha"),
            "parity": {"ok": self.parity_ok},
            "metrics": {**metrics_d,
                        "heap_min": heap_min, "heap_free": stat1.get("heap"),
                        "idle_pct": stat1.get("idle_pct", stat0.get("idle_pct")),
                        "retries": stat1.get("retries"),
                        "achieved_bps": stat1.get("achieved_bps"),
                        "host_udp_drops": host_drops,
                        **({"esp_ingress_bps": stat1.get("esp_ingress_bps"),
                            "link_bps": h7_sum.get("link_bps")} if wired else {})},
            # Stage 2 only: both ends' link counters, verbatim, plus the settings used.
            "ingress": ({"h7": h7_sum, "esp": esp_ing, "opts": self.link_opts,
                         "h7_error": h7_error,
                         "clk_applied_ppm": clk_applied,
                         "clk_measured_ppm": clk_measured} if wired else None),
            "ingress_limited": gates.ingress_limited(h7_sum) if wired else False,
            "rf": rfmeta.collect(band=str(r.band), ap=self.m.ap,
                                 rig_ceiling_mbps=ceiling,
                                 device_stat={**stat0, **stat1},
                                 idf_version=hello.get("idf"),
                                 host_path_info=host_path),
            "valid": valid,
            # What the drift gate compared: the cell's reference RSSI and the reading
            # taken before this run. Kept so a run let through with only a warning can
            # still be filtered or stratified afterwards.
            "rssi_ref_dbm": first_rssi, "rssi_pre_dbm": stat0.get("rssi"),
            "reassociated": reassociated or recovered,
            "keepalive": ka.snapshot(),
            "source_limited": gates.source_limited({
                "commanded_bps": r.offered_bps,
                "achieved_bps": stat1.get("achieved_bps")}),
            "gate_failures": pre.failures + post.failures,
            "gate_warnings": pre.warnings + post.warnings,
        }
        if samples:
            rec["telemetry"] = summarize(samples)
        if stress_log:
            rec["stress"] = stress_log
        if opts.get("device_cfg"):
            rec["device_cfg"] = dict(opts["device_cfg"])
        if self.m.timeseries_dir and pre.ok:
            rec["timeseries"] = self._write_timeseries(rec["run_id"], metrics, samples,
                                                       stress_log)
        self.ledger.append(rec)
        self.on_event("run", run=r, valid=valid,
                      goodput_mbps=metrics_d.get("goodput_bps", 0) / 1e6,
                      loss=metrics_d.get("loss_pct"))
        if opts.get("reset_after_stall") and pre.ok and \
                metrics_d.get("goodput_bps", 0) < 0.5 * r.offered_bps:
            self._reset_after_stall(r.chip, c)
        return rec

    def _reset_after_stall(self, chip: str, c) -> None:
        """A stalled TCP run can leave the board half alive (it answers hello while its
        socket is stuck), and the next run would inherit that. Start the next run from a
        fresh board: a board that no longer answers goes through the recovery ladder (a
        lock-up), one that still answers is simply reset."""
        try:
            c.hello()
        except Exception:                                    # noqa: BLE001
            if hasattr(self.driver, "recover"):
                self.driver.recover(chip)
            return
        boot = getattr(self.driver, "boot_check", None)
        if boot is None:
            return
        log = getattr(self.driver, "_rlog", None)
        if log:
            log(chip, "answers, but its last run stalled: reset before the next run")
        try:
            boot(chip, secs=4)
        except Exception as e:                               # noqa: BLE001
            if log:
                log(chip, f"reset after a stall failed: {e}")


def _unused_marker():
    pass


class _Watchdog:
    """Long runs: a board that has neither delivered data nor answered the control plane
    for `after_s` is revived in the middle of the run instead of costing the rest of it.

    It goes through the driver's recovery ladder (rediscover, serial reset, port
    re-detection, reflash) and, once the board answers, starts the stream again for the
    time left; the receiver (reconnect) carries on counting. A board that still answers
    but sends nothing is left alone: that is a stalled stream, which is what the run is
    measuring, not a dead board. Every action goes into the run's events."""

    POLL_S = 5.0

    def __init__(self, rx, sampler, driver, chip, client, restart_cfg, hold_s, after_s,
                 on_event):
        self.rx, self.sampler, self.driver, self.chip = rx, sampler, driver, chip
        self.client, self.restart_cfg = client, restart_cfg
        self.hold_s, self.after_s = float(hold_s), float(after_s)
        self.on_event = on_event
        self._stop = threading.Event()
        self._t0 = time.perf_counter()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "_Watchdog":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        quiet_until = 0.0              # after an attempt, give the board a period to show
        while not self._stop.wait(self.POLL_S):
            if time.perf_counter() < quiet_until or self.rx.silence_s() < self.after_s or \
                    self.sampler.unanswered_s() < self.after_s:
                continue
            quiet_until = time.perf_counter() + self.after_s
            left = self.hold_s - (time.perf_counter() - self._t0)
            if left < 30:
                return
            self.rx.note_event("watchdog", action="recover",
                               silent_s=round(self.rx.silence_s(), 1))
            self.on_event("recovering", chip=self.chip, error="watchdog: no data and no "
                          "answer for %.0f s mid-run" % self.after_s)
            try:
                # Straight to a reset: 90 s with no data and no stat reply is a starved or
                # hung board, which a rediscover would "find" (it may still answer hello)
                # and leave stalled.
                try:
                    ok = bool(self.driver.recover(self.chip, force_reset=True))
                except TypeError:                            # a driver without the option
                    ok = bool(self.driver.recover(self.chip))
            except Exception as e:                           # noqa: BLE001
                ok = False
                self.rx.note_event("watchdog", action="recover_error", error=str(e))
            if self._stop.is_set():
                return
            if not ok:
                self.rx.note_event("watchdog", action="gave_up")
                continue
            left = self.hold_s - (time.perf_counter() - self._t0)
            try:
                self.sampler.client = _quick(self.client)   # the address may be new
                self.client.cfg(dur_s=int(max(5, left)) + 5, **self.restart_cfg)
                self.client.start()
                self.rx.note_event("watchdog", action="restarted",
                                   left_s=round(left, 1))
            except Exception as e:                           # noqa: BLE001
                self.rx.note_event("watchdog", action="restart_error", error=str(e))


def knee(records: list[dict], threshold_pct: float = LOSS_THRESHOLD_PCT) -> dict:
    """Highest offered load whose MEDIAN loss across repeats stays under the threshold,
    with the MEDIAN goodput there and its spread.

    Aggregating across repeats before applying the threshold is the whole point. Judging
    each record on its own lets a single lucky repeat — one run that happened to slip
    under the loss bar — set the knee for the entire cell, which biases every headline
    number upward and is invisible in the output. The plan calls for median + spread over
    >= 3 repeats (§3, §1.2 rule 5); this is where that gets enforced.

    Spread is the median absolute deviation, reported so a cell whose repeats disagree is
    visible as a wide number rather than a confident wrong one.
    """
    by_load: dict[int, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("valid", True):
            by_load[int(r["offered_bps"])].append(r)

    passing = []
    for load, recs in by_load.items():
        losses = [r["metrics"].get("loss_pct", 100.0) for r in recs]
        if statistics.median(losses) < threshold_pct:
            passing.append((load, recs))

    if not passing:
        return {"knee_offered_bps": None, "goodput_bps": None,
                "goodput_mad_bps": None, "repeats": 0, "n": 0}

    load, recs = max(passing, key=lambda t: t[0])
    goods = [r["metrics"].get("goodput_bps", 0.0) for r in recs]
    med = statistics.median(goods)
    mad = statistics.median([abs(g - med) for g in goods]) if len(goods) > 1 else 0.0
    return {"knee_offered_bps": load, "goodput_bps": med, "goodput_mad_bps": mad,
            "repeats": len(recs), "n": sum(len(rs) for _, rs in passing)}


def _aslist(v):
    return v if isinstance(v, list) else [v]


def _quick(c):
    """A short-timeout second client for the sampler (fakes in tests may lack quick())."""
    q = getattr(c, "quick", None)
    return q() if q else c


def _addr_of(c) -> str | None:
    addr = getattr(c, "addr", None)
    return addr[0] if addr else None
