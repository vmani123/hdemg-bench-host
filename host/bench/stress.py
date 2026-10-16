"""Faults injected into a streaming run on a schedule, for the memory stress tests.

Each fault is one way the data stops getting through, applied while the board keeps
generating, so its memory has to absorb the backlog:

  pause <s>     this host stops reading (TCP: the receive window closes)
  restart       this host resets the TCP connection; the board must reconnect
  linkdrop <s>  the board's own Wi-Fi link goes down and comes back (both transports;
                the only one a UDP sender can feel)
  pfblock <s>   this host's firewall silently drops the board's packets: the link
                stays up but nothing is acknowledged, so TCP retransmits with
                back-off. Needs passwordless `sudo pfctl` — without it the event is
                recorded as skipped, never faked.

Times are seconds after the run's first frame. Every event records what actually
happened (time, outcome), and that list goes into the run's ledger record.
"""
from __future__ import annotations
import subprocess
import threading
import time
from dataclasses import dataclass

KINDS = ("pause", "restart", "linkdrop", "pfblock")
# The default /etc/pf.conf evaluates anchor "com.apple/*" and nothing else, so a rule
# only takes effect in a sub-anchor of it.
PF_ANCHOR = "com.apple/hdemg.bench"


@dataclass
class Event:
    at_s: float
    kind: str
    secs: float = 0.0

    @classmethod
    def parse(cls, d: dict) -> "Event":
        e = cls(float(d["at_s"]), str(d["kind"]), float(d.get("secs", 0.0)))
        if e.kind not in KINDS:
            raise ValueError(f"unknown stress event {e.kind!r} (one of {KINDS})")
        return e


def schedule(spec: list[dict] | None) -> list[Event]:
    return sorted((Event.parse(d) for d in (spec or [])), key=lambda e: e.at_s)


class PfBlock:
    """Drop one board's inbound packets with pf, for a fixed time, then remove the rule.
    Only through `sudo -n` (never a password prompt in an unattended run)."""

    def available(self) -> bool:
        try:
            r = subprocess.run(["sudo", "-n", "/sbin/pfctl", "-s", "info"],
                               capture_output=True, text=True, timeout=10)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def block(self, device_ip: str, secs: float) -> str:
        rule = f"block drop in quick proto tcp from {device_ip} to any\n"
        token = None
        try:
            en = subprocess.run(["sudo", "-n", "/sbin/pfctl", "-E"], capture_output=True,
                                text=True, timeout=10)
            for line in (en.stdout + en.stderr).splitlines():
                if "Token" in line:
                    token = line.split(":")[-1].strip()
            subprocess.run(["sudo", "-n", "/sbin/pfctl", "-a", PF_ANCHOR, "-f", "-"],
                           input=rule, capture_output=True, text=True, timeout=10,
                           check=True)
            time.sleep(secs)
            return "ok"
        finally:
            subprocess.run(["sudo", "-n", "/sbin/pfctl", "-a", PF_ANCHOR, "-F", "rules"],
                           capture_output=True, text=True, timeout=10)
            if token:
                subprocess.run(["sudo", "-n", "/sbin/pfctl", "-X", token],
                               capture_output=True, text=True, timeout=10)


class StressRunner:
    """Runs a schedule against one streaming run: `rx` is the Receiver, `control` the
    board's ControlClient, `device_ip` its address (for pf)."""

    def __init__(self, events: list[Event], rx, control, device_ip: str | None = None,
                 pf: PfBlock | None = None, wait_first_frame_s: float = 30.0):
        self.events = events
        self.rx = rx
        self.control = control
        self.device_ip = device_ip
        self.pf = pf if pf is not None else PfBlock()
        self.wait_first_frame_s = wait_first_frame_s
        self.log: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "StressRunner":
        if self.events:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> list[dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=120.0)
        return self.log

    def _run(self) -> None:
        end = time.monotonic() + self.wait_first_frame_s
        while self.rx.elapsed_s() is None:
            if self._stop.is_set() or time.monotonic() > end:
                self.log.append({"kind": "all", "outcome": "skipped: no frame arrived"})
                return
            time.sleep(0.05)
        pf_ok = None
        for ev in self.events:
            while not self._stop.is_set() and (self.rx.elapsed_s() or 0.0) < ev.at_s:
                time.sleep(0.05)
            if self._stop.is_set():
                self.log.append({"at_s": ev.at_s, "kind": ev.kind, "secs": ev.secs,
                                 "outcome": "skipped: run ended first"})
                continue
            t = round(self.rx.elapsed_s() or 0.0, 2)
            try:
                if ev.kind == "pause":
                    self.rx.pause(ev.secs)
                    outcome = "ok"
                elif ev.kind == "restart":
                    self.rx.restart_connection()
                    outcome = "ok"
                elif ev.kind == "linkdrop":
                    self.control.linkdrop(int(ev.secs * 1000))
                    outcome = "ok"
                else:                                         # pfblock
                    if pf_ok is None:
                        pf_ok = self.pf.available()
                    if not pf_ok or not self.device_ip:
                        outcome = ("skipped: pfctl needs passwordless sudo"
                                   if not pf_ok else "skipped: device address unknown")
                    else:
                        outcome = self.pf.block(self.device_ip, ev.secs)
            except Exception as e:                            # noqa: BLE001
                outcome = f"error: {type(e).__name__}: {e}"
            self.log.append({"at_s": ev.at_s, "t_s": t, "kind": ev.kind,
                             "secs": ev.secs, "outcome": outcome})


# The campaign's two stress schedules (docs/CAMPAIGN.md, tests 3.5 and 3.6): one event
# every 5 minutes of a 30-minute run, at 2.5, 7.5, ... 27.5 min.
def tcp_stress(total_s: float = 1800.0) -> list[dict]:
    kinds = [("pause", 1), ("pause", 5), ("pause", 30), ("pfblock", 10), ("restart", 0),
             ("linkdrop", 10)]
    return _spread(kinds, total_s)


def udp_stress(total_s: float = 1800.0) -> list[dict]:
    kinds = [("linkdrop", 1), ("linkdrop", 5), ("linkdrop", 30), ("linkdrop", 1),
             ("linkdrop", 5), ("linkdrop", 30)]
    return _spread(kinds, total_s)


def _spread(kinds, total_s: float) -> list[dict]:
    step = total_s / len(kinds)
    return [{"at_s": round(step * (i + 0.5), 1), "kind": k, "secs": float(s)}
            for i, (k, s) in enumerate(kinds)]
