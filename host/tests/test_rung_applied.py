"""A build must carry every symbol of the rung it was built for, or it is not flashed.

Kconfig drops an unknown symbol and clamps an out-of-range value without an error, so
without this check a candidate rung with a typo is built, flashed and measured as if it
were a new configuration while the binary is its parent's. An automated tuning loop
adds rungs nobody has built before, which is when this matters most."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench.control import ControlClient
from bench.drivers import HardwareDriver
from bench.fakeesp import FakeESP


class _Stub(HardwareDriver):
    def __init__(self, port, firmware_root, rungs_root):
        super().__init__(ips={"esp32c5": "127.0.0.1"}, firmware_root=firmware_root,
                         ports={"esp32c5": "cu.fake"}, host_ip="127.0.0.1",
                         rungs_root=rungs_root)
        self._clients["esp32c5"] = ControlClient("127.0.0.1", port, timeout=0.5)
        self.jobs = []

    def _hostrun(self, action, **params):
        self.jobs.append(action)
        return "=== exit 0"


def _tree(tmp_path, symbol_line: str, built: str):
    fw = tmp_path / "firmware" / "esp32c5"
    fw.mkdir(parents=True)
    (fw / "sdkconfig").write_text(built)
    rungs = tmp_path / "rungs" / "c5"
    rungs.mkdir(parents=True)
    (rungs / "r0-baseline.yaml").write_text("id: r0-baseline\nconfig: {}\n")
    (rungs / "t01-x.yaml").write_text(
        "id: t01-x\nparent: r0-baseline\nstudy: s\nconfig:\n  " + symbol_line + "\n")
    return tmp_path / "firmware", tmp_path / "rungs"


def test_a_rung_symbol_the_build_dropped_is_refused_before_flashing(tmp_path):
    fw, rungs = _tree(tmp_path, "CONFIG_ESP_WIFI_TASK_CORE_ID_0: y",
                      'CONFIG_IDF_TARGET="esp32c5"\n')
    drv = _Stub(16950, fw, rungs)
    with pytest.raises(RuntimeError, match="CONFIG_ESP_WIFI_TASK_CORE_ID_0"):
        drv.ensure_flashed("esp32c5", "synth", "t01-x")
    assert drv.jobs == ["esp_build"]          # built, never flashed


def test_a_build_carrying_the_rung_is_flashed(tmp_path):
    fw, rungs = _tree(tmp_path, "CONFIG_LWIP_TCP_SND_BUF_DEFAULT: 41760",
                      "CONFIG_LWIP_TCP_SND_BUF_DEFAULT=41760\n")
    esp = FakeESP("esp32c5", control_port=16960, rung="t01-x")
    esp.start()
    try:
        drv = _Stub(16960, fw, rungs)
        assert drv.ensure_flashed("esp32c5", "synth", "t01-x")["flashed"] is True
        assert drv.jobs == ["esp_build", "esp_flash"]
    finally:
        esp.shutdown()
