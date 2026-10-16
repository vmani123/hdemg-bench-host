"""The wiring hello world against a simulated harness, with each fault it exists to find."""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench.wiring import WIRES, FakeWiring, H7_PINS, check, esp_pins, render   # noqa: E402


class H7End:
    def __init__(self, fw):
        self.fw = fw

    def pins(self, *w):
        if w[0] in ("in", "hiz"):
            self.fw.h7_release_all()
        elif w[0] == "drive":
            self.fw.h7_release_all()
            self.fw.h7_drive(w[1], int(w[2]))
        return {"ok": 1, "levels": {p: self.fw.h7_level(p) for p in H7_PINS}}


class EspEnd:
    def __init__(self, fw, chip):
        self.fw, self.chip = fw, chip

    def pins(self, *w):
        if w[0] in ("in", "release"):
            self.fw.esp_release_all(self.chip)
        elif w[0] == "drive":
            self.fw.esp_release_all(self.chip)
            self.fw.esp_drive(self.chip, int(w[1]), int(w[2]))
        return {"ok": True, "levels": {str(g): self.fw.esp_level(self.chip, g)
                                       for g in esp_pins(self.chip)}}


def _run(faults=None):
    fw = FakeWiring(faults=faults)
    esps = {"esp32s3": EspEnd(fw, "esp32s3"), "esp32c5": EspEnd(fw, "esp32c5")}
    return check(H7End(fw), esps)


def _verdict(res, n):
    return next(r["verdict"] for r in res["rows"] if r["n"] == n)


def test_correct_harness_passes_every_wire():
    res = _run()
    assert res["ok"], render(res)
    assert len(res["rows"]) == len(WIRES) == 18


@pytest.mark.parametrize("faults,n,expect", [
    ({"open": [3]}, 3, "open"),
    ({"swap": [(11, 12)]}, 11, "swapped with #12"),
    ({"short": [(15, 16)]}, 15, "shorted to #16"),
    ({"stuck_high": [8]}, 8, "stuck high"),
])
def test_each_fault_is_named(faults, n, expect):
    res = _run(faults)
    assert not res["ok"]
    assert _verdict(res, n) == expect
    assert "| ok |" in render(res)          # the other wires still read ok
