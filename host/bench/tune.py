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

What a number must be before it may decide anything:

  * SATURATED. Goodput within `unsaturated_ratio` of the offered load means the load set
    it, not the chip. Such a measurement is repeated at `load_step` x the load, up to
    the rig ceiling; still unsaturated there, it is not a result (a starting rung that
    outruns the ceiling blocks the climb).
  * AT THE SAME LOAD AS WHAT IT IS COMPARED WITH. A candidate offered more than the
    incumbent was measured at (a wider channel, a raised load) makes the engine
    re-measure the incumbent at that load first. Once the load has gone up it stays up.
  * IN EFFECT. A candidate may name what the link must read (`expect_phy`, e.g. "bw40
    sta40"); a lever the access point did not honour was not tested.

A candidate that did not produce such a number is `unmeasured`: it says nothing about the
lever, so it is not a miss, and it is retried on the next invocation (`max_attempts` in
all). Two unmeasured candidates in a row stop the climb as `blocked` — a failure hitting
every step (a content filter, a lapsed flash window, a refused load) is the rig, and must
not quietly use up the list. A chip stops after `stop_after_misses` real misses in a row
(plan §5.4), or when its list runs out.

Everything is resumable: the ledger holds the runs, state.json the decisions, and the
plan is re-read before every step, which is how a supervisor steers a running study. One
engine per study at a time (a lock in the state directory).

Study rungs carry `study:` so the formal ladder and Stage 1's `tuned` never see them.
"""
from __future__ import annotations
import fcntl
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .ledger import Ledger
from .orchestrator import Matrix
from .rungs import _short

HOST = Path(__file__).resolve().parents[1]


class TuneError(RuntimeError):
    pass


@contextmanager
def board_lock(chip: str, port: str, *, owner: str, expected_s: float,
               root: str | Path | None = None):
    """Hold a board shared with other work (other firmware, other sessions).

    The protocol agreed between the sessions that use these boards: the lock is the
    directory ~/.<chip>-<port>.lock, taken with mkdir (atomic) and released by removing
    it; an `owner` file inside says who holds it and until when. A held lock is never
    broken here — whoever holds it is asked first. Whoever takes it reflashes their own
    firmware before using the board, which every study step does anyway."""
    d = Path(root or Path.home()) / f".{chip}-{port.removeprefix('cu.')}.lock"
    try:
        d.mkdir()
    except FileExistsError:
        try:
            who = (d / "owner").read_text().strip()
        except OSError:
            who = "(no owner file)"
        raise TuneError(f"{chip} on {port} is locked by another session ({d}) — not "
                        f"touching it. Ask the holder to release it:\n{who}") from None
    now = time.time()
    stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t))  # noqa: E731
    (d / "owner").write_text(f"owner {owner}\nacquired {stamp(now)}\n"
                             f"expected_release {stamp(now + expected_s)}\n"
                             f"pid {os.getpid()}\n")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@dataclass
class Candidate:
    id: str
    config: dict
    lever: str | None = None
    why: str = ""
    # A lever that can lift the ceiling past the plan's saturating load (a wider channel)
    # must be offered more, or it would read as no gain.
    saturate_mbps: float | None = None
    # A substring the link's phy_rate must contain for the lever to count as in effect.
    expect_phy: str | None = None


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
    max_load_mbps: float = 150.0       # never offered more (the rig ceiling may lower it)
    load_step: float = 1.5             # how much more an unsaturated measurement is offered
    gain_pct: float = 2.0
    stop_after_misses: int = 3
    block_after_unmeasured: int = 2
    max_attempts: int = 2
    unsaturated_ratio: float = 0.9
    ledger: str = "results/tuning/tuning.jsonl"
    state_dir: str = "results/tuning/state"

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        if not raw.get("study"):
            raise TuneError(f"{path}: `study:` must name the study — without it the "
                            f"study's rungs would count as formal rungs")
        climbs = []
        for c in raw.get("climbs") or []:
            cands = [Candidate(id=str(k["id"]), config=dict(k.get("config") or {}),
                               lever=k.get("lever"), why=str(k.get("why") or ""),
                               saturate_mbps=k.get("saturate_mbps"),
                               expect_phy=k.get("expect_phy"))
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
                 plan_path: str | Path | None = None, max_load_mbps: float | None = None,
                 chips: list[str] | None = None, log=print):
        self.plan = plan
        self.band = str(band)
        self.rungs_root = Path(rungs_root)
        self.state_dir = Path(state_dir)
        self.ledger = Path(ledger)
        self.runner = runner
        self.plan_path = Path(plan_path) if plan_path else None
        self.max_load_mbps = max_load_mbps      # the rig ceiling for this band, if known
        # Only these chips' climbs. A board shared with other work (other firmware on
        # it) is left entirely alone: never built, flashed or measured.
        self.chips = list(chips) if chips else None
        self.log = log

    def _cap(self) -> float:
        cap = float(self.plan.max_load_mbps)
        return min(cap, float(self.max_load_mbps)) if self.max_load_mbps else cap

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
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_dir / ".lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise TuneError(f"another `bench tune` is already running on this study "
                                f"({self.state_dir})") from None
            state = self._load_state()
            budget = [max_steps]
            for key in [c.key for c in self.plan.climbs if self._mine(c)]:
                self._climb(key, state, budget)
            self._save_state(state)
        return {"study": state["study"],
                "climbs": {k: v for k, v in state["climbs"].items()
                           if v["band"] == self.band
                           and (not self.chips or v["chip"] in self.chips)}}

    def _mine(self, c: Climb) -> bool:
        return c.band == self.band and (not self.chips or c.chip in self.chips)

    def _climb_def(self, key: str) -> Climb | None:
        if self.plan_path is not None:              # the supervisor may have edited it
            try:
                self.plan = Plan.load(self.plan_path)
            except (yaml.YAMLError, KeyError, TypeError, TuneError) as e:
                self.log(f"plan unreadable ({e}); keeping the previous version")
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
        if climb is None:
            return
        cs = state["climbs"].setdefault(key, {
            "chip": climb.chip, "band": climb.band, "incumbent": None, "steps": [],
            "misses": 0, "done": False, "done_reason": None, "blocked": None, "best": {}})
        cs["blocked"] = None                 # every block is re-evaluated on a new run

        # Step 0: the starting rung. Retried on every invocation until it measures.
        if not cs["steps"] or cs["steps"][0]["decision"] == "unmeasured":
            start, why = self._resolve_start(climb, state)
            if start is None:
                cs["blocked"] = why
                return
            if not self._spend(budget):
                return
            self.log(f"[{key}] start: measuring {start}")
            step = self._saturating(climb, start, None, None, self.plan.saturate_mbps)
            if not self._complete(step):
                step.update(decision="unmeasured",
                            reason="no complete measurement — see " + step["log"])
            elif step.get("load_limited"):
                step.update(decision="unmeasured",
                            reason=f"still unsaturated at the {self._cap():g} Mbit/s rig "
                                   f"ceiling: the chip outruns what this rig can measure")
            else:
                step.update(decision="start", reason="starting point")
            cs["steps"][:1] = [step]
            if step["decision"] == "unmeasured":
                cs["blocked"] = f"the starting rung {start}: {step['reason']}"
                self._save_state(state)
                return
            cs["incumbent"] = start
            self._refresh(cs)
            self._save_state(state)
            self.log(f"[{key}] start {start}: {_describe(step)}")

        attempted_now: set[str] = set()
        unmeasured_run = 0
        while True:
            climb = self._climb_def(key)
            if climb is None:
                break
            cs["done"], cs["done_reason"] = False, None
            if cs["misses"] >= self.plan.stop_after_misses:
                cs["done"] = True
                cs["done_reason"] = (f"{cs['misses']} consecutive candidates gained under "
                                     f"{self.plan.gain_pct:g} % (stop rule)")
                break
            cand = next((c for c in climb.candidates
                         if c.id not in attempted_now and self._open(cs, c.id)), None)
            if cand is None:
                retry = [c.id for c in climb.candidates if self._open(cs, c.id)]
                cs["done"] = True
                cs["done_reason"] = "candidate list exhausted" + (
                    f" (unmeasured, retried on the next run: {', '.join(retry)})"
                    if retry else "")
                break
            if not self._spend(budget):
                break
            attempted_now.add(cand.id)
            n = len(cs["steps"])
            rung = f"t{_band_tag(climb.band)}-{n:02d}-{cand.id}"
            parent = cs["incumbent"]
            self._write_rung(climb.chip, rung, parent, cand)
            self.log(f"[{key}] step {n}: {rung} ({cand.lever}) on top of {parent}")
            inc = self._incumbent_step(cs)
            load = max(self.plan.saturate_mbps, inc["offered_mbps"], cand.saturate_mbps or 0)
            step = self._saturating(climb, rung, cand, parent, load)
            self._judge(step, cand, climb, cs)
            cs["steps"].append(step)
            if step["decision"] == "kept":
                cs["incumbent"], cs["misses"] = rung, 0
            elif step["decision"] == "rejected":
                cs["misses"] += 1
            self._record_in_rung(climb.chip, rung, step)
            self._refresh(cs)
            self.log(f"[{key}] {rung}: {_describe(step)} -> {step['decision'].upper()}"
                     f" ({step['reason']})")
            unmeasured_run = unmeasured_run + 1 if step["decision"] == "unmeasured" else 0
            if unmeasured_run >= self.plan.block_after_unmeasured:
                cs["blocked"] = (f"{unmeasured_run} candidates in a row produced no usable "
                                 f"measurement — likely the rig, not the levers (see their "
                                 f"logs). Fix it and re-run; they will be retried.")
                self._save_state(state)
                break
            self._save_state(state)

    def _open(self, cs: dict, cand_id: str) -> bool:
        """Still to be tried: never decided, and under the attempt limit."""
        mine = [s for s in cs["steps"] if s.get("candidate") == cand_id]
        if any(s["decision"] in ("kept", "rejected") for s in mine):
            return False
        return len(mine) < self.plan.max_attempts

    def _resolve_start(self, climb: Climb, state: dict) -> tuple[str | None, str | None]:
        if not climb.start.startswith("best:"):
            return climb.start, None
        band = climb.start[5:]
        other = state["climbs"].get(f"{climb.chip}/{band}")
        if not other or not other.get("incumbent"):
            return None, f"starts from the {band} GHz result — run that band first"
        if not other.get("done") or other.get("blocked"):
            return None, (f"starts from the {band} GHz result, and that climb is not "
                          f"finished ({other.get('blocked') or 'still climbing'})")
        return other["incumbent"], None

    def _incumbent_step(self, cs: dict) -> dict:
        for s in reversed(cs["steps"]):
            if s["rung"] == cs["incumbent"] and s["decision"] in ("start", "kept"):
                return s
        raise TuneError(f"state for {cs['chip']}/{cs['band']} has no step for its "
                        f"incumbent {cs['incumbent']!r} — state.json is inconsistent")

    # ---- measurement ---------------------------------------------------------------
    def _saturating(self, climb: Climb, rung: str, cand: Candidate | None,
                    parent: str | None, load: float) -> dict:
        """Measure, raising the offered load while any transport is load-limited."""
        cap = self._cap()
        load = min(float(load), cap)
        while True:
            step = self._measure_at(climb, rung, cand, parent, load)
            if not self._complete(step) or not any(
                    step[t]["unsaturated"] for t in self.plan.transports):
                return step
            nxt = min(cap, round(load * self.plan.load_step, 1))
            if nxt <= load:
                step["load_limited"] = True
                return step
            self.log(f"    {rung} unsaturated at {load:g} Mbit/s offered; offering {nxt:g}")
            load = nxt

    def _measure_at(self, climb: Climb, rung: str, cand: Candidate | None,
                    parent: str | None, load: float) -> dict:
        tag = f"{_short(climb.chip)}-{_band_tag(climb.band)}-{rung}"
        mdir, ldir = self.state_dir / "matrices", self.state_dir / "logs"
        mdir.mkdir(parents=True, exist_ok=True)
        ldir.mkdir(parents=True, exist_ok=True)
        mpath, lpath = mdir / f"{tag}.yaml", ldir / f"{tag}.log"
        mpath.write_text(yaml.safe_dump({
            "stage": 1, "repeats": self.plan.repeats, "ap": self.plan.ap,
            "ssid": self.plan.ssid, "expected_idf": self.plan.expected_idf,
            "frame_bytes": self.plan.frame_bytes,
            "sweep": {"start_mbps": load, "stop_mbps": load, "steps": 1,
                      "hold_s": self.plan.hold_s, "discard_s": self.plan.discard_s},
            "cells": [{"chip": climb.chip, "band": climb.band, "source": "synth",
                       "transport": [t], "tune": [rung], "loads_mbps": [load]}
                      for t in self.plan.transports],
        }, sort_keys=False))
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        rc = self.runner(mpath, self.ledger, lpath)
        if rc == 2:
            raise TuneError(f"`bench run` refused to start for {rung} (wrong network, "
                            f"parity, or firmware not configured) — see {lpath}")
        runs = {(r.cell_id, r.offered_bps): r.transport for r in Matrix.load(mpath).expand()}
        recs = [r for r in Ledger(self.ledger).read()
                if (r.get("cell_id"), int(r.get("offered_bps", -1))) in runs
                and r.get("valid", True)]
        step = {"rung": rung, "candidate": cand.id if cand else None,
                "lever": cand.lever if cand else None, "parent": parent,
                "offered_mbps": load, "max_load_mbps": self._cap(), "log": str(lpath)}
        for t in self.plan.transports:
            step[t] = _score([r for r in recs if r["transport"] == t], load,
                             self.plan.unsaturated_ratio)
        return step

    def _complete(self, step: dict) -> bool:
        return all(step[t] is not None and step[t]["n"] >= self.plan.repeats
                   for t in self.plan.transports)

    def _judge(self, step: dict, cand: Candidate, climb: Climb, cs: dict) -> None:
        """Decide a candidate step — or say why it cannot be decided."""
        def unmeasured(reason: str) -> None:
            step.update(decision="unmeasured", gain_pct={}, reason=reason)

        if not self._complete(step):
            return unmeasured("no complete measurement (build, flash or gates) — see log")
        if step.get("load_limited"):
            return unmeasured(f"still unsaturated at the {self._cap():g} Mbit/s rig "
                              f"ceiling — raise the ceiling to measure this lever")
        if cand.expect_phy:
            links = {step[t]["phy_rate"] for t in self.plan.transports}
            off = [p for p in links if cand.expect_phy not in str(p)]
            if off:
                return unmeasured(f"lever not in effect: the link reads {off[0]!r}, "
                                  f"expected {cand.expect_phy!r}")
        inc = self._incumbent_step(cs)
        # Same access point as the incumbent, or the numbers compare two APs, not two
        # configurations (a network with several APs can hand a reflashed board the far one).
        here, there = self._channels(step), self._channels(inc)
        if here and there and here != there:
            return unmeasured(f"the board was on {'/'.join(sorted(here))}, the current best "
                              f"on {'/'.join(sorted(there))}: a different access point or "
                              f"channel, not comparable")
        if step["offered_mbps"] > inc["offered_mbps"]:
            # Compare at one load: bring the incumbent up to the candidate's.
            self.log(f"    re-measuring the incumbent {inc['rung']} at "
                     f"{step['offered_mbps']:g} Mbit/s to compare at the same load")
            again = self._measure_at(climb, inc["rung"], None, inc.get("parent"),
                                     step["offered_mbps"])
            if not self._complete(again):
                return unmeasured(f"the incumbent {inc['rung']} could not be re-measured "
                                  f"at {step['offered_mbps']:g} Mbit/s — see {again['log']}")
            inc.setdefault("history", []).append(
                {k: inc[k] for k in ("offered_mbps", *self.plan.transports)})
            inc.update({k: again[k] for k in ("offered_mbps", "max_load_mbps",
                                               *self.plan.transports)})
        if any(inc[t]["mbps"] <= 0 for t in self.plan.transports):
            return unmeasured(f"the incumbent {inc['rung']} measured 0 Mbit/s")
        self._decide(step, inc)

    def _channels(self, step: dict) -> set[str]:
        out = set()
        for t in self.plan.transports:
            m = re.search(r"\bch(\d+)\b", str((step.get(t) or {}).get("phy_rate") or ""))
            if m:
                out.add(f"ch{m.group(1)}")
        return out

    def reopen(self, key: str, rung: str, reason: str) -> None:
        """Withdraw a decision shown afterwards to be invalid (a step measured on the
        wrong access point): the step becomes unmeasured with the reason recorded, its
        candidate is tried again on the next run, and a false miss stops counting."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_dir / ".lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise TuneError("a `bench tune` is running on this study; stop it "
                                "first") from None
            state = self._load_state()
            cs = state["climbs"].get(key)
            step = next((s for s in (cs or {}).get("steps", []) if s["rung"] == rung), None)
            if step is None or step["decision"] not in ("rejected", "unmeasured"):
                raise TuneError(f"{key}: no rejected or unmeasured step {rung!r} to reopen "
                                f"(a kept step has later steps built on it)")
            step["reason"] = (f"reopened: {reason} (was {step['decision']}: "
                              f"{step.get('reason', '')})")
            step["decision"] = "unmeasured"
            last = max(i for i, s in enumerate(cs["steps"])
                       if s["decision"] in ("start", "kept"))
            cs["misses"] = sum(1 for s in cs["steps"][last + 1:]
                               if s["decision"] == "rejected")
            chip = cs["chip"]
            if self._rung_path(chip, rung).exists():
                self._record_in_rung(chip, rung, step)
            self._save_state(state)

    def _decide(self, step: dict, inc: dict) -> None:
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
            "offered_mbps": step["offered_mbps"],
            **{f"{t}_mbps": (step[t] or {}).get("mbps") for t in self.plan.transports},
            **{f"gain_{t}_pct": gains.get(t) for t in self.plan.transports}}
        path.write_text("# Written by `bench tune` (study rung — not part of the formal "
                        "ladder).\n" + yaml.safe_dump(body, sort_keys=False))

    def _refresh(self, cs: dict) -> None:
        """The headline numbers: the incumbent's. A rejected step's higher median is not
        a result — its gain did not survive its own repeat spread."""
        inc = self._incumbent_step(cs)
        cs["best"] = {t: {"rung": inc["rung"], "mbps": inc[t]["mbps"],
                          "loss_pct": inc[t]["loss_pct"]}
                      for t in self.plan.transports if inc.get(t)}


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
    return "; ".join(parts) + f" at {step.get('offered_mbps', 0):g} offered"


def render_summary(state: dict) -> str:
    out = [f"# Tuning study: {state.get('study')}", "",
           f"_updated {state.get('updated', '')}_", ""]
    for key, cs in state.get("climbs", {}).items():
        out.append(f"## {cs['chip']} @ {cs['band']} GHz")
        out.append("")
        status = (f"BLOCKED: {cs['blocked']}" if cs.get("blocked") else
                  f"done — {cs['done_reason']}" if cs.get("done") else "climbing")
        out.append(f"- current best configuration: **{cs.get('incumbent')}** ({status})")
        for t, b in (cs.get("best") or {}).items():
            loss = f", loss {b['loss_pct']:.2f}%" if t == "udp" else ""
            out.append(f"- {t.upper()}: **{b['mbps']:.1f} Mbit/s**{loss}")
        out += ["", "| step | rung | lever | parent | offered | TCP Mbit/s | "
                "UDP Mbit/s (loss) | gain % | decision | link | reason |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
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
            redo = " (re-measured)" if s.get("history") else ""
            out.append(f"| {i} | {s['rung']} | {s.get('lever') or '—'} | "
                       f"{s.get('parent') or '—'} | {s.get('offered_mbps', 0):g}{redo} | "
                       f"{tcp_s} | {udp_s} | {gains or '—'} | {s['decision']} | {link} | "
                       f"{s.get('reason', '')} |")
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

    HEARTBEAT_MAX_AGE_S = 60

    def __init__(self, *, band: str, ceilings: str, s3_port: str, c5_port: str,
                 agent_dir: str | Path, s3_hub_port: str | None = None,
                 c5_hub_port: str | None = None, allow_shared_band: bool = False,
                 passes: int = 2):
        self.agent_dir = Path(agent_dir)
        self.args = ["--band", str(band), "--ceilings", ceilings,
                     "--s3-port", s3_port, "--c5-port", c5_port,
                     "--agent-dir", str(self.agent_dir),
                     "--unattended", "--rssi-drift-warn", "--passes", str(passes)]
        if s3_hub_port:
            self.args += ["--s3-hub-port", s3_hub_port]
        if c5_hub_port:
            self.args += ["--c5-hub-port", c5_hub_port]
        if allow_shared_band:
            self.args.append("--allow-shared-band")

    def __call__(self, matrix_path, ledger_path, log_path) -> int:
        # hostrun.sh stops beating while it waits at its flash-approval prompt, and every
        # build or flash would then wait out its 30-minute timeout. Stop instead.
        hb = self.agent_dir / "heartbeat"
        if not hb.exists() or time.time() - hb.stat().st_mtime > self.HEARTBEAT_MAX_AGE_S:
            raise TuneError(f"hostrun.sh is not answering (no fresh {hb}): stopped, or "
                            f"waiting at its flash-approval prompt. Approve it or restart "
                            f"it, then re-run — nothing measured so far is lost.")
        cmd = [sys.executable, "-u", "-m", "cli.bench", "run", "--matrix", str(matrix_path),
               "--ledger", str(ledger_path), *self.args]
        with open(log_path, "a") as log:
            log.write(f"\n$ {' '.join(cmd)}\n")
            log.flush()
            p = subprocess.Popen(cmd, cwd=HOST, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            try:
                for line in p.stdout:
                    log.write(line)
                    log.flush()
                    print("    " + line, end="", flush=True)
                return p.wait()
            except BaseException:
                # A stopped study must not leave a child that goes on to build or flash
                # a board after the study's board locks are released.
                p.terminate()
                try:
                    p.wait(timeout=10)
                except Exception:                              # noqa: BLE001
                    p.kill()
                raise
