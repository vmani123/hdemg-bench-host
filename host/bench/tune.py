"""bench tune — a one-lever-at-a-time climber for the max-throughput study.

For each (chip, band) the study names a starting rung and an ordered list of candidate
levers. The engine measures the start, then for each candidate:

  1. writes a rung whose parent is the current best (the incumbent), so the candidate
     differs from it by exactly one lever;
  2. builds, flashes and measures it through the ordinary `bench run` machinery — every
     gate, the ledger, resume and recovery included — at ONE saturating offered load per
     transport, so the goodput read back is the most the device delivered;
  3. keeps it only if some transport gains at least `gain_pct` beyond the repeat spread
     and no transport regresses by as much; otherwise the incumbent stays.

A chip stops climbing after `stop_after_misses` misses in a row (plan §5.4), or when the
list runs out. A candidate that produced no valid measurement (a build Kconfig rejected,
a board that never answered) is recorded as unmeasured: it says nothing about the lever,
so it is not a miss — it is left for a person to look at.

Everything is resumable. The ledger holds the runs, state.json the decisions, and the
plan is re-read before every step, which is how a supervisor steers a running study:
it edits the plan (adds, reorders or removes candidates) and the next step follows it.

Study rungs carry `study:` so the formal ladder and Stage 1's `tuned` never see them.
"""
from __future__ import annotations
import json
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ledger import Ledger
from .orchestrator import Matrix
from .rungs import _short

HOST = Path(__file__).resolve().parents[1]


class TuneError(RuntimeError):
    pass


@dataclass
class Candidate:
    id: str
    config: dict
    lever: str | None = None
    why: str = ""
    # A lever that can lift the ceiling past the plan's saturating load (a wider channel)
    # must be offered more, or it would read as no gain.
    saturate_mbps: float | None = None


@dataclass
class Climb:
    chip: str
    band: str
    start: str           # a rung id, or "best:<band>" for the incumbent another band ended on
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.chip}/{self.band}"


@dataclass
class Plan:
    study: str
    climbs: list[Climb]
    ap: str = "verizon-router"
    ssid: str | None = None
    expected_idf: str | None = None
    frame_bytes: int = 270
    repeats: int = 2
    hold_s: float = 20.0
    discard_s: float = 3.0
    transports: list[str] = field(default_factory=lambda: ["tcp", "udp"])
    saturate_mbps: float = 60.0
    gain_pct: float = 2.0
    stop_after_misses: int = 3
    unsaturated_ratio: float = 0.9
    ledger: str = "results/tuning/tuning.jsonl"
    state_dir: str = "results/tuning/state"

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        climbs = []
        for c in raw.get("climbs") or []:
            cands = [Candidate(id=str(k["id"]), config=dict(k.get("config") or {}),
                               lever=k.get("lever"), why=str(k.get("why") or ""),
                               saturate_mbps=k.get("saturate_mbps"))
                     for k in c.get("candidates") or []]
            climbs.append(Climb(chip=c["chip"], band=str(c["band"]),
                                start=str(c["start"]), candidates=cands))
        keys = {f.name for f in cls.__dataclass_fields__.values()} - {"climbs"}
        return cls(climbs=climbs, **{k: raw[k] for k in keys if k in raw})


def _band_tag(band: str) -> str:
    return str(band).replace(".", "")


class Engine:
    def __init__(self, plan: Plan, *, band: str, rungs_root: str | Path,
                 state_dir: str | Path, ledger: str | Path, runner,
                 plan_path: str | Path | None = None, log=print):
        self.plan = plan
        self.band = str(band)
        self.rungs_root = Path(rungs_root)
        self.state_dir = Path(state_dir)
        self.ledger = Path(ledger)
        self.runner = runner
        self.plan_path = Path(plan_path) if plan_path else None
        self.log = log

    # ---- state ------------------------------------------------------------------
    def _state_path(self) -> Path:
        return self.state_dir / "state.json"

    def _load_state(self) -> dict:
        p = self._state_path()
        if p.exists():
            return json.loads(p.read_text())
        return {"study": self.plan.study, "climbs": {}}

    def _save_state(self, state: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = self._state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self._state_path())
        (self.state_dir / "summary.md").write_text(render_summary(state))

    # ---- the loop -------------------------------------------------------------------
    def run(self, max_steps: int | None = None) -> dict:
        state = self._load_state()
        budget = [max_steps]
        for key in [c.key for c in self.plan.climbs if c.band == self.band]:
            self._climb(key, state, budget)
        self._save_state(state)
        return {"study": state["study"],
                "climbs": {k: v for k, v in state["climbs"].items()
                           if v["band"] == self.band}}

    def _climb_def(self, key: str) -> Climb | None:
        if self.plan_path is not None:              # the supervisor may have edited it
            self.plan = Plan.load(self.plan_path)
        return next((c for c in self.plan.climbs if c.key == key), None)

    def _spend(self, budget: list) -> bool:
        if budget[0] is None:
            return True
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        return True

    def _climb(self, key: str, state: dict, budget: list) -> None:
        climb = self._climb_def(key)
        cs = state["climbs"].setdefault(key, {
            "chip": climb.chip, "band": climb.band, "incumbent": None, "steps": [],
            "misses": 0, "done": False, "done_reason": None, "blocked": None, "best": {}})

        # Step 0: the starting rung, measured under the same conditions as everything
        # that will be compared with it. Retried on every invocation until it measures.
        if not cs["steps"] or cs["steps"][0]["decision"] == "unmeasured":
            start = self._resolve_start(climb, state)
            if start is None:
                cs["blocked"] = f"start {climb.start!r} has no result yet — run that band first"
                return
            if not self._spend(budget):
                return
            self.log(f"[{key}] start: measuring {start}")
            step = self._measure(climb, start, None, None)
            step["decision"] = "start" if self._complete(step) else "unmeasured"
            cs["steps"][:1] = [step]
            if step["decision"] == "unmeasured":
                cs["blocked"] = ("the starting rung produced no complete measurement — "
                                 "see " + step["log"])
                self._save_state(state)
                return
            cs["blocked"] = None
            cs["incumbent"] = start
            self._refresh(cs)
            self._save_state(state)
            self.log(f"[{key}] start {start}: {_describe(step)}")

        while True:
            climb = self._climb_def(key)
            cs["done"], cs["done_reason"] = False, None
            if cs["misses"] >= self.plan.stop_after_misses:
                cs["done"] = True
                cs["done_reason"] = (f"{cs['misses']} consecutive candidates gained under "
                                     f"{self.plan.gain_pct:g} % (stop rule)")
                break
            tried = {s["candidate"] for s in cs["steps"] if s.get("candidate")}
            cand = next((c for c in climb.candidates if c.id not in tried), None)
            if cand is None:
                cs["done"], cs["done_reason"] = True, "candidate list exhausted"
                break
            if not self._spend(budget):
                break
            n = len(cs["steps"])
            rung = f"t{_band_tag(climb.band)}-{n:02d}-{cand.id}"
            parent = cs["incumbent"]
            self._write_rung(climb.chip, rung, parent, cand)
            self.log(f"[{key}] step {n}: {rung} ({cand.lever}) on top of {parent}")
            inc = self._incumbent_step(cs)
            step = self._measure(climb, rung, cand, parent, floor_mbps=inc["offered_mbps"])
            self._decide(step, inc)
            cs["steps"].append(step)
            if step["decision"] == "kept":
                cs["incumbent"], cs["misses"] = rung, 0
            elif step["decision"] == "rejected":
                cs["misses"] += 1
            self._record_in_rung(climb.chip, rung, step)
            self._refresh(cs)
            self._save_state(state)
            self.log(f"[{key}] {rung}: {_describe(step)} -> {step['decision'].upper()}"
                     f" ({step['reason']})")

    def _resolve_start(self, climb: Climb, state: dict) -> str | None:
        if climb.start.startswith("best:"):
            other = state["climbs"].get(f"{climb.chip}/{climb.start[5:]}")
            return other.get("incumbent") if other else None
        return climb.start

    def _incumbent_step(self, cs: dict) -> dict:
        return next(s for s in reversed(cs["steps"])
                    if s["rung"] == cs["incumbent"] and s["decision"] in ("start", "kept"))

    # ---- measurement ---------------------------------------------------------------
    def _measure(self, climb: Climb, rung: str, cand: Candidate | None,
                 parent: str | None, floor_mbps: float = 0.0) -> dict:
        # The saturating load: the plan's, raised by a candidate that needs more (a wider
        # channel), and never below what the incumbent was measured at — once a lever
        # that needed more has won, everything stacked on it needs as much.
        sat = max(float(self.plan.saturate_mbps), float(floor_mbps),
                  float(cand.saturate_mbps) if cand and cand.saturate_mbps else 0.0)
        tag = f"{_short(climb.chip)}-{_band_tag(climb.band)}-{rung}"
        mdir, ldir = self.state_dir / "matrices", self.state_dir / "logs"
        mdir.mkdir(parents=True, exist_ok=True)
        ldir.mkdir(parents=True, exist_ok=True)
        mpath, lpath = mdir / f"{tag}.yaml", ldir / f"{tag}.log"
        mpath.write_text(yaml.safe_dump({
            "stage": 1, "repeats": self.plan.repeats, "ap": self.plan.ap,
            "ssid": self.plan.ssid, "expected_idf": self.plan.expected_idf,
            "frame_bytes": self.plan.frame_bytes,
            "sweep": {"start_mbps": sat, "stop_mbps": sat, "steps": 1,
                      "hold_s": self.plan.hold_s, "discard_s": self.plan.discard_s},
            "cells": [{"chip": climb.chip, "band": climb.band, "source": "synth",
                       "transport": [t], "tune": [rung], "loads_mbps": [sat]}
                      for t in self.plan.transports],
        }, sort_keys=False))
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        rc = self.runner(mpath, self.ledger, lpath)
        if rc == 2:
            raise TuneError(f"`bench run` refused to start for {rung} (wrong network, "
                            f"parity, or firmware not configured) — see {lpath}")
        cells = {r.cell_id: r.transport for r in Matrix.load(mpath).expand()}
        recs = [r for r in Ledger(self.ledger).read()
                if r.get("cell_id") in cells and r.get("valid", True)]
        step = {"rung": rung, "candidate": cand.id if cand else None,
                "lever": cand.lever if cand else None, "parent": parent,
                "offered_mbps": sat, "log": str(lpath)}
        for t in self.plan.transports:
            step[t] = _score([r for r in recs if r["transport"] == t], sat,
                             self.plan.unsaturated_ratio)
        return step

    def _complete(self, step: dict) -> bool:
        return all(step[t] is not None and step[t]["n"] >= self.plan.repeats
                   for t in self.plan.transports)

    def _decide(self, step: dict, inc: dict) -> None:
        if not self._complete(step):
            step.update(decision="unmeasured", gain_pct={},
                        reason="no complete measurement (build, flash or gates) — see log")
            return
        gains, gained, regressed, unresolved = {}, [], [], []
        for t in self.plan.transports:
            a, b = inc[t], step[t]
            g = 100.0 * (b["mbps"] - a["mbps"]) / a["mbps"]
            noise = 100.0 * (a["spread"] + b["spread"]) / a["mbps"]
            gains[t] = round(g, 2)
            if abs(g) >= self.plan.gain_pct and abs(g) <= noise:
                unresolved.append(f"{t} {g:+.1f}% inside the repeat spread ±{noise:.1f}%")
            elif g >= self.plan.gain_pct:
                gained.append(f"{t} {g:+.1f}%")
            elif g <= -self.plan.gain_pct:
                regressed.append(f"{t} {g:+.1f}%")
        step["gain_pct"] = gains
        if regressed:
            step.update(decision="rejected", reason="regressed: " + ", ".join(regressed))
        elif gained:
            step.update(decision="kept", reason="gained: " + ", ".join(gained))
        elif unresolved:
            step.update(decision="rejected", reason="; ".join(unresolved))
        else:
            step.update(decision="rejected",
                        reason=f"no transport gained {self.plan.gain_pct:g} %")

    # ---- rung files ----------------------------------------------------------------
    def _rung_path(self, chip: str, rung: str) -> Path:
        return self.rungs_root / _short(chip) / f"{rung}.yaml"

    def _write_rung(self, chip: str, rung: str, parent: str, cand: Candidate) -> None:
        path = self._rung_path(chip, rung)
        body = {"id": rung, "applies_to": chip, "parent": parent,
                "study": self.plan.study, "lever": cand.lever, "rationale": cand.why,
                "config": cand.config, "measured_gain_pct": None}
        if path.exists():
            old = yaml.safe_load(path.read_text()) or {}
            if any(old.get(k) != body[k] for k in ("parent", "study", "config")):
                raise TuneError(f"{path} already exists with a different parent, study or "
                                f"config — refusing to overwrite it; rename the candidate")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Written by `bench tune` (study rung — not part of the formal "
                        "ladder).\n" + yaml.safe_dump(body, sort_keys=False))

    def _record_in_rung(self, chip: str, rung: str, step: dict) -> None:
        path = self._rung_path(chip, rung)
        body = yaml.safe_load(path.read_text()) or {}
        gains = step.get("gain_pct") or {}
        body["measured_gain_pct"] = max(gains.values()) if gains else None
        body["measured"] = {
            "decision": step["decision"], "reason": step["reason"],
            **{f"{t}_mbps": (step[t] or {}).get("mbps") for t in self.plan.transports},
            **{f"gain_{t}_pct": gains.get(t) for t in self.plan.transports}}
        path.write_text("# Written by `bench tune` (study rung — not part of the formal "
                        "ladder).\n" + yaml.safe_dump(body, sort_keys=False))

    def _refresh(self, cs: dict) -> None:
        """Best number per transport across every measured step of the climb."""
        best = {}
        for t in self.plan.transports:
            done = [s for s in cs["steps"] if s.get(t) and s["decision"] != "unmeasured"]
            if done:
                s = max(done, key=lambda s: s[t]["mbps"])
                best[t] = {"rung": s["rung"], "mbps": s[t]["mbps"],
                           "loss_pct": s[t]["loss_pct"]}
        cs["best"] = best


def _score(recs: list[dict], offered_mbps: float, unsat_ratio: float) -> dict | None:
    if not recs:
        return None
    goods = [r["metrics"].get("goodput_bps", 0.0) / 1e6 for r in recs]
    med = statistics.median(goods)
    loss = statistics.median([r["metrics"].get("loss_pct", 0.0) for r in recs])
    rssi = [r["rf"].get("rssi_dbm") for r in recs if r.get("rf", {}).get("rssi_dbm")]
    phy = Counter(r.get("rf", {}).get("phy_rate") for r in recs).most_common(1)[0][0]
    return {"mbps": round(med, 3), "spread": round((max(goods) - min(goods)) / 2, 3),
            "loss_pct": round(loss, 3), "n": len(recs), "phy_rate": phy,
            "rssi_dbm": statistics.median(rssi) if rssi else None,
            "unsaturated": med >= unsat_ratio * offered_mbps}


def _describe(step: dict) -> str:
    parts = []
    for t in ("tcp", "udp"):
        s = step.get(t)
        if s is None:
            if t in step:
                parts.append(f"{t} —")
            continue
        flag = " UNSATURATED" if s["unsaturated"] else ""
        loss = f" (loss {s['loss_pct']:.2f}%)" if t == "udp" else ""
        parts.append(f"{t} {s['mbps']:.1f}±{s['spread']:.1f} Mbit/s{loss}{flag}")
    return "; ".join(parts)


def render_summary(state: dict) -> str:
    out = [f"# Tuning study: {state.get('study')}", "",
           f"_updated {state.get('updated', '')}_", ""]
    for key, cs in state.get("climbs", {}).items():
        out.append(f"## {cs['chip']} @ {cs['band']} GHz")
        out.append("")
        status = (f"BLOCKED: {cs['blocked']}" if cs.get("blocked") else
                  f"done — {cs['done_reason']}" if cs.get("done") else "climbing")
        out.append(f"- incumbent: **{cs.get('incumbent')}** ({status})")
        for t, b in (cs.get("best") or {}).items():
            loss = f", loss {b['loss_pct']:.2f}%" if t == "udp" else ""
            out.append(f"- best {t.upper()}: **{b['mbps']:.1f} Mbit/s**{loss} ({b['rung']})")
        out += ["", "| step | rung | lever | parent | TCP Mbit/s | UDP Mbit/s (loss) | "
                "gain % | decision | link | reason |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for i, s in enumerate(cs.get("steps", [])):
            tcp, udp = s.get("tcp"), s.get("udp")
            tcp_s = f"{tcp['mbps']:.1f} ±{tcp['spread']:.1f}" if tcp else "—"
            udp_s = (f"{udp['mbps']:.1f} ±{udp['spread']:.1f} ({udp['loss_pct']:.2f}%)"
                     if udp else "—")
            if tcp and tcp["unsaturated"]:
                tcp_s += " ⚠unsat"
            if udp and udp["unsaturated"]:
                udp_s += " ⚠unsat"
            gains = ", ".join(f"{t} {g:+.1f}" for t, g in (s.get("gain_pct") or {}).items())
            link = (tcp or udp or {}).get("phy_rate") or "—"
            out.append(f"| {i} | {s['rung']} | {s.get('lever') or '—'} | "
                       f"{s.get('parent') or '—'} | {tcp_s} | {udp_s} | {gains or '—'} | "
                       f"{s['decision']} | {link} | {s.get('reason', '')} |")
        out.append("")
    return "\n".join(out) + "\n"


# ---- runners: how a generated matrix gets measured ------------------------------------

class SimRunner:
    """Loopback fake ESPs, in process. Plumbing only: the fake's goodput does not depend
    on the rung, so it exercises the machinery, not the decisions."""

    def __call__(self, matrix_path, ledger_path, log_path) -> int:
        from .drivers import SimDriver
        from .orchestrator import Orchestrator
        m = Matrix.load(matrix_path)
        drv = SimDriver()
        try:
            Orchestrator(m, drv, Ledger(ledger_path),
                         rig_ceilings={"2.4": 1000.0, "5": 1000.0}).run_all()
        finally:
            drv.shutdown()
        Path(log_path).write_text(f"simulated {matrix_path}\n")
        return 0


class HardwareRunner:
    """`bench run` on the real boards, one subprocess per step. Every gate, resume pass,
    keepalive and power-cycle recovery of a normal sweep applies unchanged."""

    def __init__(self, *, band: str, ceilings: str, s3_port: str, c5_port: str,
                 s3_hub_port: str | None = None, c5_hub_port: str | None = None,
                 allow_shared_band: bool = False, passes: int = 2):
        self.args = ["--band", str(band), "--ceilings", ceilings,
                     "--s3-port", s3_port, "--c5-port", c5_port,
                     "--unattended", "--rssi-drift-warn", "--passes", str(passes)]
        if s3_hub_port:
            self.args += ["--s3-hub-port", s3_hub_port]
        if c5_hub_port:
            self.args += ["--c5-hub-port", c5_hub_port]
        if allow_shared_band:
            self.args.append("--allow-shared-band")

    def __call__(self, matrix_path, ledger_path, log_path) -> int:
        cmd = [sys.executable, "-u", "-m", "cli.bench", "run", "--matrix", str(matrix_path),
               "--ledger", str(ledger_path), *self.args]
        with open(log_path, "a") as log:
            log.write(f"\n$ {' '.join(cmd)}\n")
            log.flush()
            p = subprocess.Popen(cmd, cwd=HOST, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                log.write(line)
                log.flush()
                print("    " + line, end="", flush=True)
            return p.wait()
