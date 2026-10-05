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

    def __init__(self, ips: dict[str, str | None], firmware_root: str | Path,
                 ports: dict[str, str], agent_dir: str | Path = "_agent",
                 host_ip: str | None = None, interactive_band: bool = True,
                 hub_ports: dict[str, str] | None = None,
                 rungs_root: str | Path | None = None,
                 ap: str = rfmeta.IPHONE_AP, ssid: str | None = None):
        # An IP of None or "auto" is found by discovery on the host's /24: the access
        # point assigns addresses by DHCP, so they are not known in advance.
        self.ips = {k: (None if v in (None, "", "auto") else v) for k, v in ips.items()}
        self.ap = ap
        self.ssid = ssid          # when set, firmware is only built if it joins this network
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

    def host_ip(self) -> str:
        """The address the ESPs stream to.

        iPhone rig: the Mac's end of the iPhone USB link — not the default route, which
        is usually the Mac's own Wi-Fi and unreachable from the hotspot. Router rig: the
        Mac's address on the router's LAN, which is where the default route points. The
        iPhone link is ignored there even if a phone happens to be plugged in: an address
        on the phone's subnet would send every frame nowhere."""
        if self._host_ip:
            return self._host_ip
        if self.ap == rfmeta.IPHONE_AP:
            ip = rfmeta.iphone_usb_ip()
            if ip:
                return ip
        known = next((i for i in self.ips.values() if i), None)
        return _local_ip_toward(known or "8.8.8.8")

    def host_path(self, chip: str) -> dict:
        """How this Mac reaches a board: the second hop of the rig (rfmeta.host_path)."""
        return rfmeta.host_path(self.ips.get(chip), self.ap)

    FULL_SCAN_MIN_INTERVAL_S = 5.0   # a blind /24 scan is ~240 ARP broadcasts; ration them

    def rediscover(self, want: str | None = None) -> dict:
        """Find the managed boards on the host's /24 and update their addresses in
        place, so clients already handed out (the keepalive's) follow a new lease.

        Addresses known to exist are asked first: the boards' last addresses and
        everything already in this host's neighbour table. Only if a board is still
        missing (`want`, or any managed board when `want` is None) is the rest of the /24
        probed, and that blind scan is rationed — on a home network most of those
        addresses do not exist and each probe costs an ARP broadcast."""
        base = self.host_ip().rsplit(".", 1)[0]
        first = [ip for ip in self.ips.values() if ip]
        first += [ip for ip in rfmeta.lan_neighbours(base) if ip not in first]
        found = discover(first, timeout=0.6) if first else {}
        if want:
            missing = want not in found
        else:
            missing = not self.ips or any(c not in found for c in self.ips)
        now = time.monotonic()
        if missing and now - getattr(self, "_last_full_scan", -1e9) >= self.FULL_SCAN_MIN_INTERVAL_S:
            self._last_full_scan = now
            seen = set(first)
            rest = [a for a in rfmeta.subnet_hosts(self.host_ip()) if a not in seen]
            found = {**discover(rest), **found}
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
        """Make the next cells run on `band`.

        The band is pinned in the board's firmware (BENCH_BAND), so the next
        ensure_flashed() builds for it; a dual-band chip is rebuilt when the band
        changes. On the iPhone rig the access point also serves only one band at a time
        and the switch is a manual toggle: pause and say exactly what to change. Either
        way the pre-run gate verifies the band from the device rather than assuming
        (spec §3.6)."""
        band = str(band)
        if self._band == band:
            return
        if self.ap == rfmeta.IPHONE_AP:
            toggle = "ON (2.4 GHz)" if band == "2.4" else "OFF (5 GHz)"
            if self.interactive_band:
                input(f"\n>> Set Personal Hotspot -> Maximize Compatibility {toggle}, "
                      f"then press Enter. ")
            else:
                # Non-interactive: the band was set before the sweep started (one toggle
                # per unattended block). A wrong toggle fails loudly at the gate rather
                # than silently producing numbers for the other band.
                print(f">> assuming hotspot is already on {band} GHz "
                      f"(Maximize Compatibility {toggle})")
        elif self.ap == rfmeta.HOTSPOT_WIFI_AP:
            toggle = "ON" if band == "2.4" else "OFF"
            print(f">> band {band} GHz: the hotspot serves one band at a time — Personal "
                  f"Hotspot > Maximize Compatibility must be {toggle}. The band is also "
                  f"pinned in the firmware, and verified from each board.")
        else:
            print(f">> band {band} GHz: pinned in the firmware build; access point "
                  f"'{self.ap}' serves both bands")
        self._band = band

    def board_ssid(self, chip: str) -> str | None:
        """The network a chip's firmware is built to join (CONFIG_EXAMPLE_WIFI_SSID in
        firmware/<chip>/sdkconfig.local), or None if that cannot be read."""
        try:
            text = (self.firmware_root / chip / "sdkconfig.local").read_text()
        except OSError:
            return None
        m = re.search(r'^CONFIG_EXAMPLE_WIFI_SSID="([^"]*)"', text, re.M)
        return m.group(1) if m else None

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
            if chip in self.rediscover(chip):
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
        # The band is part of the build: the firmware pins the radio to it, because a
        # dual-band access point would otherwise let the chip choose. request_band() is
        # always called before the first run of a band, so it is known here.
        band = self._band
        want = (source, rung, band)
        if self._flashed.get(chip) == want:
            return {"flashed": False, "chip": chip, "rung": rung}
        self._flashed.pop(chip, None)
        if self.ssid:
            # The firmware can only ever join the one network it is built for, so
            # checking that before building is what enforces where the board ends up.
            built_for = self.board_ssid(chip)
            if built_for != self.ssid:
                raise RuntimeError(
                    f"firmware/{chip}/sdkconfig.local joins {built_for!r}, but this test "
                    f"must run on {self.ssid!r} — not building or flashing")
        build = {"target": chip, "rung": rung, "ingress": source}
        if band:
            build["band"] = band
        self._hostrun("esp_build", **build)
        self._hostrun("esp_flash", target=chip, port=self.ports[chip])
        self._await_boot(chip, rung, source, band)
        self._flashed[chip] = want
        return {"flashed": True, "chip": chip, "rung": rung}

    def _await_boot(self, chip: str, rung: str, source: str, band: str | None = None) -> dict:
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
                self.rediscover(chip)   # a reboot can come back on a new DHCP lease
                time.sleep(2.0)
                continue
            if h.get("rung") != rung or h.get("ingress") != source:
                raise RuntimeError(
                    f"{chip} answers with rung={h.get('rung')} ingress={h.get('ingress')}"
                    f", expected rung={rung} ingress={source} — the flash did not take")
            if band and str(h.get("band")) not in (str(band), "None", "unknown"):
                raise RuntimeError(
                    f"{chip} joined on band {h.get('band')} but this build pins band "
                    f"{band} — the band pin did not hold; measuring would mislabel the cell")
            return h
        raise TimeoutError(f"{chip} did not answer within {self.BOOT_TIMEOUT_S:.0f} s "
                           f"of flashing ({last})")

    def control(self, chip: str) -> ControlClient:
        """One client per chip, reused, so an address change found by rediscover()
        reaches everything holding it — notably the keepalive."""
        if chip not in self._clients:
            if not self.ips.get(chip):
                self.rediscover(chip)
            if not self.ips.get(chip):
                raise ControlError(f"{chip} not found on {self.host_ip()}/24 — is it "
                                   f"powered and joined to the access point?")
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
        body = f"action={action}\n" + "".join(f"{k}={v}\n" for k, v in params.items())
        (q / f"{jid}.job").write_text(body)
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
