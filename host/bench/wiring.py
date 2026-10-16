"""The Stage 2 harness as wired (docs/CAMPAIGN_PROTOCOL.md §2), and the wiring check.

One wire per pin, nothing shared between boards. WIRES is the single source of truth:
the wiring "hello world" (`bench wiring`) checks the bench against it, and FakeWiring
simulates it so the fake H7 and fake ESPs can answer `pins` commands the way the real
harness would, including the faults the check exists to find.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Wire:
    n: int            # wire number, as on the wiring sheet
    h7: str           # STM32H745 pin, e.g. "PB2"
    chip: str         # "esp32s3" | "esp32c5"
    gpio: int         # ESP GPIO number
    role: str         # what the wire carries (both roles where the H7 switches)
    zio: str          # Nucleo connector position


WIRES: tuple[Wire, ...] = (
    Wire(1, "PB2", "esp32s3", 12, "QSPI CLK", "CN10-15"),
    Wire(2, "PG6", "esp32s3", 10, "QSPI CS", "CN10-13"),
    Wire(3, "PD11", "esp32s3", 11, "QSPI D0", "CN10-23"),
    Wire(4, "PD12", "esp32s3", 13, "QSPI D1", "CN10-21"),
    Wire(5, "PE2", "esp32s3", 14, "QSPI D2", "CN10-25"),
    Wire(6, "PD13", "esp32s3", 9, "QSPI D3", "CN10-19"),
    Wire(7, "PG9", "esp32s3", 7, "READY", "CN7-20"),
    Wire(8, "PF10", "esp32c5", 6, "QSPI CLK", "CN10-9"),
    Wire(9, "PD2", "esp32c5", 10, "SDIO CMD / QSPI CS", "CN8-12"),
    Wire(10, "PC9", "esp32c5", 7, "SDIO D1 / QSPI D0", "CN8-4"),
    Wire(11, "PF9", "esp32c5", 2, "QSPI D1", "CN9-28"),
    Wire(12, "PF7", "esp32c5", 5, "QSPI D2", "CN9-26"),
    Wire(13, "PF6", "esp32c5", 4, "QSPI D3", "CN10-7"),
    Wire(14, "PG10", "esp32c5", 3, "READY", "CN8-14"),
    Wire(15, "PC12", "esp32c5", 9, "SDIO CLK", "CN8-10"),
    Wire(16, "PC8", "esp32c5", 8, "SDIO D0", "CN8-2"),
    Wire(17, "PC10", "esp32c5", 14, "SDIO D2", "CN8-6"),
    Wire(18, "PC11", "esp32c5", 13, "SDIO D3", "CN8-8"),
)

H7_PINS: tuple[str, ...] = tuple(w.h7 for w in WIRES)


def esp_pins(chip: str) -> list[int]:
    """The link GPIOs of one board, in wire order."""
    return [w.gpio for w in WIRES if w.chip == chip]


def wire_for_h7(pin: str) -> Wire | None:
    return next((w for w in WIRES if w.h7 == pin), None)


def wire_for_esp(chip: str, gpio: int) -> Wire | None:
    return next((w for w in WIRES if w.chip == chip and w.gpio == gpio), None)


class FakeWiring:
    """A simulated harness. Each end drives a pin or leaves it as an input with a
    pull-down; a pin reads 1 only if something on its net drives it high.

    faults (for tests of the check itself), by wire number:
      {"open": [n, ...]}            the wire is missing
      {"swap": [(a, b), ...]}       wires a and b are crossed at the ESP end
      {"short": [(a, b), ...]}      wires a and b touch each other
      {"stuck_high": [n, ...]}      the net is tied high
    """

    def __init__(self, wires: tuple[Wire, ...] = WIRES, faults: dict | None = None):
        self.faults = faults or {}
        swap = {}
        for a, b in self.faults.get("swap", []):
            swap[a], swap[b] = b, a
        # net id per wire after the faults; a net joins one H7 pin and one ESP pin
        self._h7_net: dict[str, int] = {}
        self._esp_net: dict[tuple[str, int], int] = {}
        opened = set(self.faults.get("open", []))
        by_n = {w.n: w for w in wires}
        for w in wires:
            self._h7_net[w.h7] = w.n
            if w.n in opened:
                continue                       # the ESP end is not connected to anything
            esp_end = by_n[swap.get(w.n, w.n)]
            self._esp_net[(esp_end.chip, esp_end.gpio)] = w.n
        self._merged: dict[int, int] = {}
        for a, b in self.faults.get("short", []):
            self._merged[b] = a
        self._stuck = set(self.faults.get("stuck_high", []))
        self.h7_driven: dict[str, int] = {}
        self.esp_driven: dict[tuple[str, int], int] = {}

    def _net(self, n: int | None) -> int | None:
        while n in self._merged:
            n = self._merged[n]
        return n

    def _net_level(self, net: int | None) -> int:
        if net is None:
            return 0
        if any(self._net(n) == net for n in self._stuck):
            return 1
        for pin, lvl in self.h7_driven.items():
            if self._net(self._h7_net.get(pin)) == net and lvl:
                return 1
        for key, lvl in self.esp_driven.items():
            if self._net(self._esp_net.get(key)) == net and lvl:
                return 1
        return 0

    # -- the H7 end ------------------------------------------------------------------
    def h7_drive(self, pin: str, level: int | None) -> None:
        if level is None:
            self.h7_driven.pop(pin, None)
        else:
            self.h7_driven[pin] = int(bool(level))

    def h7_release_all(self) -> None:
        self.h7_driven.clear()

    def h7_level(self, pin: str) -> int:
        if pin in self.h7_driven:
            return self.h7_driven[pin]
        return self._net_level(self._net(self._h7_net.get(pin)))

    # -- an ESP end ------------------------------------------------------------------
    def esp_drive(self, chip: str, gpio: int, level: int | None) -> None:
        if level is None:
            self.esp_driven.pop((chip, gpio), None)
        else:
            self.esp_driven[(chip, gpio)] = int(bool(level))

    def esp_release_all(self, chip: str) -> None:
        for k in [k for k in self.esp_driven if k[0] == chip]:
            del self.esp_driven[k]

    def esp_level(self, chip: str, gpio: int) -> int:
        if (chip, gpio) in self.esp_driven:
            return self.esp_driven[(chip, gpio)]
        return self._net_level(self._net(self._esp_net.get((chip, gpio))))


# ---- the wiring "hello world" (docs/CAMPAIGN_PROTOCOL.md §4) ------------------------
def _levels(reply: dict) -> dict:
    return {str(k): int(v) for k, v in (reply.get("levels") or {}).items()}


def _high(levels: dict) -> set:
    return {k for k, v in levels.items() if v}


def check(h7, esps: dict, wires: tuple[Wire, ...] = WIRES) -> dict:
    """Walk every wire in both directions. `h7` answers `pins ...` (H7Control or
    FakeH7); `esps` maps chip -> a ControlClient-like object answering `pins ...`. Both
    ESPs must run synth builds and the H7 must have no link open.

    Returns {"ok": bool, "rows": [one per wire], "baseline_high": [...]} where each row
    says what the H7 end and the ESP end saw and gives a verdict: ok, open, swapped with
    #n, shorted to #n, stuck high, or one-way (only one direction worked)."""
    by_esp = {(w.chip, w.gpio): w for w in wires}
    by_h7 = {w.h7: w for w in wires}
    rows: list[dict] = []
    baseline: list[str] = []
    try:
        h7.pins("in")
        for c in esps.values():
            c.pins("in")
        base_esp: set = set()
        for chip, c in esps.items():
            for g in sorted(_high(_levels(c.pins("read"))), key=int):
                baseline.append(f"{chip} GPIO{g}")
                base_esp.add((chip, int(g)))
        base_h7 = _high(_levels(h7.pins("read")))
        baseline += [f"H7 {p}" for p in sorted(base_h7)]

        fwd: dict[int, set] = {}
        fwd_low_stuck: dict[int, bool] = {}
        for w in wires:
            h7.pins("drive", w.h7, 1)
            seen = set()
            for chip, c in esps.items():
                seen |= {(chip, int(g)) for g in _high(_levels(c.pins("read")))}
            h7.pins("drive", w.h7, 0)
            lo = esps[w.chip].pins("read") if w.chip in esps else {}
            fwd_low_stuck[w.n] = bool(_levels(lo).get(str(w.gpio), 0))
            h7.pins("in")
            fwd[w.n] = seen
        rev: dict[int, set] = {}
        for w in wires:
            c = esps.get(w.chip)
            if c is None:
                rev[w.n] = set()
                continue
            c.pins("drive", w.gpio, 1)
            rev[w.n] = _high(_levels(h7.pins("read")))
            c.pins("drive", w.gpio, 0)
            c.pins("in")

        for w in wires:
            want_esp, want_h7 = (w.chip, w.gpio), w.h7
            f = fwd[w.n] - (base_esp - {want_esp})
            r = rev[w.n] - (base_h7 - {want_h7})
            f_ok = f == {want_esp} and not fwd_low_stuck[w.n]
            r_ok = r == {want_h7}
            if f_ok and r_ok:
                verdict = "ok"
            elif fwd_low_stuck[w.n] or f"H7 {w.h7}" in baseline or \
                    f"{w.chip} GPIO{w.gpio}" in baseline:
                verdict = "stuck high"
            elif not f and not r:
                verdict = "open"
            elif want_esp not in f and len(f) == 1:
                other = by_esp.get(next(iter(f)))
                verdict = f"swapped with #{other.n}" if other else \
                    f"lands on {next(iter(f))[0]} GPIO{next(iter(f))[1]}"
            elif want_esp in f and len(f) > 1:
                others = sorted(by_esp[x].n for x in f - {want_esp} if x in by_esp)
                verdict = "shorted to " + ", ".join(f"#{n}" for n in others)
            elif f_ok != r_ok:
                verdict = "one-way: " + ("H7 -> ESP only" if f_ok else "ESP -> H7 only")
            else:
                verdict = "wrong"
            rows.append({"n": w.n, "h7": w.h7, "zio": w.zio, "esp": f"{w.chip} GPIO{w.gpio}",
                         "role": w.role,
                         "h7_drives_esp_sees": sorted(f"{c} GPIO{g}" for c, g in f),
                         "esp_drives_h7_sees": sorted(r), "verdict": verdict})
    finally:
        for c in esps.values():
            try:
                c.pins("release")
            except Exception:                                # noqa: BLE001
                pass
        try:
            h7.pins("hiz")
        except Exception:                                    # noqa: BLE001
            pass
    ok = bool(rows) and all(r["verdict"] == "ok" for r in rows) and not baseline
    return {"ok": ok, "rows": rows, "baseline_high": baseline}


def render(result: dict) -> str:
    """A table for the terminal and the summary."""
    out = ["| # | Nucleo | H7 pin | ESP | carries | H7 drives -> ESP sees | "
           "ESP drives -> H7 sees | verdict |", "|---|---|---|---|---|---|---|---|"]
    for r in result.get("rows", []):
        out.append(f"| {r['n']} | {r['zio']} | {r['h7']} | {r['esp']} | {r['role']} | "
                   f"{', '.join(r['h7_drives_esp_sees']) or '—'} | "
                   f"{', '.join(r['esp_drives_h7_sees']) or '—'} | "
                   f"{'ok' if r['verdict'] == 'ok' else '**' + r['verdict'] + '**'} |")
    if result.get("baseline_high"):
        out.append("")
        out.append("Pins reading HIGH with nothing driving them (should all be low): "
                   + ", ".join(result["baseline_high"]))
    return "\n".join(out)
