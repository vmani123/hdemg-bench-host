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
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import gates, rfmeta
from .control import ControlError
from .keepalive import Keepalive, NullKeepalive
from .control import ControlClient
from .frame import HDR_BYTES
from .ledger import Ledger, git_sha, new_run_id
from .receiver import Receiver

DEFAULT_SWEEP = {"start_mbps": 1, "stop_mbps": 60, "steps": 8,
                 "hold_s": 60, "discard_s": 3}
LOSS_THRESHOLD_PCT = 0.1


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

    @classmethod
    def load(cls, path: str | Path) -> "Matrix":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls(stage=raw.get("stage", 0), repeats=raw.get("repeats", 3),
                   sweep={**DEFAULT_SWEEP, **(raw.get("sweep") or {})},
                   cells=raw.get("cells") or [], ap=raw.get("ap", "iphone-hotspot"),
                   frame_bytes=raw.get("frame_bytes", 270),
                   expected_idf=raw.get("expected_idf", "v6.0.2"))

    def offered_steps(self) -> list[int]:
        s, e, n = self.sweep["start_mbps"], self.sweep["stop_mbps"], self.sweep["steps"]
        if n <= 1:
            return [int(e * 1e6)]
        step = (e - s) / (n - 1)
        return [int((s + i * step) * 1e6) for i in range(n)]

    def expand(self, bands: list[str] | None = None,
               transports: list[str] | None = None) -> list[Run]:
        """bands: restrict to these bands. transports: restrict to these transports
        (the points left out stay pending for a later sweep).

        The hotspot serves one band at a time and the switch is a manual toggle, so a
        sweep restricted to one band is the unit that can run unattended end to end.
        Two such blocks with one toggle between them replaces six blocking prompts
        scattered through the night.
        """
        runs: list[Run] = []
        steps = self.offered_steps()
        for rep in range(1, self.repeats + 1):        # repeat is the OUTER loop
            for cell in self.cells:
                chip, band = cell["chip"], str(cell["band"])
                if bands and band not in bands:
                    continue
                source = cell.get("source", "synth")
                for transport in _aslist(cell.get("transport", ["udp"])):
                    if transports and transport not in transports:
                        continue
                    for tune in _aslist(cell.get("tune", ["tuned"])):
                        cid = f"s{self.stage}-{chip}-{band}-{source}-{transport}-{tune}"
                        for off in steps:
                            runs.append(Run(cid, chip, band, source, transport,
                                            tune, off, rep))
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
                 rssi_drift_warn_only: bool = False):
        self.m = matrix
        self.driver = driver
        self.ledger = ledger
        self.rig_ceilings = rig_ceilings or {}
        self.parity_ok = parity_ok
        # Downgrades "this host is on the same Wi-Fi band as the cell" from a failed
        # gate to a warning. Only for a rig where that is knowingly accepted.
        self.allow_shared_band = allow_shared_band
        self.recv_port = recv_port
        self.bands = [str(b) for b in bands] if bands else None
        self.transports = [str(t) for t in transports] if transports else None
        # Record an RSSI drift as a warning instead of refusing the run (see gates.py).
        self.rssi_drift_warn_only = rssi_drift_warn_only
        self.want_keepalive = keepalive
        self.want_recover = recover
        self.on_event = on_event or (lambda *_a, **_k: None)
        self._first_rssi: dict[str, float] = {}
        self._current_band: str | None = None
        self._keepalives: dict[str, object] = {}

    def pending(self) -> list[Run]:
        done = self.ledger.completed_keys()
        return [r for r in self.m.expand(self.bands, self.transports) if r.key not in done]

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

    def run_all(self, limit: int | None = None) -> dict:
        todo = self.pending()
        if limit:
            todo = todo[:limit]
        ok = invalid = errors = 0
        broken: dict[str, str] = {}     # cell_id -> why it was abandoned this pass
        try:
            for r in todo:
                if r.cell_id in broken:
                    continue
                if str(r.band) != str(self._current_band):
                    self.driver.request_band(str(r.band))
                    self._current_band = str(r.band)
                    self.on_event("band", band=r.band)
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
        finally:
            for ka in self._keepalives.values():
                ka.stop()
            self._keepalives.clear()    # a stopped keepalive never polls again
        return {"attempted": len(todo), "valid": ok, "invalid": invalid,
                "errors": errors, "abandoned_cells": broken,
                "remaining": len(self.pending())}

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
        self.driver.ensure_flashed(r.chip, r.source, r.tune)
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
        hold = float(self.m.sweep["hold_s"])
        # A run blocked by a pre-gate is still recorded, with zeroed metrics: the
        # ledger must show that the point was attempted and refused, or resume would
        # retry it forever and the report would silently under-count the matrix.
        metrics_d: dict = {"goodput_bps": 0.0, "frames": 0, "loss_pct": 0.0,
                           "duration_s": 0.0, "not_run": True}
        post = gates.GateResult(True, [], [])
        stat1: dict = {}

        host_drops: int | None = None      # datagrams THIS HOST discarded during the window

        if pre.ok:
          with ka.paused():        # the instrument must not appear in its own number
            # UDP only, and not on loopback (the fake has no real network path): what
            # the host itself has dropped so far, to compare after the window closes.
            drops0 = rfmeta.host_udp_drops() \
                if r.transport == "udp" and host_path.get("link") != "loopback" else None
            rx = Receiver(r.transport, self.recv_port, payload_bytes,
                          duration_s=hold, discard_s=float(self.m.sweep["discard_s"]))
            rx.start()
            c.cfg(rate_bps=r.offered_bps, transport=r.transport,
                  dst=f"{self.driver.host_ip()}:{self.recv_port}",
                  dur_s=int(hold) + 5, frame_bytes=self.m.frame_bytes,
                  payload="rand", label=f"{r.cell_id}-r{r.repeat}")
            c.start()
            metrics = rx.join()
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
            try:
                stat1 = c.stop().get("summary", {}) or c.stat()
            except Exception:                                 # noqa: BLE001
                stat1 = {}
            for k in ("achieved_bps", "idle_pct"):
                if live.get(k) is not None:
                    stat1[k] = live[k]
            if r.cell_id not in self._first_rssi:
                settled = live.get("rssi") or stat1.get("rssi")
                if settled:                       # 0 means "not associated", not 0 dBm
                    self._first_rssi[r.cell_id] = settled
            metrics_d = metrics.as_dict()
            post = gates.post_run({
                "commanded_bps": r.offered_bps,
                "achieved_bps": stat1.get("achieved_bps"),
                "esp_reset_during": False,
                "heap_min": stat1.get("heap"), "heap_floor": 20000,
                "metrics": metrics_d, "rig_ceiling_mbps": ceiling,
                "host_udp_drops": host_drops,
            })

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
                        "idle_pct": stat1.get("idle_pct", stat0.get("idle_pct")),
                        "retries": stat1.get("retries"),
                        "achieved_bps": stat1.get("achieved_bps"),
                        "host_udp_drops": host_drops},
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
        self.ledger.append(rec)
        self.on_event("run", run=r, valid=valid,
                      goodput_mbps=metrics_d.get("goodput_bps", 0) / 1e6,
                      loss=metrics_d.get("loss_pct"))
        return rec


def _unused_marker():
    pass


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
