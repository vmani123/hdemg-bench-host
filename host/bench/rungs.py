"""Per-chip tuning ladders (spec §1.0a, plan §5.3/§5.4).

This is a BEST-EFFORT comparison: each chip is tuned independently, including with
levers the other chip does not have. The ladders therefore diverge on purpose. What the
host repo owns is the bookkeeping that keeps the *effort* comparable:

  * one common-lever checklist, so a lever tried on one chip is visibly either tried or
    explicitly declined on the other, never silently forgotten;
  * a measured gain recorded against every rung, including the ones that lost;
  * the stop rule, evaluated from those gains rather than from memory.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

import yaml

STOP_RULE_WINDOW = 3        # consecutive rungs...
STOP_RULE_GAIN_PCT = 2.0    # ...each below this gain means the ladder has plateaued


@dataclass
class Rung:
    id: str
    applies_to: str
    rationale: str = ""
    parent: str | None = None
    config: dict = field(default_factory=dict)
    lever: str | None = None
    measured_gain_pct: float | None = None

    @property
    def is_measured(self) -> bool:
        return self.measured_gain_pct is not None


class Ladder:
    def __init__(self, chip: str, rungs: list[Rung]):
        self.chip = chip
        self.rungs = {r.id: r for r in rungs}
        self.order = [r.id for r in rungs]

    @classmethod
    def load(cls, root: str | Path, chip: str) -> "Ladder":
        d = Path(root) / _short(chip)
        rungs = []
        for p in sorted(d.glob("*.yaml")):
            raw = yaml.safe_load(p.read_text()) or {}
            rungs.append(Rung(
                id=raw.get("id", p.stem),
                applies_to=raw.get("applies_to", chip),
                rationale=raw.get("rationale", ""),
                parent=raw.get("parent"),
                config=raw.get("config") or {},
                lever=raw.get("lever"),
                measured_gain_pct=raw.get("measured_gain_pct"),
            ))
        return cls(chip, rungs)

    def resolve(self, rung_id: str) -> dict:
        """Flatten a rung and its ancestors into one sdkconfig mapping.

        A null value removes the symbol, which is how a chip opts out of a lever that is
        invalid on it (e.g. CONFIG_ESP_INTR_IN_IRAM on the C5).
        """
        chain: list[Rung] = []
        seen: set[str] = set()
        cur: str | None = rung_id
        while cur:
            if cur in seen:
                raise ValueError(f"rung cycle at {cur!r}")
            seen.add(cur)
            if cur not in self.rungs:
                raise KeyError(f"no such rung {cur!r} in the {self.chip} ladder")
            r = self.rungs[cur]
            chain.append(r)
            cur = r.parent
        cfg: dict = {}
        for r in reversed(chain):
            for k, v in r.config.items():
                if v is None:
                    cfg.pop(k, None)
                else:
                    cfg[k] = v
        return cfg

    def plateaued(self) -> bool:
        """The §5.4 stop rule, evaluated from recorded gains."""
        gains = [self.rungs[i].measured_gain_pct for i in self.order
                 if self.rungs[i].is_measured]
        if len(gains) < STOP_RULE_WINDOW:
            return False
        return all(g < STOP_RULE_GAIN_PCT for g in gains[-STOP_RULE_WINDOW:])

    def unmeasured(self) -> list[str]:
        return [i for i in self.order if not self.rungs[i].is_measured]


def load_common_levers(root: str | Path) -> dict:
    p = Path(root) / "common-levers.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {"levers": []}


def effort_report(root: str | Path, chips=("esp32s3", "esp32c5")) -> dict:
    """Is the equal-effort protocol being honoured? Reported, never silently enforced."""
    common = load_common_levers(root)
    ladders = {c: Ladder.load(root, c) for c in chips}
    rows = []
    for lev in common.get("levers", []):
        att = lev.get("attempted", {})
        row = {"lever": lev.get("id"),
               **{_short(c): att.get(_short(c), att.get(c, "NOT ATTEMPTED")) for c in chips}}
        rows.append(row)
    gaps = [r for r in rows
            if any(str(v) == "NOT ATTEMPTED" for k, v in r.items() if k != "lever")]
    return {
        "levers": rows,
        "unattempted": gaps,
        "equal_effort_ok": not gaps,
        "plateaued": {c: ladders[c].plateaued() for c in chips},
        "rung_counts": {c: len(ladders[c].order) for c in chips},
    }


def _short(chip: str) -> str:
    return {"esp32s3": "s3", "esp32c5": "c5"}.get(chip, chip)
