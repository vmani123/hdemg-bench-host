"""The hotspot probe: TCP with a larger send buffer against the stock build, plus UDP,
on a rig that is ENFORCED — the named Wi-Fi network or nothing."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench import gates, rfmeta
from bench.control import ControlClient
from bench.drivers import HardwareDriver
from bench.fakeesp import FakeESP
from bench.ledger import Ledger
from bench.orchestrator import Matrix, Orchestrator
from bench.rungs import Ladder

HOST = pathlib.Path(__file__).resolve().parents[1]
PROBE = HOST / "matrices" / "hotspot-probe.yaml"
RUNGS = HOST / "rungs"

CTX = {"associated": True, "band": "2.4", "expected_band": "2.4", "rig_ceiling_mbps": 60.0,
       "offered_bps": 4_000_000, "host_link": "wifi", "host_band": "2.4",
       "allow_shared_band": True}


# -- the matrix --------------------------------------------------------------
def test_the_probe_matrix_names_its_network_and_its_runs():
    m = Matrix.load(PROBE)
    assert m.ssid == "VikPhone" and m.ap == rfmeta.HOTSPOT_WIFI_AP
    assert m.allow_shared_band is True
    runs = m.expand(["2.4"])
    assert len(runs) == 72                          # 2 chips x 3 cells x 6 loads x 2 repeats
    assert sorted({r.offered_bps // 1_000_000 for r in runs}) == [4, 12, 20, 28, 36, 44]
    cells = {r.cell_id for r in runs}
    for chip in ("esp32c5", "esp32s3"):
        assert f"s1-{chip}-2.4-synth-tcp-baseline" in cells
        assert f"s1-{chip}-2.4-synth-tcp-p1-tcp-sndbuf" in cells
        assert f"s1-{chip}-2.4-synth-udp-baseline" in cells
    assert not any("udp-p1" in c for c in cells), "UDP does not depend on the TCP buffer"


def test_the_probe_rung_changes_only_the_send_buffer_and_leaves_tuned_alone():
    for chip, tuned in (("esp32c5", "r2-iram"), ("esp32s3", "r2-core-split")):
        lad = Ladder.load(RUNGS, chip)
        assert lad.rung_for_tune("p1-tcp-sndbuf") == "p1-tcp-sndbuf"
        base, probe = lad.resolve("r0-baseline"), lad.resolve("p1-tcp-sndbuf")
        assert {k: v for k, v in probe.items() if k not in base} == \
            {"CONFIG_LWIP_TCP_SND_BUF_DEFAULT": 46080}
        assert all(probe[k] == v for k, v in base.items())
        # A side branch off the baseline must not change what `tuned` means.
        assert lad.rung_for_tune("tuned") == tuned
        assert lad.rung_for_tune("baseline") == "r0-baseline"


# -- the network is enforced: host side --------------------------------------
def test_a_host_on_another_network_is_refused():
    g = gates.pre_run({**CTX, "expected_ssid": "VikPhone", "host_ssid": "CMU-SECURE"})
    assert not g.ok
    assert any("'CMU-SECURE'" in f and "'VikPhone'" in f for f in g.failures)


def test_a_host_whose_network_cannot_be_read_is_refused_too():
    assert not gates.pre_run({**CTX, "expected_ssid": "VikPhone", "host_ssid": None}).ok


def test_a_host_on_the_named_network_passes_with_the_shared_channel_as_a_warning():
    g = gates.pre_run({**CTX, "expected_ssid": "VikPhone", "host_ssid": "VikPhone"})
    assert g.ok, g.failures
    assert any("share" in w for w in g.warnings)


def test_matrices_without_a_named_network_are_not_affected():
    assert gates.pre_run({**CTX, "host_ssid": "anything"}).ok


def test_the_matrix_switches_the_shared_band_refusal_to_a_warning(tmp_path):
    m = Matrix.load(PROBE)
    orc = Orchestrator(m, object(), Ledger(tmp_path / "r.jsonl"))
    assert orc.allow_shared_band is True


# -- the network is enforced: board side -------------------------------------
class _StubHostrun(HardwareDriver):
    def __init__(self, port, firmware_root, ssid):
        super().__init__(ips={"esp32c5": "127.0.0.1"}, firmware_root=firmware_root,
                         ports={"esp32c5": "cu.fake"}, host_ip="127.0.0.1",
                         interactive_band=False, ap=rfmeta.HOTSPOT_WIFI_AP, ssid=ssid)
        self._clients["esp32c5"] = ControlClient("127.0.0.1", port, timeout=0.5)
        self.jobs = []

    def _hostrun(self, action, **params):
        self.jobs.append((action, params))
        return "=== exit 0"


def _firmware(tmp_path, ssid):
    d = tmp_path / "firmware" / "esp32c5"
    d.mkdir(parents=True)
    (d / "sdkconfig.local").write_text(
        f'CONFIG_EXAMPLE_CONNECT_WIFI=y\nCONFIG_EXAMPLE_WIFI_SSID="{ssid}"\n'
        'CONFIG_EXAMPLE_WIFI_PASSWORD="not-a-real-password"\n')
    return tmp_path / "firmware"


def test_firmware_configured_for_another_network_is_not_built_or_flashed(tmp_path):
    drv = _StubHostrun(17610, _firmware(tmp_path, "Verizon_SLD967"), "VikPhone")
    drv.request_band("2.4")
    with pytest.raises(RuntimeError, match="must run on 'VikPhone'"):
        drv.ensure_flashed("esp32c5", "synth", "baseline")
    assert drv.jobs == [], "nothing may reach the runner"


def test_firmware_configured_for_the_named_network_is_built(tmp_path):
    esp = FakeESP("esp32c5", control_port=17620, rung="p1-tcp-sndbuf", band="2.4")
    esp.start()
    try:
        drv = _StubHostrun(17620, _firmware(tmp_path, "VikPhone"), "VikPhone")
        drv.request_band("2.4")
        assert drv.ensure_flashed("esp32c5", "synth", "p1-tcp-sndbuf")["flashed"] is True
        assert drv.jobs[0] == ("esp_build", {"target": "esp32c5", "rung": "p1-tcp-sndbuf",
                                             "ingress": "synth", "band": "2.4"})
    finally:
        esp.shutdown()


def test_a_missing_credentials_file_is_refused_not_guessed(tmp_path):
    (tmp_path / "firmware" / "esp32c5").mkdir(parents=True)
    drv = _StubHostrun(17630, tmp_path / "firmware", "VikPhone")
    with pytest.raises(RuntimeError, match="joins None"):
        drv.ensure_flashed("esp32c5", "synth", "baseline")


# -- discovery follows the real subnet ---------------------------------------
def test_a_hotspot_slash_28_is_scanned_as_14_addresses_not_254(monkeypatch):
    ifconfig = ("en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n"
                "\tinet 172.20.10.2 netmask 0xfffffff0 broadcast 172.20.10.15\n")
    monkeypatch.setattr(rfmeta, "_run", lambda cmd, timeout=15.0: ifconfig)
    hosts = rfmeta.subnet_hosts("172.20.10.2")
    assert hosts[0] == "172.20.10.1" and hosts[-1] == "172.20.10.14" and len(hosts) == 14


def test_an_unknown_or_wide_netmask_falls_back_to_the_slash_24(monkeypatch):
    monkeypatch.setattr(rfmeta, "_run", lambda cmd, timeout=15.0: "")
    assert len(rfmeta.subnet_hosts("192.168.1.160")) == 254
    wide = "\tinet 10.0.5.9 netmask 0xffff0000 broadcast 10.0.255.255\n"
    monkeypatch.setattr(rfmeta, "_run", lambda cmd, timeout=15.0: wide)
    assert len(rfmeta.subnet_hosts("10.0.5.9")) == 254
