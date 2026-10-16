"""The H7's wire-once pin map from the host's side (docs/CAMPAIGN_PROTOCOL.md §2-4):
`cfg target=`, the `pins` verb, and the fake generator acting on a simulated harness.

The fake must answer exactly as the firmware does, and the firmware's own tables must
match the contract, or the wiring check would pass on a bench that is wired wrong.
"""
import re
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench.h7 import H7_PIN_ROLES, FakeH7, H7Control, H7Error
from bench.wiring import H7_PINS, WIRES, FakeWiring, esp_pins

H7_SRC = (pathlib.Path(__file__).resolve().parents[2] /
          "firmware" / "stm32h745" / "hdemg_h745" / "CM7" / "Core" / "Src")


# -- the firmware's tables against the contract ------------------------------------
def test_firmware_pin_table_matches_the_wiring_map_and_the_host():
    """linkpins.c is what `pins list` reports and what every link open releases."""
    src = (H7_SRC / "linkpins.c").read_text()
    rows = re.findall(r'\{\s*"(P[A-K]\d+)",\s*GPIO([A-K]),\s*GPIO_PIN_(\d+),\s*"([^"]+)"\s*\}', src)
    assert [r[0] for r in rows] == list(H7_PINS)                 # all 18, in wire order
    for name, port, num, role in rows:
        assert name == f"P{port}{num}"                           # the name is the pin
        assert H7_PIN_ROLES[name] == role


def _qset(src, which):
    body = re.search(r"static const qset_t QSET_%s = \{(.*?)\n\};" % which, src, re.S).group(1)
    pins = {k: (f"P{port}{num}", int(af)) for k, port, num, af in re.findall(
        r"\.(\w+)\s*=\s*\{\s*GPIO([A-K]),\s*GPIO_PIN_(\d+),\s*GPIO_AF(\d+)_QUADSPI\s*\}", body)}
    cs = re.search(r"\.cs_port = GPIO([A-K]), \.cs_pin = GPIO_PIN_(\d+)", body)
    rdy = re.search(r"\.rdy_port = GPIO([A-K]), \.rdy_pin = GPIO_PIN_(\d+), \.rdy_irq = (\w+)", body)
    return (pins, f"P{cs.group(1)}{cs.group(2)}" if cs else None,
            (f"P{rdy.group(1)}{rdy.group(2)}", rdy.group(3)))


def test_firmware_qspi_pin_sets_and_alternate_functions():
    """The AF numbers as read from the CubeMX database (GPIO-STM32H747_gpio_v1_0_Modes.xml)."""
    src = (H7_SRC / "link_qspi.c").read_text()
    s3, s3_cs, s3_rdy = _qset(src, "S3")
    assert s3 == {"clk": ("PB2", 9), "io0": ("PD11", 9), "io1": ("PD12", 9),
                  "io2": ("PE2", 9), "io3": ("PD13", 9), "ncs": ("PG6", 10)}
    assert s3_cs is None and s3_rdy == ("PG9", "EXTI9_5_IRQn")
    c5, c5_cs, c5_rdy = _qset(src, "C5")
    # no "ncs": the peripheral's NCS is not routed for the C5; chip select is PD2, by hand
    assert c5 == {"clk": ("PF10", 9), "io0": ("PC9", 9), "io1": ("PF9", 10),
                  "io2": ("PF7", 9), "io3": ("PF6", 9)}
    assert c5_cs == "PD2" and c5_rdy == ("PG10", "EXTI15_10_IRQn")


# -- cfg target= ------------------------------------------------------------------
def test_target_defaults_to_s3_and_is_reported_by_hello_and_cfg():
    h7 = FakeH7()
    assert h7.hello()["target"] == "s3" and h7.hello()["cfg"]["target"] == "s3"
    assert h7.cfg(link="qspi", target="c5")["target"] == "c5"
    assert h7.hello()["target"] == "c5"
    assert h7.cfg(target="s3")["target"] == "s3"


def test_sdio_always_means_the_c5():
    h7 = FakeH7()
    assert h7.cfg(link="sdio")["target"] == "c5"
    assert h7.cfg(link="qspi")["target"] == "s3"         # the QSPI choice itself is kept


def test_a_bad_target_is_refused():
    with pytest.raises(H7Error, match="target must be s3 or c5"):
        FakeH7().cfg(target="esp32")


# -- pins on a simulated harness -----------------------------------------------------
def test_pins_list_names_all_18_with_their_roles():
    r = FakeH7().pins("list")
    assert list(r["pins"]) == list(H7_PINS)
    assert r["pins"]["PD2"] == "c5_sdio_cmd/c5_qspi_cs" and r["pins"]["PG9"] == "s3_ready"


def test_each_h7_pin_reaches_exactly_its_esp_gpio():
    """§4 step 2 against a correct harness: the fake does what the check expects."""
    w = FakeWiring()
    h7 = FakeH7(wiring=w)
    for wire in WIRES:
        r = h7.pins("drive", wire.h7, 1)
        assert r["levels"] == {p: int(p == wire.h7) for p in H7_PINS}
        for chip in ("esp32s3", "esp32c5"):
            for g in esp_pins(chip):
                assert w.esp_level(chip, g) == int((chip, g) == (wire.chip, wire.gpio)), (wire, chip, g)
        h7.pins("drive", wire.h7, 0)
        assert w.esp_level(wire.chip, wire.gpio) == 0
    assert h7.pins("hiz") == {"ok": True} and not w.h7_driven


def test_an_esp_gpio_driven_high_shows_on_exactly_its_h7_pin():
    """§4 step 3: the H7 holds every link pin as a pulled-down input."""
    w = FakeWiring()
    h7 = FakeH7(wiring=w)
    w.esp_drive("esp32c5", 3, 1)                                 # C5 READY -> PG10
    levels = h7.pins("in")["levels"]
    assert [p for p, v in levels.items() if v] == ["PG10"]
    w.esp_drive("esp32c5", 3, 0)
    assert not any(h7.pins("read")["levels"].values())


def test_a_crossed_pair_shows_up():
    w = FakeWiring(faults={"swap": [(3, 4)]})                     # S3 D0 and D1 crossed
    h7 = FakeH7(wiring=w)
    h7.pins("drive", "PD11", 1)
    assert w.esp_level("esp32s3", 13) == 1 and w.esp_level("esp32s3", 11) == 0


def test_hi_z_pins_read_zero_like_an_analog_pin_does():
    w = FakeWiring()
    h7 = FakeH7(wiring=w)
    w.esp_drive("esp32s3", 12, 1)
    assert not any(h7.pins("read")["levels"].values())          # never configured: analog
    assert h7.pins("in")["levels"]["PB2"] == 1
    h7.pins("hiz")
    assert not any(h7.pins("read")["levels"].values())


def test_pin_names_are_case_insensitive_and_bad_arguments_are_refused():
    h7 = FakeH7()
    assert h7.pins("drive", "pg10", "1")["levels"]["PG10"] == 1
    with pytest.raises(H7Error, match="not a link pin"):
        h7.pins("drive", "PA5", 1)
    with pytest.raises(H7Error, match="level must be 0 or 1"):
        h7.pins("drive", "PB2", 2)
    with pytest.raises(H7Error, match="unknown pins command"):
        h7.pins("release")
    with pytest.raises(H7Error):
        h7.pins()


def test_pins_are_refused_during_a_run_but_listing_is_not():
    h7 = FakeH7()
    h7.cfg(rate_bps=4_000_000, dur_s=1, link="qspi")
    h7.start()
    for words in (("hiz",), ("in",), ("read",), ("drive", "PB2", 1)):
        with pytest.raises(H7Error, match="a run is active"):
            h7.pins(*words)
    assert len(h7.pins("list")["pins"]) == 18
    h7.stop()
    assert "levels" in h7.pins("in")


@pytest.mark.parametrize("opener", ["probe", "start"])
def test_a_link_open_releases_whatever_pins_left_driven(opener):
    w = FakeWiring()
    h7 = FakeH7(wiring=w)
    h7.pins("drive", "PF10", 1)
    assert w.esp_level("esp32c5", 6) == 1
    h7.cfg(rate_bps=4_000_000, dur_s=1, link="qspi", target="c5")
    getattr(h7, opener)()
    assert not w.h7_driven and w.esp_level("esp32c5", 6) == 0
    h7.stop()


# -- the serial client -----------------------------------------------------------------
class _Wire:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self._pending = []

    def write(self, data):
        self.sent.append(data.decode().strip())
        self._pending = list(self.replies.pop(0)) if self.replies else []

    def readline(self, timeout):
        return self._pending.pop(0) if self._pending else None


def test_client_sends_pins_commands_and_returns_the_reply():
    w = _Wire([[b'{"ok":true,"levels":{"PB2":1,"PG6":0}}'], [b'{"ok":true}']])
    c = H7Control(transport=w)
    assert c.pins("drive", "PB2", 1)["levels"]["PB2"] == 1
    assert c.pins("hiz") == {"ok": True}
    assert w.sent == ["pins drive PB2 1", "pins hiz"]


def test_client_treats_ok_0_as_a_refusal():
    """The contract writes replies as "ok":1 / "ok":0; the firmware sends true/false."""
    w = _Wire([[b'{"ok":0,"err":"a link is open; close it first"}']])
    with pytest.raises(H7Error, match="a link is open"):
        H7Control(transport=w).pins("in")


def test_client_hello_tolerates_the_target_field():
    w = _Wire([[b'{"ok":true,"proto":1,"chip":"stm32h745","fw_sha":"abc","target":"c5",'
                b'"cfg":{"link":"qspi","target":"c5"}}']])
    assert H7Control(transport=w).hello()["target"] == "c5"
