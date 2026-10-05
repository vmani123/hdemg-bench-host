"""The dual-band router rig: the access point offers both bands under one SSID, so the
band of a cell is pinned in the firmware build, and the host's own Wi-Fi band is checked
so the two hops never share a channel unnoticed."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench import drivers, gates, rfmeta
from bench.control import ControlClient
from bench.drivers import HardwareDriver
from bench.fakeesp import FakeESP

BASE_CTX = {"associated": True, "band": "2.4", "expected_band": "2.4",
            "rig_ceiling_mbps": 60.0, "offered_bps": 4_000_000, "rssi_dbm": -50,
            "first_rssi_dbm": -50, "ambient_ok": True, "esp_reset_since_last": False,
            "idf_version": "v6", "expected_idf": "v6", "proto_ok": True, "parity_ok": True}


# -- the Mac's own link ------------------------------------------------------
def test_channel_text_is_parsed_into_band_channel_and_width():
    assert rfmeta.parse_channel("104 (5GHz, 80MHz)") == {"channel": 104, "band": "5", "width_mhz": 80}
    assert rfmeta.parse_channel("6 (2GHz, 20MHz)") == {"channel": 6, "band": "2.4", "width_mhz": 20}
    assert rfmeta.parse_channel("11") == {"channel": 11, "band": "2.4", "width_mhz": None}
    assert rfmeta.parse_channel(None) == {"channel": None, "band": None, "width_mhz": None}


def test_link_label_names_the_band_and_channel_of_a_wifi_host():
    assert rfmeta.link_label({"link": "wifi", "band": "5", "channel": 104}) == "wifi-5-ch104"
    assert rfmeta.link_label({"link": "ethernet", "dev": "en8"}) == "ethernet"
    assert rfmeta.link_label({"link": "usb"}) == "usb"
    assert rfmeta.link_label({}) == "unknown"


# -- the shared-band gate ----------------------------------------------------
def test_host_on_the_other_band_passes():
    g = gates.pre_run({**BASE_CTX, "host_link": "wifi", "host_band": "5"})
    assert g.ok, g.failures


def test_host_on_the_measured_band_is_refused():
    """Both hops on one channel: every frame crosses the same air twice."""
    g = gates.pre_run({**BASE_CTX, "host_link": "wifi", "host_band": "2.4"})
    assert not g.ok
    assert any("share" in f for f in g.failures)


def test_shared_band_can_be_knowingly_accepted_as_a_warning():
    g = gates.pre_run({**BASE_CTX, "host_link": "wifi", "host_band": "2.4",
                       "allow_shared_band": True})
    assert g.ok
    assert any("share" in w for w in g.warnings)


def test_wired_hosts_carry_no_band_and_always_pass():
    for link in ("ethernet", "usb", "loopback"):
        assert gates.pre_run({**BASE_CTX, "host_link": link, "host_band": None}).ok


def test_an_unreadable_host_band_is_a_warning_not_a_silent_pass():
    g = gates.pre_run({**BASE_CTX, "host_link": "wifi", "host_band": None})
    assert g.ok
    assert any("could not read" in w for w in g.warnings)


def test_a_board_on_the_wrong_band_is_still_refused():
    g = gates.pre_run({**BASE_CTX, "band": "5", "host_link": "wifi", "host_band": "5"})
    assert any("board is on band 5" in f for f in g.failures)


# -- which address the boards stream to --------------------------------------
def test_router_rig_ignores_an_iphone_that_happens_to_be_plugged_in(monkeypatch):
    """An address on the phone's subnet would send every frame nowhere."""
    monkeypatch.setattr(rfmeta, "iphone_usb_ip", lambda: "172.20.10.2")
    monkeypatch.setattr(drivers, "_local_ip_toward", lambda dest: "192.168.1.160")
    router = HardwareDriver(ips={}, firmware_root=".", ports={}, ap="verizon-router")
    phone = HardwareDriver(ips={}, firmware_root=".", ports={}, ap=rfmeta.IPHONE_AP)
    assert router.host_ip() == "192.168.1.160"
    assert phone.host_ip() == "172.20.10.2"


# -- the band is part of the build -------------------------------------------
class _StubHostrun(HardwareDriver):
    def __init__(self, port, **kw):
        super().__init__(ips={"esp32c5": "127.0.0.1"}, firmware_root=".",
                         ports={"esp32c5": "cu.fake"}, host_ip="127.0.0.1",
                         interactive_band=False, ap="verizon-router", **kw)
        self._clients["esp32c5"] = ControlClient("127.0.0.1", port, timeout=0.5)
        self.jobs = []

    def _hostrun(self, action, **params):
        self.jobs.append((action, params))
        return "=== exit 0"


def test_the_requested_band_is_built_into_the_firmware():
    esp = FakeESP("esp32c5", control_port=17010, rung="r0-baseline", band="2.4")
    esp.start()
    try:
        drv = _StubHostrun(17010)
        drv.request_band("2.4")
        assert drv.ensure_flashed("esp32c5", "synth", "baseline")["flashed"] is True
        assert drv.jobs[0] == ("esp_build", {"target": "esp32c5", "rung": "r0-baseline",
                                             "ingress": "synth", "band": "2.4"})
        # Same build, same band: nothing to do.
        assert drv.ensure_flashed("esp32c5", "synth", "baseline")["flashed"] is False
    finally:
        esp.shutdown()


def test_changing_band_rebuilds_even_though_the_rung_is_unchanged():
    """On a dual-band access point the firmware is the only thing that decides the band,
    so the 5 GHz block must not reuse the 2.4 GHz build."""
    esp = FakeESP("esp32c5", control_port=17020, rung="r0-baseline", band="2.4")
    esp.start()
    try:
        drv = _StubHostrun(17020)
        drv.request_band("2.4")
        drv.ensure_flashed("esp32c5", "synth", "baseline")
        esp.band = "5"                       # what the 5 GHz build would report
        drv.request_band("5")
        assert drv.ensure_flashed("esp32c5", "synth", "baseline")["flashed"] is True
        builds = [p for a, p in drv.jobs if a == "esp_build"]
        assert [b["band"] for b in builds] == ["2.4", "5"]
    finally:
        esp.shutdown()


def test_a_board_that_joined_the_other_band_is_refused_after_flashing():
    """The pin did not hold: measuring would file 5 GHz numbers under a 2.4 GHz cell."""
    esp = FakeESP("esp32c5", control_port=17030, rung="r0-baseline", band="5")
    esp.start()
    try:
        drv = _StubHostrun(17030)
        drv.request_band("2.4")
        with pytest.raises(RuntimeError, match="band pin did not hold"):
            drv.ensure_flashed("esp32c5", "synth", "baseline")
    finally:
        esp.shutdown()
