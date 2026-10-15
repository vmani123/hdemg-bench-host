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
import re
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
    # Set on rungs written by a tuning study (`bench tune`). They resolve like any rung,
    # so they can be built and flashed by name, but they are not steps of the formal
    # ladder: a study climbs past it and leaves losing siblings behind, which would
    # otherwise fork the ladder and change what Stage 1's `tuned` builds.
    study: str | None = None

    @property
    def is_measured(self) -> bool:
        return self.measured_gain_pct is not None


class Ladder:
    def __init__(self, chip: str, rungs: list[Rung]):
        self.chip = chip
        self.rungs = {r.id: r for r in rungs}
        self.order = [r.id for r in rungs]

    @property
    def formal(self) -> list[str]:
        """Rung ids of the formal ladder, in file order: everything but study rungs."""
        return [i for i in self.order if self.rungs[i].study is None]

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
                study=raw.get("study"),
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
        gains = [self.rungs[i].measured_gain_pct for i in self.formal
                 if self.rungs[i].is_measured]
        if len(gains) < STOP_RULE_WINDOW:
            return False
        return all(g < STOP_RULE_GAIN_PCT for g in gains[-STOP_RULE_WINDOW:])

    def unmeasured(self) -> list[str]:
        return [i for i in self.formal if not self.rungs[i].is_measured]

    def top(self) -> str:
        """The rung at the end of the longest parent chain: the chip's current best.

        Ambiguous if the ladder has forked into two equally deep branches — that is a
        decision for the ladder author, not something to be resolved silently here.
        """
        formal = self.formal
        parents = {self.rungs[i].parent for i in formal if self.rungs[i].parent}
        leaves = [i for i in formal if i not in parents]
        if not leaves:
            raise ValueError(f"the {self.chip} ladder has no leaf rung")

        def depth(i: str) -> int:
            n = 0
            while self.rungs[i].parent:
                i, n = self.rungs[i].parent, n + 1
            return n
        best = max(depth(i) for i in leaves)
        top = [i for i in leaves if depth(i) == best]
        if len(top) > 1:
            raise ValueError(f"the {self.chip} ladder forks into {top}; name the tuned "
                             f"rung explicitly in the matrix")
        return top[0]

    def rung_for_tune(self, tune: str) -> str:
        """Map a matrix `tune` label to the rung id that gets built.

        The matrix speaks in `baseline` / `tuned`; hostrun builds rung FILES. Passing the
        label straight through refused the build, and the stale binary already in
        build/ was flashed instead — every tuned run would have measured baseline.
        """
        if tune in self.rungs:
            return tune
        if tune == "baseline":
            roots = [i for i in self.formal if not self.rungs[i].parent]
            if len(roots) != 1:
                raise ValueError(f"the {self.chip} ladder needs exactly one root rung, "
                                 f"found {roots}")
            return roots[0]
        if tune == "tuned":
            return self.top()
        raise KeyError(f"tune {tune!r} is neither 'baseline', 'tuned', nor a rung id in "
                       f"the {self.chip} ladder")


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
        "rung_counts": {c: len(ladders[c].formal) for c in chips},
    }


_KCONFIG_DEF = re.compile(r"^\s*(?:menu)?config\s+([A-Za-z0-9_]+)\s*$", re.M)


def known_symbols(idf_path: str | Path, firmware_root: str | Path) -> set[str]:
    """Every CONFIG_ name defined by this ESP-IDF's Kconfig files or the bench's own.

    A name check only: it catches a symbol that does not exist at all (which Kconfig
    drops silently), not one whose dependencies are unmet — the post-build check in the
    hardware driver catches those."""
    files = [Path(idf_path) / "Kconfig"]               # compiler options live here
    files += list(Path(idf_path).glob("components/**/Kconfig*"))
    files += list(Path(firmware_root).glob("*/Kconfig*"))
    files += list(Path(firmware_root).glob("*/main/Kconfig*"))
    names: set[str] = set()
    for f in files:
        names |= {f"CONFIG_{n}" for n in _KCONFIG_DEF.findall(f.read_text(errors="ignore"))}
    return names


def symbols_not_applied(cfg: dict, sdkconfig_text: str) -> list[str]:
    """Symbols a rung asked for that the built sdkconfig does not carry.

    Kconfig drops an unknown name and replaces an out-of-range value with the default,
    both without an error, so a build can quietly be the parent rung under a new name.
    A symbol set to n may be absent: a disabled or non-existent option is the same thing.
    """
    built: dict[str, str] = {}
    for line in sdkconfig_text.splitlines():
        line = line.strip()
        if line.startswith("CONFIG_") and "=" in line:
            k, _, v = line.partition("=")
            built[k] = v
    missing = []
    for k, v in cfg.items():
        if v is False or v == "n":
            if built.get(k, "n") == "y":
                missing.append(k)
            continue
        if v is True or v == "y":
            want = "y"
        elif isinstance(v, int):
            want = str(v)
        else:
            want = f'"{v}"'
        if built.get(k) != want:
            missing.append(k)
    return missing


def _short(chip: str) -> str:
    return {"esp32s3": "s3", "esp32c5": "c5"}.get(chip, chip)
