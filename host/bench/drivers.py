"""Device drivers: one real, one simulated (spec §3.2, §8).

The simulated driver is not a toy. It is what lets the orchestrator, sweep, gates,
ledger, resume and report all be exercised in CI with no hardware, so bench time is
spent measuring radios rather than debugging Python.
"""
from __future__ import annotations
import socket
import subprocess
import time
from pathlib import Path

from .control import ControlClient
from .fakeesp import FakeESP

CHIP_SHORT = {"esp32s3": "s3", "esp32c5": "c5"}


class SimDriver:
    """Loopback fake ESPs. Ceilings are chosen per (chip, band) to give the harness
    something with a real knee to find; they are NOT predictions."""

    DEFAULT_CEILINGS = {
        ("esp32c5", "5"):   {"ceiling_mbps": 46.0, "cpu_bound_mbps": None},
        ("esp32c5", "2.4"): {"ceiling_mbps": 24.0, "cpu_bound_mbps": None},
        ("esp32s3", "2.4"): {"ceiling_mbps": 22.0, "cpu_bound_mbps": None},
    }

    def __init__(self, bands: dict[str, str] | None = None,
                 ceilings: dict | None = None, base_port: int = 13340):
        self.ceilings = {**self.DEFAULT_CEILINGS, **(ceilings or {})}
        self.band = "5"
        self._esps: dict[str, FakeESP] = {}
        self._ports: dict[str, int] = {}
        self._base_port = base_port
        self.flashes = 0
        self._flashed: dict[str, tuple] = {}

    def host_ip(self) -> str:
        return "127.0.0.1"

    def request_band(self, band: str) -> None:
        self.band = str(band)
        for chip in list(self._esps):
            self._respawn(chip)

    def band_of(self, chip: str) -> str:
        return self.band

    def ensure_flashed(self, chip: str, source: str, tune: str) -> dict:
        want = (source, tune)
        if self._flashed.get(chip) != want:
            self._flashed[chip] = want
            self.flashes += 1
            self._respawn(chip)
        elif chip not in self._esps:
            self._respawn(chip)
        return {"flashed": True, "chip": chip, "rung": tune}

    def control(self, chip: str) -> ControlClient:
        if chip not in self._esps:
            self._respawn(chip)
        return ControlClient("127.0.0.1", self._ports[chip], timeout=2.0)

    def shutdown(self) -> None:
        for esp in self._esps.values():
            esp.shutdown()
        self._esps.clear()
        time.sleep(0.05)

    def _respawn(self, chip: str) -> None:
        old = self._esps.pop(chip, None)
        if old:
            old.shutdown()
            time.sleep(0.05)
        port = self._ports.setdefault(
            chip, self._base_port + len(self._ports))
        band = self.band if chip == "esp32c5" else "2.4"
        params = self.ceilings.get((chip, band), {"ceiling_mbps": 20.0,
                                                  "cpu_bound_mbps": None})
        source, tune = self._flashed.get(chip, ("synth", "baseline"))
        # An untuned build is slower — the baseline-vs-tuned delta the plan reports.
        scale = 0.62 if tune == "baseline" else 1.0
        esp = FakeESP(chip, control_port=port,
                      ceiling_mbps=params["ceiling_mbps"] * scale,
                      cpu_bound_mbps=params["cpu_bound_mbps"],
                      rung=tune, band=band,
                      fw_sha=f"sim{CHIP_SHORT.get(chip, chip)}",
                      seed=hash((chip, band, tune)) & 0xFFFF)
        esp.start()
        self._esps[chip] = esp


class HardwareDriver:
    """Real boards, reached over the control plane; builds and flashes go through
    hostrun.sh so nothing here ever runs a toolchain directly."""

    def __init__(self, ips: dict[str, str], firmware_root: str | Path,
                 ports: dict[str, str], agent_dir: str | Path = "_agent",
                 host_ip: str | None = None, interactive_band: bool = True):
        self.ips = ips
        self.firmware_root = Path(firmware_root)
        self.ports = ports
        self.agent_dir = Path(agent_dir)
        self._host_ip = host_ip
        self.interactive_band = interactive_band
        self._flashed: dict[str, tuple] = {}
        self._band = None

    def host_ip(self) -> str:
        if self._host_ip:
            return self._host_ip
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()

    def request_band(self, band: str) -> None:
        """The hotspot serves one band at a time and the switch is a manual toggle.
        Pause, say exactly what to change, then verify from the device rather than
        assuming (spec §3.6)."""
        band = str(band)
        if self._band == band:
            return
        toggle = "ON (2.4 GHz)" if band == "2.4" else "OFF (5 GHz)"
        if self.interactive_band:
            input(f"\n>> Set Personal Hotspot -> Maximize Compatibility {toggle}, "
                  f"then press Enter. ")
        self._band = band

    def band_of(self, chip: str) -> str:
        try:
            return str(self.control(chip).hello().get("band"))
        except Exception:                                    # noqa: BLE001
            return "unknown"

    def ensure_flashed(self, chip: str, source: str, tune: str) -> dict:
        want = (source, tune)
        if self._flashed.get(chip) == want:
            return {"flashed": False, "chip": chip, "rung": tune}
        self._hostrun("esp_build", target=chip, rung=tune, ingress=source)
        self._hostrun("esp_flash", target=chip, port=self.ports[chip])
        self._flashed[chip] = want
        time.sleep(3.0)
        return {"flashed": True, "chip": chip, "rung": tune}

    def control(self, chip: str) -> ControlClient:
        return ControlClient(self.ips[chip])

    def _hostrun(self, action: str, **params) -> str:
        """Drop a job file for hostrun.sh and wait for its log."""
        q = self.agent_dir / "queue"
        o = self.agent_dir / "out"
        q.mkdir(parents=True, exist_ok=True)
        o.mkdir(parents=True, exist_ok=True)
        jid = f"{int(time.time()*1000)}"
        body = f"action={action}\n" + "".join(f"{k}={v}\n" for k, v in params.items())
        (q / f"{jid}.job").write_text(body)
        log = o / f"{jid}.log"
        for _ in range(600):
            if log.exists() and "=== exit" in log.read_text():
                return log.read_text()
            time.sleep(1.0)
        raise TimeoutError(f"hostrun action {action} did not complete — is hostrun.sh running?")


def detect_git() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or "nogit"
    except OSError:
        return "nogit"
