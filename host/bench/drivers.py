"""Device drivers: one real, one simulated (spec §3.2, §8).

The simulated driver is not a toy. It is what lets the orchestrator, sweep, gates,
ledger, resume and report all be exercised in CI with no hardware, so bench time is
spent measuring radios rather than debugging Python.
"""
from __future__ import annotations
import re
import socket
import subprocess
import time
from pathlib import Path

from . import rfmeta
from .control import ControlClient, ControlError, discover
from .fakeesp import FakeESP
from .h7 import (DEFAULT_H7_PORT, FakeH7, H7Control, H7Error, fake_ingress_stat,
                 ingress_stat)
from .rungs import Ladder

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
        self.h7_faults: dict = {}          # tests inject what a broken rig would report
        self._h7 = None

    def host_ip(self) -> str:
        return "127.0.0.1"

    def ensure_h7(self) -> dict:
        return {"flashed": False}

    def h7(self):
        if self._h7 is None:
            self._h7 = FakeH7(self.h7_faults)
        return self._h7

    def ingress_stat(self, chip: str) -> dict:
        # The fake ESP was told which mode to run in, exactly as a real one is.
        payload = str(self._esps[chip].cfg.get("payload", "")) if chip in self._esps else ""
        faults = dict(self.h7_faults)
        if payload.startswith("lt:"):
            faults.setdefault("link_test", 1)
            faults.setdefault("seed", int(payload[3:], 0))
        return fake_ingress_stat(self.h7().stat(), faults)

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

    def recover(self, chip: str) -> bool:
        """Simulated power cycle: respawn the fake device."""
        self._respawn(chip)
        return True

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

    BOOT_TIMEOUT_S = 90.0      # flash -> boot -> associate -> DHCP -> control plane up
    REJOIN_TIMEOUT_S = 45.0    # how long recover() looks for a board that dropped off
    HOSTRUN_WAIT_S = 1800      # hostrun bounds set-target and build at 600 s each
    TREE_ACTIONS = ("esp_build", "esp_flash", "stm_build", "stm_flash")

    def __init__(self, ips: dict[str, str | None], firmware_root: str | Path,
                 ports: dict[str, str], agent_dir: str | Path = "_agent",
                 host_ip: str | None = None, interactive_band: bool = True,
                 hub_ports: dict[str, str] | None = None,
                 rungs_root: str | Path | None = None,
                 tree: str | None = None, h7_port: str = DEFAULT_H7_PORT):
        # An IP of None or "auto" is found by discovery on the host's /24: the hotspot
        # assigns addresses by DHCP, so they are not known in advance.
        self.ips = {k: (None if v in (None, "", "auto") else v) for k, v in ips.items()}
        self.firmware_root = Path(firmware_root)
        self.ports = ports
        self.agent_dir = Path(agent_dir)
        self._host_ip = None if host_ip in (None, "", "auto") else host_ip
        self.interactive_band = interactive_band
        self.hub_ports = hub_ports or {}
        self.rungs_root = Path(rungs_root) if rungs_root else \
            Path(__file__).resolve().parents[1] / "rungs"
        self._flashed: dict[str, tuple] = {}
        self._clients: dict[str, ControlClient] = {}
        self._ladders: dict[str, Ladder] = {}
        self._band = None
        # hostrun builds the repo root unless told to build a named worktree (work in
        # progress that must not touch the checkout a sweep is measuring from).
        self.tree = tree or None
        self.h7_port = h7_port
        self._h7: H7Control | None = None
        self._h7_ready = False

    def host_ip(self) -> str:
        """The address the ESPs stream to: the Mac's end of the iPhone USB link.

        Not the default route — that is usually the Mac's own Wi-Fi, which a device on
        the hotspot cannot reach, so every frame would go nowhere."""
        if self._host_ip:
            return self._host_ip
        ip = rfmeta.iphone_usb_ip()
        if ip:
            return ip
        known = next((i for i in self.ips.values() if i), None)
        return _local_ip_toward(known or "8.8.8.8")

    def rediscover(self) -> dict:
        """Find the managed boards on the host's /24 and update their addresses in
        place, so clients already handed out (the keepalive's) follow a new lease."""
        base = self.host_ip().rsplit(".", 1)[0]
        found = discover([f"{base}.{i}" for i in range(1, 255)])
        for chip, reply in found.items():
            if chip in self.ips:
                self.ips[chip] = reply["ip"]
                if chip in self._clients:
                    self._clients[chip].addr = (reply["ip"], self._clients[chip].addr[1])
        return found

    def rung_for(self, chip: str, tune: str) -> str:
        if chip not in self._ladders:
            self._ladders[chip] = Ladder.load(self.rungs_root, chip)
        return self._ladders[chip].rung_for_tune(tune)

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
        else:
            # Non-interactive: the band was set before the sweep started (one toggle per
            # unattended block). Announce it; the pre-run gate verifies the band from the
            # device itself, so a wrong toggle fails loudly rather than silently
            # producing numbers for the other band.
            print(f">> assuming hotspot is already on {band} GHz "
                  f"(Maximize Compatibility {toggle})")
        self._band = band

    def recover(self, chip: str) -> bool:
        """Get a board that stopped answering back on the control plane.

        Power-cycle it first if a uhubctl-capable hub port is configured. Either way,
        then look for it on the subnet: a board that was kicked off the hotspot rejoins
        on its own (the firmware retries forever) but may come back on a new DHCP lease.
        Without a hub, a truly wedged board still needs hands on RESET."""
        hub_port = self.hub_ports.get(chip)
        if hub_port:
            try:
                self._hostrun("power_cycle", hub_port=hub_port)
                time.sleep(12.0)        # boot + Wi-Fi association
                self._flashed.pop(chip, None)
            except (TimeoutError, OSError, RuntimeError) as e:
                print(f">> power cycle of {chip} failed: {e}")
        deadline = time.monotonic() + self.REJOIN_TIMEOUT_S
        while time.monotonic() < deadline:
            if chip in self.rediscover():
                print(f">> {chip} found again at {self.ips[chip]}")
                return True
            time.sleep(5.0)
        print(f">> {chip} is not answering and was not found on the subnet; "
              f"skipping this point")
        return False

    def band_of(self, chip: str) -> str:
        try:
            return str(self.control(chip).hello().get("band"))
        except Exception:                                    # noqa: BLE001
            return "unknown"

    def ensure_flashed(self, chip: str, source: str, tune: str) -> dict:
        rung = self.rung_for(chip, tune)
        want = (source, rung)
        if self._flashed.get(chip) == want:
            return {"flashed": False, "chip": chip, "rung": rung}
        self._flashed.pop(chip, None)
        self._hostrun("esp_build", target=chip, rung=rung, ingress=source)
        self._hostrun("esp_flash", target=chip, port=self.ports[chip])
        self._await_boot(chip, rung, source)
        self._flashed[chip] = want
        return {"flashed": True, "chip": chip, "rung": rung}

    # ---- Stage 2: the STM32H745 generator ------------------------------------------
    def h7(self) -> H7Control:
        if self._h7 is None:
            self._h7 = H7Control(self.h7_port)
        return self._h7

    def _tree_sha(self) -> str | None:
        """What hostrun's stm_build stamps into the firmware as fw_sha."""
        root = self.firmware_root.parent
        try:
            out = subprocess.run(["git", "-C", str(root), "describe", "--always", "--dirty"],
                                 capture_output=True, text=True, timeout=10)
            return out.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    def ensure_h7(self) -> dict:
        """Make sure the generator is running the firmware of the tree being measured.
        One image serves both links (the link is a runtime setting), so this flashes at
        most once per session — and not at all if the board already reports this build."""
        if self._h7_ready:
            return {"flashed": False}
        want = self._tree_sha()
        try:
            have = self.h7().hello().get("fw_sha")
        except H7Error:
            have = None
        flashed = False
        if have is None or want is None or have != want:
            self.h7().close()
            self._hostrun("stm_build", project="hdemg_h745", config="Release", core="all")
            self._hostrun("stm_flash", project="hdemg_h745", config="Release", core="cm4")
            self._hostrun("stm_flash", project="hdemg_h745", config="Release", core="cm7")
            time.sleep(1.5)
            have = self.h7().hello().get("fw_sha")
            flashed = True
            if want and have != want:
                raise RuntimeError(f"H7 reports fw_sha {have}, expected {want} — the "
                                   f"flash did not take")
        self._h7_ready = True
        return {"flashed": flashed, "fw_sha": have}

    def ingress_stat(self, chip: str) -> dict:
        self.control(chip)                       # makes sure the address is known
        return ingress_stat(self.ips[chip])

    def _await_boot(self, chip: str, rung: str, source: str) -> dict:
        """Wait for a freshly flashed board to rejoin and answer, then check it is
        running the build that was asked for (spec §3.2 step 2). A mismatch means the
        flash did not take, and measuring anyway would mislabel every point."""
        deadline = time.monotonic() + self.BOOT_TIMEOUT_S
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                h = self.control(chip).hello()
            except (ControlError, OSError) as e:
                last = e
                self.rediscover()       # a reboot can come back on a new DHCP lease
                time.sleep(2.0)
                continue
            if h.get("rung") != rung or h.get("ingress") != source:
                raise RuntimeError(
                    f"{chip} answers with rung={h.get('rung')} ingress={h.get('ingress')}"
                    f", expected rung={rung} ingress={source} — the flash did not take")
            return h
        raise TimeoutError(f"{chip} did not answer within {self.BOOT_TIMEOUT_S:.0f} s "
                           f"of flashing ({last})")

    def control(self, chip: str) -> ControlClient:
        """One client per chip, reused, so an address change found by rediscover()
        reaches everything holding it — notably the keepalive."""
        if chip not in self._clients:
            if not self.ips.get(chip):
                self.rediscover()
            if not self.ips.get(chip):
                raise ControlError(f"{chip} not found on {self.host_ip()}/24 — is it "
                                   f"powered and joined to the hotspot?")
            self._clients[chip] = ControlClient(self.ips[chip])
        return self._clients[chip]

    def _hostrun(self, action: str, **params) -> str:
        """Drop a job file for hostrun.sh, wait for its log, and fail on a non-zero
        exit. Ignoring the exit code is how a refused build used to be followed by a
        flash of whatever stale binary was already in build/."""
        q = self.agent_dir / "queue"
        o = self.agent_dir / "out"
        q.mkdir(parents=True, exist_ok=True)
        o.mkdir(parents=True, exist_ok=True)
        jid = f"{int(time.time()*1000)}"
        if self.tree and action in self.TREE_ACTIONS:
            params = {**params, "tree": self.tree}
        body = f"action={action}\n" + "".join(f"{k}={v}\n" for k, v in params.items())
        # Written under another name and renamed in: hostrun must never read half a job.
        tmp = q / f".{jid}.tmp"
        tmp.write_text(body)
        tmp.rename(q / f"{jid}.job")
        log = o / f"{jid}.log"
        for _ in range(self.HOSTRUN_WAIT_S):
            if log.exists():
                text = log.read_text()
                m = re.search(r"^=== exit (\d+)", text, re.M)
                if m:
                    if int(m.group(1)) != 0:
                        tail = "\n".join(text.splitlines()[-12:])
                        raise RuntimeError(f"hostrun {action} {params} failed "
                                           f"(exit {m.group(1)}):\n{tail}")
                    return text
            time.sleep(1.0)
        raise TimeoutError(f"hostrun action {action} did not complete — is hostrun.sh "
                           f"running and watching {q}?")


def _local_ip_toward(dest: str) -> str:
    """The local address the kernel would use to reach `dest` (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((dest, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def detect_git() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip() or "nogit"
    except OSError:
        return "nogit"
