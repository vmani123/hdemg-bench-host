"""The follow-up campaign (docs/CAMPAIGN.md): every test, in order, unattended, resumable.

One process walks the plan below. Each test is measured through the normal machinery —
Matrix + Orchestrator, so every gate, resume, keepalive and recovery of a sweep applies —
and leaves a result in state.json and a section in summary.md. A test that fails is
retried once and then recorded as failed; the campaign goes on with the next test, and
any test that needed it is skipped with the reason. Only things a person must fix stop
it: hostrun.sh not answering, the Mac unable to join a network, a board locked by
another session.

Later tests depend on earlier results, and the rules that pick are written down here,
before any of those results exist:

  * kept PSRAM setup (group 2) — the variant that frees the most internal RAM (> 4 KB more
    low-water than the design it modifies) without losing more than 2 % beyond the
    combined repeat spread on either transport. None qualifies: the design stays.
  * best setup per chip (groups 4-5, used by Stage 2) — the highest UDP median among the
    chip's 20 MHz / 40 MHz (and for the C5, 5 GHz) builds. A 40 MHz build counts only
    if its link read "bw40 sta40". A tie (within the spread) goes to the plain design.
"""
from __future__ import annotations
import json
import random
import re
import socket
import statistics
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import gates, linktest, macnet, stress as stress_mod, wiring
from .control import ControlError
from .ledger import Ledger
from .orchestrator import Matrix, Orchestrator

HD_EMG, T1, T2 = 4.32, 20.5, 41.3          # Mbit/s: the operating point and two tiers
SAT20, SAT40 = 60.0, 120.0                 # saturating offered loads at 20 / 40 MHz
SWEEP = [10, 25.7, 41.4, 57.1, 72.9, 88.6, 104.3, 120]   # 8 loads, 10-120 Mbit/s
SHORT = {"esp32s3": "s3", "esp32c5": "c5"}
# Protocol v2 runtime settings stay on a board until set again, so every campaign run
# states all of them: a Nagle-on test must not leak into the soak that follows it.
DEVICE_DEFAULTS = {"nodelay": 1, "wr_max": 0, "dgram": 0, "batch": 0}
CHIPS = ("esp32s3", "esp32c5")


class CampaignStop(RuntimeError):
    """Something only a person can fix; the campaign stops and says what."""


@dataclass
class Test:
    id: str
    group: str
    title: str
    kind: str
    band: str | None = "2.4"     # band measured (sets the Mac's network); None = no radio
    est_min: float = 1.0
    params: dict = field(default_factory=dict)
    needs: tuple = ()             # tests whose success this one depends on


def plan() -> list[Test]:
    T = Test
    return [
        # ---- block A: checks, then everything short ---------------------------------
        T("W.1", "0", "Wiring hello world: all 18 Stage 2 wires, both directions", "wiring",
          None, 8),
        T("0.1", "0", "Rig check (2.4 GHz block)", "rig", "2.4", 2),
        T("0.2", "0", "Both ESPs boot and can be flashed with the Stage 2 harness on",
          "boot", None, 5),
        T("0.3", "0", "Reference: each chip's design, TCP + UDP, 2 x 20 s, saturating",
          "reference", "2.4", 10),
        T("1.1", "1", "TCP: Nagle off vs on, today's writes (8,100 B)", "knobs", "2.4", 6,
          {"transport": "tcp", "variants": {"nagle-off": {"nodelay": 1},
                                            "nagle-on": {"nodelay": 0}}}),
        T("1.2", "1", "TCP: 8,100 B vs 540 B writes, Nagle off", "knobs", "2.4", 3,
          {"transport": "tcp", "variants": {"wr-540": {"nodelay": 1, "wr_max": 540}},
           "reuse": {"wr-8100": ["1.1", "nagle-off"]}}),
        T("1.3", "1", "UDP: 1,350 B vs 540 B datagrams", "knobs", "2.4", 6,
          {"transport": "udp", "variants": {"dg-1350": {}, "dg-540": {"dgram": 540}}}),
        T("2.1", "2", "C5 P0: PSRAM on, nothing moved into it (control)", "variant", "2.4",
          4, {"chip": "esp32c5", "variant": "p0"}),
        T("2.2", "2", "C5 P1: buffer pool in PSRAM", "variant", "2.4", 4,
          {"chip": "esp32c5", "variant": "p1"}),
        T("2.3", "2", "C5 P2: Wi-Fi and lwIP buffers in PSRAM", "variant", "2.4", 4,
          {"chip": "esp32c5", "variant": "p2"}),
        T("2.4", "2", "C5 P3: P2 plus A-MSDU", "variant", "2.4", 4,
          {"chip": "esp32c5", "variant": "p3"}),
        T("2.5", "2", "S3 P1: buffer pool in PSRAM", "variant", "2.4", 4,
          {"chip": "esp32s3", "variant": "p1"}),
        T("2.6", "2", "S3 P2: Wi-Fi and lwIP buffers in PSRAM, no A-MSDU", "variant", "2.4",
          4, {"chip": "esp32s3", "variant": "p2"}),
        T("2.7", "2", "S3 P3: P2 plus A-MSDU (A-MSDU alone cannot be built)", "variant",
          "2.4", 4, {"chip": "esp32s3", "variant": "p3"}),
        T("2.8", "2", "10-minute memory check of each chip's kept PSRAM setup, UDP 41.3",
          "memcheck", "2.4", 22),
        T("4.1", "4", "S3: 20 vs 40 MHz, TCP + UDP, 3 x 60 s at 120 offered", "width",
          "2.4", 18, {"chip": "esp32s3"}),
        T("4.2", "4", "C5: Wi-Fi 6 at 20 MHz vs 802.11n at 40 MHz", "width", "2.4", 18,
          {"chip": "esp32c5"}),
        T("4.3", "4", "UDP load sweep, each chip's 40 MHz build, 10-120 Mbit/s", "sweep40",
          "2.4", 20, needs=("4.1", "4.2")),
        T("0.4", "0", "Mac to the 2.4 GHz network; its link (the second hop)", "maclink",
          "5", 2),
        T("5.2", "5", "C5 at 5 GHz: Wi-Fi 6 at 20 MHz, TCP + UDP, 3 x 60 s", "five", "5", 9,
          {"variant": "base"}),
        T("5.3", "5", "C5 at 5 GHz: 802.11n at 40 MHz", "five", "5", 9, {"variant": "ht40"}),
        T("5.4", "5", "C5 at 5 GHz: Wi-Fi 6 at 20 MHz with MCS 8-9", "five", "5", 9,
          {"variant": "mcs9"}),
        T("5.5", "5", "C5 at 5 GHz: UDP load sweep of the best of 5.2-5.4", "sweep5", "5", 10),
        T("5.6", "5", "C5 at 5 GHz with the Mac also on 5 GHz (shared channel)", "shared5",
          "5", 9),
        # ---- block B: soak and stress ---------------------------------------------------
        T("3.0", "3", "Block B start: rig check and reference", "reference", "2.4", 12,
          {"rig": True}),
        T("3.1", "3", "Soak: UDP at 4.32 Mbit/s, 30 min, both boards at once", "soak",
          "2.4", 32, {"transport": "udp", "mbps": HD_EMG, "parallel": True}),
        T("3.2", "3", "Soak: TCP at 4.32 Mbit/s, 30 min, both boards at once", "soak",
          "2.4", 32, {"transport": "tcp", "mbps": HD_EMG, "parallel": True}),
        T("3.3", "3", "Soak: UDP at 41.3 Mbit/s, 30 min, S3 then C5", "soak", "2.4", 62,
          {"transport": "udp", "mbps": T2}),
        T("3.4", "3", "Soak: TCP offered 41.3 Mbit/s (constant back-pressure), 30 min, S3 "
          "then C5", "soak", "2.4", 62, {"transport": "tcp", "mbps": T2}),
        T("3.5", "3", "Stress: TCP at 4.32, 30 min, both boards — read pauses, firewall "
          "block, reset, link drop", "stress", "2.4", 32, {"transport": "tcp"}),
        T("3.6", "3", "Stress: UDP at 4.32, 30 min, both boards — link drops 1/5/30 s",
          "stress", "2.4", 32, {"transport": "udp"}),
        T("3.7", "3", "Leak check: 500 start/stop cycles, both boards", "leak", "2.4", 25,
          {"cycles": 500}),
        # ---- block C: Stage 2 -----------------------------------------------------------
        T("6.0", "6", "Block C start: rig check and reference", "reference", "2.4", 12,
          {"rig": True}),
        T("6.1", "6", "STM32: build and flash both cores; hello; pacing with no link", "h7",
          None, 10, needs=("W.1",)),
        T("6.2", "6", "Link up: one 20 s link-only run per arm at 41.3 Mbit/s", "linkup",
          None, 15, needs=("W.1", "6.1")),
        T("6.3", "6", "S3: built-in generator vs QSPI input (best setup)", "wired", "best",
          20, {"chip": "esp32s3"}, needs=("6.2",)),
        T("6.4", "6", "C5: built-in generator vs QSPI vs SDIO input (best setup)", "wired",
          "best", 30, {"chip": "esp32c5"}, needs=("6.2",)),
        T("6.5", "6", "C5 QSPI in 15-frame batches (SDIO's limit), UDP at 41.3", "batch15",
          "best", 2, {"chip": "esp32c5"}, needs=("6.2",)),
    ]


# ---- the Verizon TCP plan (19 Oct; docs/REPORT_4_Verizon_TCP.md) -----------------------
# The router changed and Cisco Secure Client drops inbound UDP, so this plan is TCP only,
# at 2.4 GHz (the Mac holds the router's 5 GHz radio), and repeats nothing Report 3
# already has a result for. Its rule, written before any result exists:
#   * V.2's load: the stall-zone load at which the C5's default arm delivered the
#     smallest median share of the offered rate in the V.1 rounds done so far; 20.5 if
#     every load delivered at least 95 % of it.
VZONE = [10, 15, 20.5, 25, 30]          # Mbit/s: the TCP stall zone (Report 3 §9 c)
BATCH = 30                              # frames per send with batch on (one 8,100 B buffer)


def plan_verizon() -> list[Test]:
    T = Test
    zone = "TCP at 10-30 Mbit/s, default vs batch=30, shuffled"
    return [
        T("V.0", "V", "Setup: both boards on the router, designs at 2.4 GHz; link check",
          "vsetup", "2.4", 8),
        T("V.1a", "V", f"{zone}, round 1 (2 repeats)", "vzone", "2.4", 24,
          {"repeats": 2, "seed": 1}),
        T("V.2", "V", "15 min TCP at the C5's worst stall-zone load: C5, then S3",
          "vlong", "2.4", 33),
        T("V.3", "V", "HD-EMG soak: TCP at 4.32 Mbit/s, 30 min, both boards at once",
          "vsoak", "2.4", 32, {"batch": 0}),
        T("V.1b", "V", f"{zone}, round 2", "vzone", "2.4", 12, {"repeats": 1, "seed": 2}),
        T("V.4", "V", "HD-EMG soak with batch=30: TCP at 4.32, 30 min, both boards",
          "vsoak", "2.4", 32, {"batch": BATCH}),
        T("V.5", "V", "C5 TCP at full load (60 offered), 20 min", "vfull", "2.4", 22),
        T("V.1c", "V", f"{zone}, round 3", "vzone", "2.4", 12, {"repeats": 1, "seed": 3}),
        T("V.6", "V", "Stress: TCP at 4.32, 30 min, both boards — read pauses, receiver "
          "resets, link drops", "stress", "2.4", 32, {"transport": "tcp"}),
        T("V.7", "V", "TCP writes: 8,100 vs 2,700 vs 1,350 B at full load", "knobs", "2.4",
          10, {"transport": "tcp", "variants": {"wr-8100": {}, "wr-2700": {"wr_max": 2700},
                                                "wr-1350": {"wr_max": 1350}}}),
        # Extra (19 Oct), in the time the S3 left free by browning out: V.2's counterpart
        # with batch on, at the load V.2 used — does batching hold for 15 min, not 20 s?
        T("V.2b", "V", "Extra: 15 min TCP with batch=30 at V.2's load, C5", "vlong", "2.4",
          17, {"batch": BATCH, "chips": ["esp32c5"]}),
    ]


PLANS = {"followup": plan, "verizon-tcp": plan_verizon}


def plan_for(cfg: dict) -> list[Test]:
    return PLANS[cfg.get("plan", "followup")]()


# ---- the Mac's network ---------------------------------------------------------------
class RealMac:
    def __init__(self, networks: dict, password: str | None, log=print):
        self.networks = {str(k): v for k, v in networks.items()}
        self.password = password
        self.log = log

    def ensure(self, measured_band: str, shared: bool = False) -> dict:
        """Put the Mac on the OTHER band from the one measured (or, shared=True, on the
        same band: the typical-home comparison of test 5.6)."""
        other = "5" if measured_band == "2.4" else "2.4"
        on_band = measured_band if shared else other
        # networks is keyed by the band MEASURED: the network that puts the Mac on band
        # X is the one used while the other band is measured.
        ssid = self.networks["2.4" if on_band == "5" else "5"]
        try:
            return macnet.switch(ssid, self.password, want_band=on_band, log=self.log)
        except macnet.MacNetError as e:
            cur = macnet.current()
            if str(cur.get("band")) == on_band and cur.get("ip"):
                self.log(f">> {e}; the Mac is already on {on_band} GHz via "
                         f"{cur.get('ssid')!r}, carrying on")
                return cur
            raise CampaignStop(str(e)) from None

    def current(self) -> dict:
        return macnet.current()


class FakeMac:
    def __init__(self):
        self.band = "5"

    def ensure(self, measured_band: str, shared: bool = False) -> dict:
        self.band = measured_band if shared else ("5" if measured_band == "2.4" else "2.4")
        return self.current()

    def current(self) -> dict:
        return {"ssid": f"sim-{self.band}", "band": self.band, "channel": None,
                "width_mhz": None, "phy": "sim", "ip": "127.0.0.1"}


# ---- the engine ---------------------------------------------------------------------
class Campaign:
    def __init__(self, cfg_path: str | Path, *, driver, host_dir: str | Path,
                 sim: bool = False, mac=None, log=print, agent_dir: str | Path | None = None,
                 parity_ok: bool = True):
        self.cfg_path = Path(cfg_path)
        self.cfg = yaml.safe_load(self.cfg_path.read_text())
        self.host = Path(host_dir)
        self.sim = sim
        sd = self.host / self.cfg["state_dir"]
        led = self.host / self.cfg["ledger"]
        if sim:
            sd = Path(str(sd) + "-sim")
            led = led.with_name(led.stem + "-sim.jsonl")
        self.state_dir, self.ledger_path = sd, led
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(led)
        self.driver = driver
        self.mac = mac or FakeMac()
        self.log = log
        self.agent_dir = Path(agent_dir) if agent_dir else None
        self.parity_ok = parity_ok
        self.tests = plan_for(self.cfg)
        # Boards the recovery ladder could not bring back, with when it last tried: they
        # sit out the tests that follow and get the ladder again after DOWN_RETRY_S.
        self.down: dict[str, float] = {}
        self.until: float | None = None          # wall clock after which nothing starts
        self.shared_band = False                 # the Mac could not get off the band
        if getattr(driver, "recovery_log", "absent") != "absent":
            driver.recovery_log = str(self.state_dir / "recovery.log")
        if isinstance(getattr(driver, "fallback_builds", None), dict):
            # What the ladder reflashes when this process has not flashed a board yet.
            driver.fallback_builds.update({chip: ("synth", self.design(chip), "2.4")
                                           for chip in CHIPS})
        self.by_id = {t.id: t for t in self.tests}
        self.state = self._load_state()
        # A board found down by an earlier invocation stays down (cheap checks only)
        # until it comes back or the next full ladder is due.
        self.down.update({c: time.monotonic()
                          for c in self.state["derived"].get("down") or [] if c in CHIPS})

    # -- state ---------------------------------------------------------------------------
    def _load_state(self) -> dict:
        p = self.state_dir / "state.json"
        st = json.loads(p.read_text()) if p.exists() else {}
        st.setdefault("campaign", self.cfg.get("campaign"))
        st.setdefault("tests", {})
        st.setdefault("derived", {})
        for t in self.tests:
            st["tests"].setdefault(t.id, {"status": "pending", "attempts": 0, "round": 1})
        return st

    def save(self) -> None:
        self.state["updated"] = _now()
        tmp = self.state_dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True, default=str))
        tmp.replace(self.state_dir / "state.json")
        (self.state_dir / "summary.md").write_text(render_summary(self))

    def st(self, tid: str) -> dict:
        return self.state["tests"][tid]

    def result(self, tid: str) -> dict:
        return self.st(tid).get("result") or {}

    def done(self, tid: str) -> bool:
        return self.st(tid).get("status") == "done"

    # -- running -------------------------------------------------------------------------
    def selected(self, only=None, start=None, stop=None) -> list[Test]:
        ids = [t.id for t in self.tests]
        lo = ids.index(start) if start else 0
        hi = ids.index(stop) + 1 if stop else len(ids)
        pick = self.tests[lo:hi]
        if only:
            want = set(only)
            pick = [t for t in self.tests if t.id in want]
        return pick

    def redo(self, tid: str) -> None:
        s = self.st(tid)
        s.update(status="pending", attempts=0, round=int(s.get("round", 1)) + 1)
        s.pop("result", None)
        s.pop("error", None)
        self.save()

    def run(self, only=None, start=None, stop=None, max_tests: int | None = None,
            until: float | None = None) -> int:
        """Run every selected test that is not done. Returns 0 when all selected are
        done, 1 if any failed or was skipped, 3 if stopped for the user. `until` (epoch
        seconds): no test starts that could not finish by then."""
        self.until = until
        self.state.setdefault("started", _now())
        todo = [t for t in self.selected(only, start, stop) if not self.done(t.id)]
        self.log(f"campaign {self.cfg.get('campaign')}: {len(todo)} test(s) to run, about "
                 f"{sum(t.est_min for t in todo):.0f} min" + (" (simulated)" if self.sim else ""))
        n = 0
        for t in todo:
            if max_tests is not None and n >= max_tests:
                break
            n += 1
            if until is not None and time.time() + t.est_min * 60 > until:
                self._finish(t, "skipped", error=f"out of time: ~{t.est_min:g} min would "
                                                f"end after {time.strftime('%H:%M', time.localtime(until))}")
                continue
            blocked = [d for d in t.needs if self.st(d).get("status") in ("failed", "skipped")]
            if blocked:
                self._finish(t, "skipped", error=f"needs {', '.join(blocked)}, which did not "
                                                f"pass")
                continue
            try:
                self._run_test(t)
            except CampaignStop as e:
                self.st(t.id)["status"] = "pending"
                self.state["stopped"] = {"at": _now(), "test": t.id, "reason": str(e)}
                self.save()
                self.log(f"\n!! campaign stopped at {t.id}: {e}")
                return 3
        self.state.pop("stopped", None)
        self.save()
        sel = self.selected(only, start, stop)
        bad = [t.id for t in sel if self.st(t.id).get("status") in ("failed", "skipped")]
        left = [t.id for t in sel if self.st(t.id).get("status") == "pending"]
        self.log(f"\ncampaign: {len(sel) - len(bad) - len(left)} done, "
                 f"{len(bad)} failed/skipped {bad or ''}, {len(left)} pending")
        return 0 if not bad and not left else 1

    def _run_test(self, t: Test) -> None:
        s = self.st(t.id)
        while True:
            s["attempts"] = int(s.get("attempts", 0)) + 1
            s.update(status="running", started=_now())
            self.save()
            self.log(f"\n=== {t.id}  {t.title}  (attempt {s['attempts']}, ~{t.est_min:g} min)")
            try:
                self._preflight(t)
                res = getattr(self, f"_t_{t.kind}")(t)
                ok = res.pop("_ok", True) if isinstance(res, dict) else True
                starved = _starved(res.get("scores")) if ok and isinstance(res, dict) else []
                if starved and self.cfg.get("stall_is_result"):
                    # A plan that measures stalls (Verizon TCP): a point that delivered
                    # nothing is the finding, and a dead board has had the recovery
                    # ladder already. Noted, not failed and re-run.
                    res["starved"] = starved
                    starved = []
                if starved:
                    ok = False
                    res["why"] = "delivered almost nothing: " + "; ".join(starved)
                self._finish(t, "done" if ok else "failed", result=res,
                             error=None if ok else res.get("why"))
                return
            except CampaignStop:
                raise
            except Exception as e:                            # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                s["error"] = err
                s["traceback"] = traceback.format_exc()[-3000:]
                self.log(f"!! {t.id} failed: {err}")
                if s["attempts"] >= 2:
                    self._finish(t, "failed", error=err)
                    return
                self.log("   retrying once")
                self._recover()

    def _finish(self, t: Test, status: str, result: dict | None = None,
                error: str | None = None) -> None:
        s = self.st(t.id)
        s.update(status=status, finished=_now())
        if result is not None:
            s["result"] = result
        if status == "done":
            # A pass after a failed attempt: the old error stays in the log, not here.
            s.pop("error", None)
            s.pop("traceback", None)
        if error:
            s["error"] = error
        if status == "done" and t.kind in ("width", "five"):
            self._update_best()
        self.save()
        self.log(f"=== {t.id} {status.upper()}" + (f": {error}" if error else ""))

    def _preflight(self, t: Test) -> None:
        if not self.sim and self.agent_dir is not None:
            hb = self.agent_dir / "heartbeat"
            if not hb.exists() or time.time() - hb.stat().st_mtime > 60:
                raise CampaignStop(f"hostrun.sh is not answering (no fresh {hb}): it is "
                                   f"stopped, or waiting at its flash-approval prompt")
        band = self._band_for(t)
        if band:
            self.mac_state = self._ensure_mac(band, shared=(t.kind == "shared5"))
            self.driver.request_band(band)
        # Not before the setup test: it is what puts the boards on this network.
        if self.cfg.get("health_check") and not self.sim and t.kind != "vsetup":
            self._health()

    MAC_PATIENCE_S = 600
    DOWN_RETRY_S = 5400          # a full ladder at most every 90 min

    def _ensure_mac(self, band: str, shared: bool) -> dict:
        """The Mac onto the band not measured. With `mac_lenient` (a one-name router,
        where the Mac cannot be told which band to use), a failure is retried for ten
        minutes and then accepted: the test runs with the shared band allowed, and says
        so in its result, instead of the night ending here."""
        self.shared_band = False
        if not self.cfg.get("mac_lenient"):
            return self.mac.ensure(band, shared=shared)
        t_end = time.monotonic() + self.MAC_PATIENCE_S
        while True:
            try:
                return self.mac.ensure(band, shared=shared)
            except CampaignStop as e:
                if time.monotonic() >= t_end:
                    self.log(f"  !! {e}; carrying on with the Mac on the measured band "
                             f"(runs carry the shared-band warning)")
                    self.shared_band = True
                    return self.mac.current()
                self.log(f"  !! {e}; trying again in 60 s")
                time.sleep(60)

    def _health(self) -> None:
        """Before each test: every board answers, or goes through the recovery ladder.
        A board the ladder cannot bring back sits this test out (the other carries on)
        and is tried again once DOWN_RETRY_S has passed."""
        for chip in CHIPS:
            last = self.down.get(chip)
            if last is not None and time.monotonic() - last < self.DOWN_RETRY_S:
                # Between full ladders, only the cheap check: has it come back on its own
                # (someone replugged it)? The ladder already said why it is down.
                try:
                    if chip in (self.driver.rediscover(chip) or {}):
                        self.driver.control(chip).hello()
                        self.log(f"  {chip} is back on the network")
                        self.down.pop(chip, None)
                except Exception:                             # noqa: BLE001
                    pass
                continue
            try:
                self.driver.control(chip).hello()
                if chip in self.down:
                    self.log(f"  {chip} answers again")
                self.down.pop(chip, None)
                continue
            except Exception:                                 # noqa: BLE001
                pass
            self.log(f"  !! {chip} is not answering before the test: recovery ladder")
            ok = False
            try:
                ok = bool(self.driver.recover(chip))
            except Exception as e:                            # noqa: BLE001
                self.log(f"  !! recovery of {chip} raised {type(e).__name__}: {e}")
            if ok:
                self.down.pop(chip, None)
            else:
                self.down[chip] = time.monotonic()
                self.log(f"  !! {chip} IS DOWN: it sits out the tests until it can be "
                         f"brought back (next try in {self.DOWN_RETRY_S // 60} min)")
        self.state["derived"]["down"] = sorted(self.down)
        self.save()

    def chips_up(self) -> list[str]:
        return [c for c in CHIPS if c not in self.down]

    def _recover(self) -> None:
        for chip in CHIPS:
            try:
                self.driver.control(chip).hello()
            except Exception:                                 # noqa: BLE001
                rec = getattr(self.driver, "recover", None)
                if rec:
                    try:
                        rec(chip)
                    except Exception:                         # noqa: BLE001
                        pass

    # -- holds (a simulated campaign is a rehearsal of the plumbing, not of the rig) ----
    def _h(self, seconds: float) -> float:
        return float(seconds) if not self.sim else max(1.0, round(seconds * 0.004, 1))

    def _discard(self) -> float:
        return 3.0 if not self.sim else 0.2

    # -- designs ---------------------------------------------------------------------------
    def design(self, chip: str) -> str:
        return self.cfg["designs"][chip]

    def variant(self, chip: str, key: str) -> str:
        return self.cfg["variants"][chip][key]

    def soak_design(self, chip: str) -> str:
        kept = (self.state["derived"].get("kept_psram") or {}).get(chip) or {}
        return kept.get("rung") or self.design(chip)

    def best(self, chip: str) -> dict:
        """{'rung', 'band', 'why'}: the chip's best wireless setup (module docstring)."""
        b = (self.state["derived"].get("best") or {}).get(chip)
        return b or {"rung": self.design(chip), "band": "2.4",
                     "why": "no width/band results yet: the design at 2.4 GHz, 20 MHz"}

    def _band_for(self, t: Test) -> str | None:
        if t.band == "best":
            return self.best(t.params["chip"])["band"]
        return t.band

    # -- measuring ---------------------------------------------------------------------
    def _measure(self, t: Test, cells: list[dict], *, hold_s: float, repeats: int,
                 parallel: bool = False, allow_shared_band: bool = False,
                 link_opts: dict | None = None, stage: int = 1, passes: int = 2,
                 shuffle_seed: int | None = None) -> list[dict]:
        allow_shared_band = allow_shared_band or self.shared_band
        cells = [c for c in cells if c["chip"] not in self.down]
        rnd = int(self.st(t.id).get("round", 1))
        tag0 = f"t{t.id}" + (f"r{rnd}" if rnd > 1 else "")
        cells = [dict(c) for c in cells]
        for c in cells:
            c["tag"] = f"{tag0}-{c.pop('label')}"
            c.setdefault("source", "synth")
            c["device_cfg"] = {**DEVICE_DEFAULTS, **(c.get("device_cfg") or {})}
        host_ssid = None if self.sim else (getattr(self, "mac_state", None) or {}).get("ssid")
        m = Matrix(stage=stage, repeats=repeats,
                   sweep={"start_mbps": 1, "stop_mbps": 1, "steps": 1,
                          "hold_s": self._h(hold_s), "discard_s": self._discard()},
                   cells=cells, ap=self.cfg["ap"], frame_bytes=self.cfg["frame_bytes"],
                   expected_idf=None if self.sim else self.cfg["expected_idf"],
                   ssid=None if self.sim else self.cfg["ssid"], host_ssid=host_ssid,
                   allow_shared_band=allow_shared_band,
                   timeseries_dir=str(self.state_dir / "ts" / t.id),
                   shuffle_seed=shuffle_seed)
        mdir = self.state_dir / "matrices"
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / f"{t.id}.yaml").write_text(yaml.safe_dump(
            {"stage": stage, "repeats": repeats, "sweep": m.sweep, "cells": cells,
             "ap": m.ap, "ssid": m.ssid, "host_ssid": host_ssid,
             "allow_shared_band": allow_shared_band, "link_opts": link_opts or {}},
            sort_keys=False))
        groups = ([[c for c in cells if c["chip"] == chip] for chip in CHIPS
                   if any(c["chip"] == chip for c in cells)] if parallel else [cells])
        if parallel and len(groups) > 1:
            # Flash first, one board at a time: hostrun runs one job at a time anyway, and
            # the two measuring threads then start together.
            for g in groups:
                c0 = g[0]
                for tune in c0["tune"]:
                    self.driver.ensure_flashed(c0["chip"], c0["source"], tune)
            threads = [threading.Thread(target=self._orchestrate,
                                        args=(m, g, allow_shared_band, link_opts, passes),
                                        daemon=True) for g in groups]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        else:
            self._orchestrate(m, cells, allow_shared_band, link_opts, passes)
        ids = set()
        for c in cells:
            for tr in c["transport"]:
                for tune in c["tune"]:
                    ids.add(f"s{stage}-{c['chip']}-{c['band']}-{c['source']}-{tr}-{tune}"
                            f"-{c['tag']}")
        return [r for r in self.ledger.read() if r.get("cell_id") in ids]

    def _orchestrate(self, m: Matrix, cells, allow_shared_band, link_opts, passes) -> None:
        sub = Matrix(**{**m.__dict__, "cells": cells})
        chip = cells[0]["chip"]
        port = int(self.cfg["recv_ports"][chip]) + (0 if not self.sim else 10000)
        orc = Orchestrator(sub, self.driver, self.ledger,
                           stress_pf=_NoPf() if self.sim else None,
                           rig_ceilings={str(k): float(v) for k, v in
                                         self.cfg["ceilings"].items()},
                           parity_ok=self.parity_ok, recv_port=port,
                           keepalive=not self.sim, recover=True, on_event=self._event,
                           allow_shared_band=allow_shared_band, rssi_drift_warn_only=True,
                           link_opts=link_opts,
                           deadline=None if self.until is None else
                           time.monotonic() + (self.until - time.time()))
        for p in range(max(1, passes)):
            res = orc.run_all()
            if res["remaining"] == 0 or (res["valid"] == 0 and p > 0):
                break

    def _event(self, kind, **kw) -> None:
        if kind == "run":
            r = kw["run"]
            loss = kw.get("loss")
            self.log(f"  {r.cell_id:<52} off={r.offered_bps / 1e6:6.1f} "
                     f"good={kw['goodput_mbps']:6.2f} "
                     f"loss={'%5.2f%%' % loss if loss is not None else '  -  '} "
                     f"rep{r.repeat}{'' if kw['valid'] else '  INVALID'}")
        elif kind == "run_error":
            self.log(f"  !! {kw['run'].cell_id}: {kw.get('error')}")
        elif kind in ("recovering", "keepalive_lost"):
            self.log(f"  !! {kind}: {kw}")

    # -- the tests ---------------------------------------------------------------------
    def _t_wiring(self, t: Test) -> dict:
        for chip in CHIPS:
            self.driver.request_band("2.4")
            self.driver.ensure_flashed(chip, "synth", self.design(chip))
        self.driver.ensure_h7()
        h7 = self.driver.h7()
        try:
            h7.stop()
        except Exception:                                     # noqa: BLE001
            pass
        esps = {chip: self.driver.control(chip) for chip in CHIPS}
        res = wiring.check(h7, esps)
        self.log(wiring.render(res))
        bad = [r for r in res["rows"] if r["verdict"] != "ok"]
        return {**res, "_ok": res["ok"],
                "why": None if res["ok"] else
                f"{len(bad)} wire(s) wrong: " + "; ".join(f"#{r['n']} {r['verdict']}"
                                                          for r in bad[:6])}

    def _t_rig(self, t: Test) -> dict:
        out: dict = {"mac": self.mac.current(), "boards": {}}
        redisc = getattr(self.driver, "rediscover", None)
        if redisc:
            redisc()
        for chip in self.chips_up():
            c = self.driver.control(chip)
            h, s = c.hello(), c.stat()
            out["boards"][chip] = {k: h.get(k) for k in ("rung", "ingress", "band", "idf",
                                                         "fw_sha")}
            out["boards"][chip].update({k: s.get(k) for k in ("rssi", "phy_rate", "heap",
                                                              "heap_min", "uptime_s")})
        rs = [b.get("rssi") for b in out["boards"].values() if b.get("rssi")]
        notes = []
        if len(rs) == 2 and abs(rs[0] - rs[1]) > 6:
            notes.append(f"the boards' signal differs by {abs(rs[0] - rs[1])} dB — move the "
                         f"stronger one back for a fair comparison")
        # No width note here: the boards run their 20 MHz designs, and "bw" in phy_rate is
        # the connection's width, which a 20 MHz station always reads as bw20 whatever the
        # access point offers (17 Oct: this note called a 40 MHz router 20 MHz). Whether
        # 40 MHz is in effect is read where it is tested, from the 40 MHz builds (4.x, 5.3).
        out["notes"] = notes
        for n in notes:
            self.log(f"  note: {n}")
        return out

    def _t_boot(self, t: Test) -> dict:
        out = {}
        for chip in CHIPS:
            fn = getattr(self.driver, "boot_check", None)
            out[chip] = fn(chip) if fn else {"ok": True, "mode": "simulated"}
            self.log(f"  {chip}: esptool={out[chip].get('esptool')} "
                     f"boot={out[chip].get('mode')} "
                     f"{'answers' if out[chip].get('hello') or self.sim else 'NOT answering'}")
        ok = all(v.get("ok") for v in out.values())
        return {**out, "_ok": ok,
                "why": None if ok else "a board did not boot normally with the harness on"}

    def _t_reference(self, t: Test) -> dict:
        out = {}
        if t.params.get("rig"):
            out["rig"] = self._t_rig(t)
        cells = [{"chip": chip, "band": "2.4", "transport": ["tcp", "udp"],
                  "tune": [self.design(chip)], "loads_mbps": [SAT20], "label": "ref",
                  "sample_s": 1.0} for chip in CHIPS]
        recs = self._measure(t, cells, hold_s=20, repeats=2)
        out["scores"] = score_table(recs)
        return out

    def _t_knobs(self, t: Test) -> dict:
        p = t.params
        cells = []
        for chip in self.chips_up():
            for label, dc in p["variants"].items():
                cells.append({"chip": chip, "band": "2.4", "transport": [p["transport"]],
                              "tune": [self.design(chip)], "loads_mbps": [SAT20],
                              "label": label, "device_cfg": dict(dc), "sample_s": 1.0,
                              **({"reset_after_stall": True} if
                                 self.cfg.get("stall_is_result") and not self.sim else {})})
        recs = self._measure(t, cells, hold_s=20, repeats=3)
        out = {"scores": score_table(recs)}
        for label, (tid, other) in (p.get("reuse") or {}).items():
            out.setdefault("reused", {})[label] = f"{other} from test {tid}"
        return out

    def _t_variant(self, t: Test) -> dict:
        chip, key = t.params["chip"], t.params["variant"]
        rung = self.variant(chip, key)
        recs = self._measure(t, [{"chip": chip, "band": "2.4", "transport": ["tcp", "udp"],
                                  "tune": [rung], "loads_mbps": [SAT20], "label": key,
                                  "sample_s": 1.0}], hold_s=20, repeats=2)
        out = {"rung": rung, "scores": score_table(recs)}
        if not any(gates.counts_as_valid(r) for r in recs):
            out.update(_ok=False, why="no valid run (build, flash or gates) — see the log")
        return out

    def kept_psram(self) -> dict:
        """Apply the kept-PSRAM rule (module docstring) to group 2's results."""
        ref = (self.result("0.3").get("scores") or {})
        kept: dict = {}
        for chip in CHIPS:
            base = {tr: _pick(ref, chip, tr) for tr in ("tcp", "udp")}
            best, why = None, "no PSRAM variant qualified"
            cands = []
            for tid in ("2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7"):
                tt = self.by_id[tid]
                if tt.params["chip"] != chip or not self.done(tid):
                    continue
                sc = self.result(tid).get("scores") or {}
                v = {tr: _pick(sc, chip, tr) for tr in ("tcp", "udp")}
                if not all(v.values()) or not all(base.values()):
                    continue
                losses = []
                for tr in ("tcp", "udp"):
                    allow = 0.02 * base[tr]["mbps"] + base[tr]["spread"] + v[tr]["spread"]
                    if v[tr]["mbps"] < base[tr]["mbps"] - allow:
                        losses.append(f"{tr} {v[tr]['mbps']:.1f} vs {base[tr]['mbps']:.1f}")
                gain = (_low(v) or 0) - (_low(base) or 0)
                cands.append({"test": tid, "rung": self.result(tid).get("rung"),
                              "heap_gain_B": gain, "losses": losses})
            ok = [c for c in cands if not c["losses"] and c["heap_gain_B"] > 4096]
            if ok:
                best = max(ok, key=lambda c: c["heap_gain_B"])
                why = (f"{best['rung']} frees {best['heap_gain_B']} B of internal RAM "
                       f"without losing throughput")
            kept[chip] = {"rung": best["rung"] if best else None, "why": why,
                          "candidates": cands}
        self.state["derived"]["kept_psram"] = kept
        return kept

    def _t_memcheck(self, t: Test) -> dict:
        kept = self.kept_psram()
        out = {"kept": kept, "runs": {}}
        cells = [{"chip": chip, "band": "2.4", "transport": ["udp"], "tune": [k["rung"]],
                  "loads_mbps": [T2], "label": "mem10", "sample_s": 1.0,
                  "long_run": True, "window_s": 10.0}
                 for chip, k in kept.items() if k.get("rung")]
        if not cells:
            out["note"] = "no PSRAM setup was kept on either chip; nothing to check"
            return out
        recs = []
        for c in cells:                                    # one board at a time at T2
            recs += self._measure(t, [c], hold_s=600, repeats=1)
        out["scores"] = score_table(recs)
        out["telemetry"] = telemetry_table(recs)
        return out

    def _t_soak(self, t: Test) -> dict:
        p = t.params
        cells = [{"chip": chip, "band": "2.4", "transport": [p["transport"]],
                  "tune": [self.soak_design(chip)], "loads_mbps": [p["mbps"]],
                  "label": f"soak{p['transport']}", "sample_s": 1.0, "long_run": True,
                  "window_s": 10.0, "reconnect": p["transport"] == "tcp"} for chip in CHIPS]
        if p.get("parallel"):
            recs = self._measure(t, cells, hold_s=1800, repeats=1, parallel=True)
        else:
            recs = []
            for c in cells:
                recs += self._measure(t, [c], hold_s=1800, repeats=1)
        return {"designs": {c: self.soak_design(c) for c in CHIPS},
                "scores": score_table(recs), "telemetry": telemetry_table(recs),
                "soak": soak_table(recs)}

    def _t_stress(self, t: Test) -> dict:
        tr = t.params["transport"]
        total = self._h(1800)
        sched = stress_mod.tcp_stress(total) if tr == "tcp" else stress_mod.udp_stress(total)
        if self.sim:
            for e in sched:
                e["secs"] = max(0.2, round(e["secs"] * 0.02, 2))
        cells = [{"chip": chip, "band": "2.4", "transport": [tr],
                  "tune": [self.soak_design(chip)], "loads_mbps": [HD_EMG],
                  "label": f"stress{tr}", "sample_s": 1.0, "long_run": True,
                  "window_s": 10.0, "stress": sched, "reconnect": tr == "tcp",
                  "tcp_rcvbuf": 65536 if tr == "tcp" else None,
                  **self._dog()} for chip in self.chips_up()]
        for c in cells:
            if c["tcp_rcvbuf"] is None:
                c.pop("tcp_rcvbuf")
        recs = self._measure(t, cells, hold_s=1800, repeats=1, parallel=True)
        return {"designs": {c: self.soak_design(c) for c in CHIPS},
                "schedule": sched, "scores": score_table(recs),
                "telemetry": telemetry_table(recs), "soak": soak_table(recs),
                "events": {r["target"]: r.get("stress") for r in recs}}

    def _t_leak(self, t: Test) -> dict:
        cycles = int(t.params["cycles"]) if not self.sim else 12
        for chip in CHIPS:
            self.driver.ensure_flashed(chip, "synth", self.soak_design(chip))
        out: dict = {}
        threads = [threading.Thread(target=lambda ch=chip: out.__setitem__(
            ch, self._leak_one(ch, cycles)), daemon=True) for chip in CHIPS]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return out

    def _leak_one(self, chip: str, cycles: int) -> dict:
        port = int(self.cfg["recv_ports"][chip]) + (0 if not self.sim else 10000)
        sink = _Sink(port)
        c = self.driver.control(chip)
        host = self.driver.host_ip()
        rows, errors, run_of_errors, wedges = [], 0, 0, []
        on_s = 1.0 if not self.sim else 0.15
        try:
            for i in range(cycles):
                tr = "tcp" if i % 2 else "udp"
                if run_of_errors >= 3:
                    # The board stopped answering (17 Oct: both did, after 31-43 cycles,
                    # and stayed away). Record where, get it back (a reset, if need be),
                    # and carry on: how often it wedges is the finding.
                    wedges.append(i)
                    rows.append({"i": i, "recovered": bool(self.driver.recover(chip))})
                    c = self.driver.control(chip)
                    run_of_errors = 0
                try:
                    c.cfg(rate_bps=int(HD_EMG * 1e6), transport=tr, dst=f"{host}:{port}",
                          dur_s=5, frame_bytes=self.cfg["frame_bytes"], payload="rand",
                          label=f"leak{i}")
                    c.start()
                    time.sleep(on_s)
                    s = c.stop().get("summary", {}) or {}
                    rows.append({"i": i, "transport": tr, **{k: s.get(k) for k in (
                        "heap", "heap_min", "heap_largest", "uptime_s", "tcp_opens",
                        "send_errs", "wifi_disc")}})
                    run_of_errors = 0
                except (ControlError, OSError) as e:
                    errors += 1
                    run_of_errors += 1
                    rows.append({"i": i, "error": str(e)})
                    time.sleep(2.0)
                    if errors > 100:
                        break
        finally:
            sink.stop()
        good = [r for r in rows if r.get("heap") is not None]
        half = good[len(good) // 2:]
        slope = _slope([(r["i"], r["heap"]) for r in half])
        resets = sum(1 for a, b in zip(good, good[1:])
                     if (b.get("uptime_s") or 0) < (a.get("uptime_s") or 0))
        path = self.state_dir / "ts" / "3.7" / f"{chip}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
        return {"cycles": sum(1 for r in rows if "recovered" not in r), "errors": errors,
                "resets": resets, "wedged_at": wedges,
                "heap_first": good[0]["heap"] if good else None,
                "heap_last": good[-1]["heap"] if good else None,
                "heap_low": min((r["heap_min"] for r in good if r.get("heap_min") is not None),
                                default=None),
                "heap_slope_B_per_100_cycles": round(slope * 100, 1) if slope is not None
                else None, "series": str(path)}

    def _t_width(self, t: Test) -> dict:
        chip = t.params["chip"]
        cells = [{"chip": chip, "band": "2.4", "transport": ["tcp", "udp"],
                  "tune": [rung], "loads_mbps": [SAT40], "label": label, "sample_s": 1.0}
                 for label, rung in (("20mhz", self.design(chip)),
                                     ("40mhz", self.variant(chip, "ht40")))]
        recs = self._measure(t, cells, hold_s=60, repeats=3)
        sc = score_table(recs)
        return {"scores": sc, "in_effect_40": _in_effect(sc, chip, "40mhz")}

    def _t_sweep40(self, t: Test) -> dict:
        out = {}
        for chip, tid in (("esp32s3", "4.1"), ("esp32c5", "4.2")):
            if not self.result(tid).get("in_effect_40"):
                out[chip] = "skipped: 40 MHz was not in effect in " + tid
                continue
            recs = self._measure(t, [{"chip": chip, "band": "2.4", "transport": ["udp"],
                                      "tune": [self.variant(chip, "ht40")],
                                      "loads_mbps": SWEEP, "label": "sweep"}],
                                 hold_s=30, repeats=2)
            out[chip] = score_table(recs)
        return {"sweeps": out}

    def _t_maclink(self, t: Test) -> dict:
        cur = self.mac.current()
        self.log(f"  Mac: {cur.get('ssid')!r} {cur.get('band')} GHz ch{cur.get('channel')} "
                 f"{cur.get('width_mhz')} MHz {cur.get('phy')} tx {cur.get('tx_rate_mbps')}")
        return {"mac": cur}

    def _t_five(self, t: Test) -> dict:
        key = t.params["variant"]
        rung = self.design("esp32c5") if key == "base" else self.variant("esp32c5", key)
        recs = self._measure(t, [{"chip": "esp32c5", "band": "5", "transport": ["tcp", "udp"],
                                  "tune": [rung], "loads_mbps": [SAT40], "label": key,
                                  "sample_s": 1.0}], hold_s=60, repeats=3)
        sc = score_table(recs)
        out = {"rung": rung, "scores": sc}
        if key == "ht40":
            out["in_effect_40"] = _in_effect(sc, "esp32c5", key)
        return out

    def _best5(self) -> tuple[str, str] | None:
        best = None
        for tid in ("5.2", "5.3", "5.4"):
            r = self.result(tid)
            if not r or (tid == "5.3" and not r.get("in_effect_40")):
                continue
            u = _pick(r.get("scores") or {}, "esp32c5", "udp")
            if u and (best is None or u["mbps"] > best[2]):
                best = (tid, r["rung"], u["mbps"])
        return (best[0], best[1]) if best else None

    def _t_sweep5(self, t: Test) -> dict:
        b = self._best5()
        if not b:
            return {"_ok": False, "why": "no valid 5 GHz result to sweep"}
        recs = self._measure(t, [{"chip": "esp32c5", "band": "5", "transport": ["udp"],
                                  "tune": [b[1]], "loads_mbps": SWEEP, "label": "sweep"}],
                             hold_s=30, repeats=2)
        return {"best_of": b[0], "rung": b[1], "scores": score_table(recs)}

    def _t_shared5(self, t: Test) -> dict:
        b = self._best5()
        if not b:
            return {"_ok": False, "why": "no valid 5 GHz result"}
        recs = self._measure(t, [{"chip": "esp32c5", "band": "5", "transport": ["tcp", "udp"],
                                  "tune": [b[1]], "loads_mbps": [SAT40], "label": "shared",
                                  "sample_s": 1.0}], hold_s=60, repeats=3,
                             allow_shared_band=True)
        return {"rung": b[1], "mac": self.mac.current(), "scores": score_table(recs)}

    def _update_best(self) -> None:
        """Re-derive each chip's best wireless setup from what is measured so far."""
        cands: dict = {c: [] for c in CHIPS}
        for chip, tid in (("esp32s3", "4.1"), ("esp32c5", "4.2")):
            r = self.result(tid)
            if not r and self.result("0.3"):
                # The width test was not run (no 40 MHz on this router): the design's
                # 2.4 GHz figure comes from the reference instead.
                u = _pick(self.result("0.3").get("scores") or {}, chip, "udp", label="ref")
                if u:
                    cands[chip].append({"rung": self.design(chip), "band": "2.4",
                                        "udp": u["mbps"], "spread": u["spread"],
                                        "from": "0.3", "plain": True})
                continue
            for label, rung in (("20mhz", self.design(chip)),
                                ("40mhz", self.variant(chip, "ht40"))):
                if label == "40mhz" and not r.get("in_effect_40"):
                    continue
                u = _pick(r.get("scores") or {}, chip, "udp", label=label)
                if u:
                    cands[chip].append({"rung": rung, "band": "2.4", "udp": u["mbps"],
                                        "spread": u["spread"], "from": tid,
                                        "plain": label == "20mhz"})
        for tid in ("5.2", "5.3", "5.4"):
            r = self.result(tid)
            if not r or (tid == "5.3" and not r.get("in_effect_40")):
                continue
            u = _pick(r.get("scores") or {}, "esp32c5", "udp")
            if u:
                cands["esp32c5"].append({"rung": r["rung"], "band": "5", "udp": u["mbps"],
                                         "spread": u["spread"], "from": tid, "plain": False})
        best = {}
        for chip, cs in cands.items():
            if not cs:
                continue
            top = max(cs, key=lambda c: c["udp"])
            plain = next((c for c in cs if c["plain"]), None)
            if plain and top is not plain and top["udp"] - plain["udp"] <= \
                    top["spread"] + plain["spread"]:
                top = plain                                   # a tie goes to the design
            best[chip] = {"rung": top["rung"], "band": top["band"],
                          "why": f"UDP {top['udp']:.1f} Mbit/s ({top['from']}), best of "
                                 + ", ".join(f"{c['rung']}@{c['band']}={c['udp']:.1f}"
                                             for c in cs)}
        self.state["derived"]["best"] = best

    # -- the Verizon TCP plan ------------------------------------------------------------
    def _dog(self) -> dict:
        """Long-run cells get the mid-run watchdog when the rig config asks for it."""
        w = self.cfg.get("watchdog_s")
        return {"watchdog_s": float(w)} if w and not self.sim else {}

    def _long_cell(self, chip: str, mbps: float, label: str, dc: dict | None = None) -> dict:
        return {"chip": chip, "band": "2.4", "transport": ["tcp"],
                "tune": [self.design(chip)], "loads_mbps": [mbps], "label": label,
                "device_cfg": dict(dc or {}), "sample_s": 1.0, "long_run": True,
                "window_s": 10.0, "reconnect": True, **self._dog()}

    def _t_vsetup(self, t: Test) -> dict:
        out: dict = {"flash": {}}
        for chip in CHIPS:
            self.driver.request_band("2.4")
            try:
                out["flash"][chip] = self.driver.ensure_flashed(chip, "synth",
                                                                self.design(chip))
                self.down.pop(chip, None)
            except Exception as e:                            # noqa: BLE001
                self.log(f"  !! {chip} could not be flashed: {e}; recovery ladder")
                if self.driver.recover(chip) if hasattr(self.driver, "recover") else False:
                    out["flash"][chip] = self.driver.ensure_flashed(chip, "synth",
                                                                    self.design(chip))
                    self.down.pop(chip, None)
                else:
                    self.down[chip] = time.monotonic()
                    out["flash"][chip] = {"error": str(e)}
        self.state["derived"]["down"] = sorted(self.down)
        out.update(self._t_rig(t))
        out["mac_shared_band"] = self.shared_band
        missing = []
        for chip in self.chips_up():
            st = self.driver.control(chip).stat()
            out["boards"].setdefault(chip, {})["batch_knob"] = "batch" in st
            if "batch" not in st:
                missing.append(chip)
        ok = not missing and bool(self.chips_up())
        return {**out, "_ok": ok,
                "why": None if ok else f"no batch setting on {missing or 'any board'} "
                                       f"(old firmware?)"}

    def _t_vzone(self, t: Test) -> dict:
        p = t.params
        cells = [{"chip": chip, "band": "2.4", "transport": ["tcp"],
                  "tune": [self.design(chip)], "loads_mbps": VZONE, "label": label,
                  "device_cfg": dc, "sample_s": 1.0,
                  "reset_after_stall": bool(self.cfg.get("stall_is_result")) and not self.sim}
                 for chip in self.chips_up()
                 for label, dc in (("def", {}), (f"b{BATCH}", {"batch": BATCH}))]
        recs = self._measure(t, cells, hold_s=20, repeats=int(p["repeats"]),
                             shuffle_seed=int(p["seed"]))
        return {"scores": score_table(recs), "stalls": stall_table(recs),
                "skipped_chips": sorted(self.down), "mac_shared_band": self.shared_band}

    def worst_zone_load(self) -> tuple[float, dict]:
        """V.2's load (rule at the top of the Verizon plan)."""
        share: dict = {}
        for tid in ("V.1a", "V.1b", "V.1c"):
            sc = ((self.result(tid).get("scores") or {}).get("esp32c5") or {}).get("def") \
                or {}
            for off, s in (sc.get("tcp") or {}).items():
                if s.get("n"):
                    share.setdefault(float(off), []).append(s["mbps"] / float(off))
        med = {off: statistics.median(v) for off, v in share.items()}
        if not med or min(med.values()) >= 0.95:
            return T1, med
        return min(med, key=med.get), med

    def _t_vlong(self, t: Test) -> dict:
        b = int(t.params.get("batch") or 0)
        prev = (self.result("V.2") or {}).get("load_mbps") if b else None
        load, med = (float(prev), {}) if prev else self.worst_zone_load()
        self.log(f"  load {load} Mbit/s (C5 default share by load: "
                 f"{ {k: round(v, 3) for k, v in sorted(med.items())} })")
        recs = []
        for chip in t.params.get("chips") or ("esp32c5", "esp32s3"):
            if chip in self.down:
                continue
            recs += self._measure(t, [self._long_cell(chip, load, f"l15m-b{b}" if b else
                                                      "l15m", {"batch": b} if b else None)],
                                  hold_s=900, repeats=1)
        return {"load_mbps": load, "c5_share_by_load": med, "scores": score_table(recs),
                "telemetry": telemetry_table(recs), "soak": soak_table(recs),
                "skipped_chips": sorted(self.down)}

    def _t_vsoak(self, t: Test) -> dict:
        b = int(t.params.get("batch") or 0)
        dc = {"batch": b} if b else {}
        cells = [self._long_cell(chip, HD_EMG, f"soak-b{b}", dc) for chip in self.chips_up()]
        recs = self._measure(t, cells, hold_s=1800, repeats=1, parallel=True)
        return {"batch": b, "scores": score_table(recs), "telemetry": telemetry_table(recs),
                "soak": soak_table(recs), "skipped_chips": sorted(self.down)}

    def _t_vfull(self, t: Test) -> dict:
        if "esp32c5" in self.down:
            return {"_ok": False, "why": "the C5 is down"}
        recs = self._measure(t, [self._long_cell("esp32c5", SAT20, "full20m")], hold_s=1200,
                             repeats=1)
        return {"scores": score_table(recs), "telemetry": telemetry_table(recs),
                "soak": soak_table(recs)}

    # -- Stage 2 -----------------------------------------------------------------------
    def _t_h7(self, t: Test) -> dict:
        info = self.driver.ensure_h7()
        h7 = self.driver.h7()
        hello = h7.hello()
        hold = self._h(10)
        pace = []
        for mbps in (HD_EMG, T1, T2):
            h7.cfg(link="none", rate_bps=int(mbps * 1e6), dur_s=int(hold) + 3,
                   frame_bytes=self.cfg["frame_bytes"])
            h7.start()
            time.sleep(hold)
            s = h7.stop()
            ach = float(s.get("achieved_bps") or 0)
            err = abs(ach - mbps * 1e6) / (mbps * 1e6)
            pace.append({"mbps": mbps, "achieved_mbps": round(ach / 1e6, 3),
                         "error_pct": round(100 * err, 3), "ok": err <= 0.01})
            self.log(f"  pacing {mbps:5.2f} Mbit/s -> {ach / 1e6:7.3f}  "
                     f"{'ok' if err <= 0.01 else 'OFF'}")
        ok = all(p["ok"] for p in pace)
        return {"flash": info, "hello": hello, "pacing": pace, "_ok": ok,
                "why": None if ok else "the generator's pacing is off by more than 1 %"}

    def _arms(self) -> list[tuple[str, str]]:
        return [("esp32s3", "qspi"), ("esp32c5", "qspi"), ("esp32c5", "sdio")]

    def _t_linkup(self, t: Test) -> dict:
        out: dict = {}
        for chip, link in self._arms():
            b = self.best(chip)
            clocks = self.cfg["qspi_hz"] if link == "qspi" else self.cfg["sdio_hz"]
            key = "qspi_hz" if link == "qspi" else "sdio_hz"
            tries = []
            up = None
            for hz in clocks:
                opts = {"target": SHORT[chip], key: int(hz)}
                if self.cfg.get("link_slew") is not None:
                    opts["slew"] = int(self.cfg["link_slew"])
                port = int(self.cfg["recv_ports"][chip]) + (0 if not self.sim else 10000)
                r = linktest.run(self.driver, chip, link, [int(T2 * 1e6)],
                                 hold_s=self._h(20), tune=b["rung"], link_opts=opts,
                                 recv_port=port, band=b["band"], say=self.log)
                tries.append({"opts": opts, "passed": r["passed"],
                              "failures": [f for s in r["steps"] for f in s["failures"]],
                              "notes": [n for s in r["steps"] for n in s["notes"]]})
                if r["passed"]:
                    up = opts
                    break
            out[f"{chip}/{link}"] = {"up": up is not None, "opts": up, "tries": tries,
                                     "rung": b["rung"], "band": b["band"]}
        self.state["derived"]["links"] = out
        ok = any(v["up"] for v in out.values())
        return {"arms": out, "_ok": ok, "why": None if ok else "no link came up"}

    def _wired_cells(self, chip: str, sources: list[str], loads, transports) -> list[dict]:
        b = self.best(chip)
        return [{"chip": chip, "band": b["band"], "source": src, "transport": transports,
                 "tune": [b["rung"]], "loads_mbps": loads, "label": src} for src in sources]

    def _link_opts(self, chip: str) -> dict:
        opts = {"target": SHORT[chip]}
        for k, v in (self.state["derived"].get("links") or {}).items():
            if k.startswith(chip + "/") and v.get("up"):
                opts.update({kk: vv for kk, vv in v["opts"].items() if kk != "target"})
        return opts

    def _t_wired(self, t: Test) -> dict:
        chip = t.params["chip"]
        links = self.state["derived"].get("links") or {}
        up = [link for c, link in self._arms() if c == chip and
              links.get(f"{chip}/{link}", {}).get("up")]
        if not up:
            return {"_ok": False, "why": f"no wired link of {chip} came up in 6.2"}
        # One block per source: a source change is a reflash (~2 min), and interleaving
        # three sources over three repeats would cost nine of them. Synth comes first; the
        # cost of a wire is read against it from the same session.
        recs = []
        for src in ["synth", *up]:
            recs += self._measure(t, self._wired_cells(chip, [src], [HD_EMG, T1, T2],
                                                       ["udp", "tcp"]),
                                  hold_s=20, repeats=3, stage=2,
                                  link_opts=self._link_opts(chip))
        sc = score_table(recs)
        return {"best": self.best(chip), "links": up, "scores": sc,
                "ingress_cost": ingress_cost(sc, chip, up)}

    def _t_batch15(self, t: Test) -> dict:
        chip = t.params["chip"]
        if not (self.state["derived"].get("links") or {}).get(f"{chip}/qspi", {}).get("up"):
            return {"_ok": False, "why": "the C5 QSPI link did not come up in 6.2"}
        cells = self._wired_cells(chip, ["qspi"], [T2], ["udp"])
        cells[0]["label"] = "qspi-batch15"
        recs = self._measure(t, cells, hold_s=20, repeats=3, stage=2,
                             link_opts={**self._link_opts(chip), "batch": 15})
        return {"scores": score_table(recs)}


# ---- small helpers -------------------------------------------------------------------
class _NoPf:
    """A rehearsal never asks for sudo: the firewall block is recorded as skipped."""

    def available(self) -> bool:
        return False


class _Sink:
    """Accepts and drains whatever a board streams during the leak test (TCP and UDP on
    one port), so its sink has somewhere to connect and nothing is refused."""

    def __init__(self, port: int):
        self.stop_ev = threading.Event()
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind(("0.0.0.0", port))
        self.tcp.listen(4)
        self.tcp.settimeout(0.3)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.udp.bind(("0.0.0.0", port))
        self.udp.settimeout(0.3)
        self.threads = [threading.Thread(target=self._tcp, daemon=True),
                        threading.Thread(target=self._udp, daemon=True)]
        for th in self.threads:
            th.start()

    def _tcp(self):
        while not self.stop_ev.is_set():
            try:
                conn, _ = self.tcp.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._drain, args=(conn,), daemon=True).start()

    def _drain(self, conn):
        conn.settimeout(0.5)
        while not self.stop_ev.is_set():
            try:
                if not conn.recv(1 << 16):
                    break
            except socket.timeout:
                continue
            except OSError:
                break
        conn.close()

    def _udp(self):
        while not self.stop_ev.is_set():
            try:
                self.udp.recvfrom(65535)
            except (socket.timeout, OSError):
                continue

    def stop(self):
        self.stop_ev.set()
        for th in self.threads:
            th.join(timeout=2)
        self.tcp.close()
        self.udp.close()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")


def _slope(pts) -> float | None:
    if len(pts) < 5:
        return None
    mx = statistics.fmean(p[0] for p in pts)
    my = statistics.fmean(p[1] for p in pts)
    var = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / var if var else None


_TAG = re.compile(r"-t(?:\d+\.\d+|[A-Z]\.\d+[a-z]?)(?:r\d+)?-(.+)$")


def _label(rec: dict) -> str:
    """The cell tag minus the test prefix: 'nagle-on', '40mhz', 'qspi', ..."""
    m = _TAG.search(rec.get("cell_id", ""))
    return m.group(1) if m else rec.get("cell_id", "")


def score_table(recs: list[dict]) -> dict:
    """{chip: {label: {transport: {offered_mbps: score}}}} over VALID runs; the count of
    invalid runs rides along so nothing is silently dropped."""
    out: dict = {}
    groups: dict = {}
    for r in recs:
        key = (r["target"], _label(r), r["transport"], round(r["offered_bps"] / 1e6, 2))
        groups.setdefault(key, []).append(r)
    for (chip, label, tr, off), rs in sorted(groups.items()):
        good = [r for r in rs if gates.counts_as_valid(r)]
        sc = _score(good) if good else None
        if sc is None:
            sc = {"n": 0}
        sc["invalid"] = len(rs) - len(good)
        if sc["invalid"]:
            sc["why_invalid"] = sorted({f for r in rs if not gates.counts_as_valid(r)
                                        for f in r.get("gate_failures", [])})[:4]
        out.setdefault(chip, {}).setdefault(label, {}).setdefault(tr, {})[str(off)] = sc
    return out


def stall_table(recs: list[dict]) -> dict:
    """{chip/label: {offered: [runs that delivered < 50 % of the offered rate, runs]}}:
    how often each point stalled, which a median alone hides."""
    out: dict = {}
    for r in recs:
        if not gates.counts_as_valid(r):
            continue
        off = round(r["offered_bps"] / 1e6, 2)
        k = f"{r['target']}/{_label(r)}"
        row = out.setdefault(k, {}).setdefault(str(off), [0, 0])
        row[1] += 1
        if r["metrics"].get("goodput_bps", 0.0) < 0.5 * r["offered_bps"]:
            row[0] += 1
    return out


def _starved(scores: dict | None) -> list[str]:
    """Scores whose valid runs delivered under 2 % of the offered load. A board that has
    wedged can still pass every gate (the receiver saw a few frames, none were lost), so
    such a score is a failure, not a measurement."""
    out = []
    for chip, labs in (scores or {}).items():
        for lab, trs in labs.items():
            for tr, loads in trs.items():
                for off, sc in loads.items():
                    if sc.get("n") and sc.get("mbps", 0.0) < 0.02 * float(off):
                        out.append(f"{chip} {lab} {tr} at {off} Mbit/s gave "
                                   f"{sc['mbps']:.2f}")
    return out


def _score(recs: list[dict]) -> dict:
    goods = [r["metrics"].get("goodput_bps", 0.0) / 1e6 for r in recs]
    med = statistics.median(goods)
    m = [r["metrics"] for r in recs]
    phy = Counter(r.get("rf", {}).get("phy_rate") for r in recs).most_common(1)[0][0]
    rssi = [r["rf"].get("rssi_dbm") for r in recs if r.get("rf", {}).get("rssi_dbm")]
    lows = [x.get("heap_min") for x in m if x.get("heap_min") is not None]
    p50 = [x.get("latency_p50_us") for x in m if x.get("latency_p50_us") is not None]
    p99 = [x.get("latency_p99_us") for x in m if x.get("latency_p99_us") is not None]
    idle = [x.get("idle_pct") for x in m if x.get("idle_pct")]
    return {"mbps": round(med, 3), "spread": round((max(goods) - min(goods)) / 2, 3),
            "loss_pct": round(statistics.median([x.get("loss_pct", 0.0) for x in m]), 4),
            "n": len(recs), "phy_rate": phy,
            "rssi_dbm": statistics.median(rssi) if rssi else None,
            "heap_min": min(lows) if lows else None,
            "lat_p50_us": statistics.median(p50) if p50 else None,
            "lat_p99_us": statistics.median(p99) if p99 else None,
            "idle_pct": idle[0] if idle else None}


def _pick(scores: dict, chip: str, tr: str, label: str | None = None) -> dict | None:
    """The single-load score of one (chip, label, transport) — the first label if none."""
    per = scores.get(chip) or {}
    lab = per.get(label) if label else (next(iter(per.values())) if per else None)
    if not lab or tr not in lab:
        return None
    sc = next(iter(lab[tr].values()))
    return sc if sc.get("n") else None


def _low(v: dict) -> int | None:
    lows = [x["heap_min"] for x in v.values() if x and x.get("heap_min") is not None]
    return min(lows) if lows else None


def _in_effect(scores: dict, chip: str, label: str) -> bool:
    """A 40 MHz build counts only if its link read 'bw40 sta40'."""
    lab = (scores.get(chip) or {}).get(label) or {}
    phys = [sc.get("phy_rate") for tr in lab.values() for sc in tr.values() if sc.get("n")]
    return bool(phys) and all("bw40 sta40" in str(p) for p in phys)


def telemetry_table(recs: list[dict]) -> dict:
    return {f"{r['target']}/{r['transport']}/{_label(r)}": r.get("telemetry") for r in recs
            if r.get("telemetry")}


def soak_table(recs: list[dict]) -> dict:
    out = {}
    for r in recs:
        m = r["metrics"]
        out[f"{r['target']}/{r['transport']}/{_label(r)}"] = {
            "valid": gates.counts_as_valid(r), "goodput_mbps": round(m.get("goodput_bps", 0) / 1e6, 3),
            "loss_pct": m.get("loss_pct"), "lost_frames": m.get("lost_frames"),
            "duration_s": m.get("duration_s"), "longest_gap_s": m.get("longest_gap_s"),
            "lat_p50_us": m.get("latency_p50_us"), "lat_p99_us": m.get("latency_p99_us"),
            "lat_max_us": m.get("latency_max_us"), "clock_skew_ppm": m.get("clock_skew_ppm"),
            "connections": m.get("connections"), "timeseries": r.get("timeseries")}
    return out


def ingress_cost(scores: dict, chip: str, links: list[str]) -> dict:
    """synth - wired goodput, per transport and load: what the wire costs (plan §5.1)."""
    out: dict = {}
    per = scores.get(chip) or {}
    for link in links:
        for tr, loads in (per.get(link) or {}).items():
            for off, sc in loads.items():
                base = ((per.get("synth") or {}).get(tr) or {}).get(off)
                if base and base.get("n") and sc.get("n"):
                    out.setdefault(link, {}).setdefault(tr, {})[off] = round(
                        base["mbps"] - sc["mbps"], 3)
    return out


# ---- the summary ---------------------------------------------------------------------
def render_summary(c: Campaign) -> str:
    st = c.state
    lines = [f"# Campaign {st.get('campaign')}" + (" (SIMULATED)" if c.sim else ""), "",
             f"_updated {st.get('updated', '')}_", ""]
    if st.get("stopped"):
        s = st["stopped"]
        lines += [f"**STOPPED at {s['test']} ({s['at']}): {s['reason']}**", ""]
    left = sum(t.est_min for t in c.tests if c.st(t.id)["status"] in ("pending", "running"))
    counts = Counter(c.st(t.id)["status"] for t in c.tests)
    lines += [f"{counts.get('done', 0)} done · {counts.get('failed', 0)} failed · "
              f"{counts.get('skipped', 0)} skipped · {counts.get('pending', 0)} pending "
              f"(~{left:.0f} min left)", "",
              "| test | status | what | note |", "|---|---|---|---|"]
    for t in c.tests:
        s = c.st(t.id)
        note = (s.get("error") or "")[:110].replace("|", "/")
        lines.append(f"| {t.id} | {s['status']} | {t.title} | {note} |")
    d = st.get("derived", {})
    if d.get("best"):
        lines += ["", "## Best wireless setup per chip (used by Stage 2)", ""]
        for chip, b in d["best"].items():
            lines.append(f"- **{chip}**: `{b['rung']}` at {b['band']} GHz — {b['why']}")
    if d.get("kept_psram"):
        lines += ["", "## Kept PSRAM setup (used by the soak tests)", ""]
        for chip, k in d["kept_psram"].items():
            lines.append(f"- **{chip}**: {('`' + k['rung'] + '`') if k.get('rung') else 'none'}"
                         f" — {k['why']}")
    for t in c.tests:
        r = c.result(t.id)
        if not r:
            continue
        lines += ["", f"## {t.id} — {t.title}", ""]
        if t.kind == "wiring" and r.get("rows"):
            lines.append(wiring.render(r))
            continue
        for key in ("scores",):
            if r.get(key):
                lines += _render_scores(r[key])
        if r.get("sweeps"):
            for chip, sc in r["sweeps"].items():
                lines += [f"*{chip}*", ""] + (_render_scores(sc) if isinstance(sc, dict)
                                              else [str(sc)])
        for key in ("stalls", "c5_share_by_load", "soak", "telemetry", "ingress_cost",
                    "pacing", "arms", "kept"):
            if r.get(key):
                lines += ["", f"<details><summary>{key}</summary>", "", "```json",
                          json.dumps(r[key], indent=1, default=str)[:6000], "```",
                          "</details>"]
        for chip in CHIPS:
            if isinstance(r.get(chip), dict) and t.kind in ("boot", "leak"):
                v = {k: vv for k, vv in r[chip].items() if k != "log_tail"}
                lines.append(f"- **{chip}**: `{json.dumps(v, default=str)[:400]}`")
        if r.get("notes"):
            lines += [f"- note: {n}" for n in r["notes"]]
    return "\n".join(lines) + "\n"


def _render_scores(sc: dict) -> list[str]:
    out = ["| chip | variant | transport | offered | Mbit/s (±) | loss % | heap low B | "
           "p50/p99 µs | link | n |", "|---|---|---|---|---|---|---|---|---|---|"]
    for chip, labs in sc.items():
        for lab, trs in labs.items():
            for tr, loads in trs.items():
                for off, s in loads.items():
                    if not s.get("n"):
                        out.append(f"| {chip} | {lab} | {tr} | {off} | — | — | — | — | — | "
                                   f"0 ({s.get('invalid', 0)} invalid) |")
                        continue
                    lat = (f"{s['lat_p50_us']:.0f}/{s['lat_p99_us']:.0f}"
                           if s.get("lat_p50_us") is not None else "—")
                    out.append(f"| {chip} | {lab} | {tr} | {off} | {s['mbps']:.2f} "
                               f"(±{s['spread']:.2f}) | {s['loss_pct']:.3f} | "
                               f"{s.get('heap_min') or '—'} | {lat} | {s.get('phy_rate')} | "
                               f"{s['n']}" + (f" (+{s['invalid']} invalid)" if s.get('invalid')
                                              else "") + " |")
    return out


def default_seed() -> int:
    return random.randrange(1, 2 ** 31)
