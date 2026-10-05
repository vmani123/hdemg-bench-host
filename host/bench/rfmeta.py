"""Environment metadata capture (spec §3.5).

Everything here is captured automatically and never typed by a human — a number whose
conditions were remembered rather than recorded is not evidence.

THE RIG. The boards join an access point over Wi-Fi; that is the hop being measured.
The second hop, access point -> this Mac, must not cost the first one any airtime:

  * iPhone hotspot: the Mac is wired to the phone over USB ("iPhone USB" interface).
  * A dual-band router (the current bench): the Mac is on the router's OTHER band, or
    wired to it. The router offers both bands at once, so the band of a cell is pinned
    in the board's firmware build, and the Mac's own band is read here and checked by
    the pre-run gate — if both hops were on one band every frame would cross the same
    air twice and every number would be roughly halved.
"""
from __future__ import annotations
import json
import platform
import shutil
import subprocess
import time

IPHONE_AP = "iphone-hotspot"            # Mac wired to the phone over USB
HOTSPOT_WIFI_AP = "iphone-hotspot-wifi"  # Mac on the phone's Wi-Fi: both hops share a channel
HOTSPOT_APS = (IPHONE_AP, HOTSPOT_WIFI_AP)


def _run(cmd: list[str], timeout: float = 15.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def iphone_usb_ip() -> str | None:
    """IPv4 address of the Mac's 'iPhone USB' interface, or None if it is not up."""
    if platform.system() != "Darwin":
        return None
    lines = _run(["/usr/sbin/networksetup", "-listallhardwareports"]).splitlines()
    dev = None
    for i, line in enumerate(lines):
        if line.strip() == "Hardware Port: iPhone USB" and i + 1 < len(lines):
            dev = lines[i + 1].partition(":")[2].strip()
    if not dev:
        return None
    return _run(["/usr/sbin/ipconfig", "getifaddr", dev]).strip() or None


def host_udp_drops() -> int | None:
    """Datagrams THIS HOST has discarded because a receiving socket was full, since boot
    (system-wide counter; None where it cannot be read).

    Sampled before and after a UDP run: a rise means the host threw data away before
    the harness read it, so the run's loss figure is not the device's. It happens with a
    fast reader too — a socket content filter (VPN / endpoint-security software) holds
    data back while it inspects it, and the kernel counts what does not fit as exactly
    this."""
    if platform.system() != "Darwin":
        return None
    for line in _run(["/usr/sbin/netstat", "-s", "-p", "udp"]).splitlines():
        if "dropped due to full socket buffers" in line:
            head = line.strip().split()[0]
            return int(head) if head.isdigit() else None
    return None


def content_filters_active() -> int | None:
    """How many socket content filters are attached on this host (None if unknown).
    Non-zero means every datagram passes through third-party inspection first."""
    if platform.system() != "Darwin":
        return None
    out = _run(["/usr/sbin/sysctl", "-n", "net.cfil.active_count"]).strip()
    return int(out) if out.isdigit() else None


def wifi_device() -> str | None:
    """BSD name of this Mac's Wi-Fi interface (usually en0)."""
    if platform.system() != "Darwin":
        return None
    lines = _run(["/usr/sbin/networksetup", "-listallhardwareports"]).splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "Hardware Port: Wi-Fi" and i + 1 < len(lines):
            return lines[i + 1].partition(":")[2].strip() or None
    return None


def host_ssid(dev: str | None = None) -> str | None:
    """The Wi-Fi network this Mac is associated to right now, or None. Read fresh every
    time (it is what a matrix's `ssid:` is enforced against) and needs no privileges."""
    dev = dev or wifi_device()
    if not dev:
        return None
    for line in _run(["/usr/sbin/ipconfig", "getsummary", dev]).splitlines():
        k, _, v = line.partition(" : ")
        if k.strip() == "SSID":
            return v.strip() or None
    return None


def subnet_hosts(host_ip: str) -> list[str]:
    """Every usable address on the subnet host_ip sits in, from the interface's own
    netmask — a phone hotspot hands out a /28 (14 addresses), a home router a /24.
    Nothing wider than a /24 is ever scanned."""
    import ipaddress
    prefix = 24
    for line in _run(["/sbin/ifconfig"]).splitlines():
        p = line.split()
        if len(p) >= 4 and p[0] == "inet" and p[1] == host_ip and p[2] == "netmask":
            try:
                prefix = max(24, bin(int(p[3], 16)).count("1"))
            except ValueError:
                prefix = 24
    net = ipaddress.ip_network(f"{host_ip}/{prefix}", strict=False)
    return [str(h) for h in net.hosts()]


def lan_neighbours(prefix: str) -> list[str]:
    """Addresses under `prefix` ("192.168.1") that this host has actually resolved a MAC
    for — hosts known to exist. Discovery asks these first: a datagram to a resolved
    neighbour needs no ARP lookup, so it can neither stall nor add broadcast traffic."""
    out: list[str] = []
    for line in _run(["/usr/sbin/arp", "-an"]).splitlines():
        # "? (192.168.1.165) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]"
        if "(incomplete)" in line or " at " not in line:
            continue
        ip = line.partition("(")[2].partition(")")[0]
        if ip.startswith(prefix + ".") and not ip.endswith(".255") and ip not in out:
            out.append(ip)
    return out


def route_interface(dest_ip: str) -> str | None:
    """The interface this Mac uses to reach dest_ip (the one the stream arrives on)."""
    if platform.system() != "Darwin":
        return None
    for line in _run(["/sbin/route", "-n", "get", dest_ip]).splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "interface":
            return v.strip() or None
    return None


def interface_type(dev: str) -> str:
    """'wifi', 'ethernet' or 'unknown' for a BSD interface name."""
    for line in _run(["/usr/sbin/ipconfig", "getsummary", dev]).splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "InterfaceType":
            return {"WiFi": "wifi", "Ethernet": "ethernet"}.get(v.strip(), "unknown")
    return "unknown"


def parse_channel(text: str | None) -> dict:
    """'104 (5GHz, 80MHz)' -> {'channel': 104, 'band': '5', 'width_mhz': 80}."""
    out: dict = {"channel": None, "band": None, "width_mhz": None}
    if not text:
        return out
    head = str(text).split()[0].rstrip(",")
    if head.isdigit():
        out["channel"] = int(head)
        out["band"] = "2.4" if out["channel"] <= 14 else "5"
    low = str(text).lower().replace(" ", "")
    if "2ghz" in low or "2.4ghz" in low:
        out["band"] = "2.4"
    elif "6ghz" in low:
        out["band"] = "6"
    elif "5ghz" in low:
        out["band"] = "5"
    for part in low.replace("(", ",").replace(")", ",").split(","):
        if part.endswith("mhz") and part[:-3].isdigit():
            out["width_mhz"] = int(part[:-3])
    return out


_WIFI_CACHE: dict = {"at": 0.0, "value": None}
WIFI_CACHE_S = 120.0


def host_wifi(max_age_s: float = WIFI_CACHE_S) -> dict:
    """The Mac's OWN Wi-Fi association: ssid, channel, band, width, phy, signal.

    Needs no sudo but takes a few seconds, so the answer is cached: a run holds for a
    minute, and a Mac that changes band is caught by the run after next at the latest."""
    now = time.monotonic()
    if _WIFI_CACHE["value"] is not None and now - _WIFI_CACHE["at"] < max_age_s:
        return _WIFI_CACHE["value"]
    info: dict = {"associated": False}
    if platform.system() == "Darwin":
        try:
            data = json.loads(_run(["/usr/sbin/system_profiler", "SPAirPortDataType", "-json"],
                                   timeout=30.0) or "{}")
            for itf in (data.get("SPAirPortDataType") or [{}])[0].get(
                    "spairport_airport_interfaces", []):
                cur = itf.get("spairport_current_network_information")
                if not cur:
                    continue
                info = {"associated": True, "dev": itf.get("_name"), "ssid": cur.get("_name"),
                        "phy": cur.get("spairport_network_phymode"),
                        "signal_noise": cur.get("spairport_signal_noise"),
                        **parse_channel(cur.get("spairport_network_channel"))}
                break
        except (ValueError, IndexError, AttributeError):
            info = {"associated": False}
    _WIFI_CACHE.update(at=now, value=info)
    return info


def host_path(dest_ip: str | None, ap: str = "") -> dict:
    """How this Mac reaches the boards: {'link': 'usb'|'wifi'|'ethernet'|'unknown', ...}
    plus, for Wi-Fi, the band and channel of the Mac's own association.

    'usb' is reported only for the iPhone rig, and only when the iPhone USB interface
    actually has an address — the service merely existing says nothing about topology."""
    if platform.system() != "Darwin":
        return {"link": "unknown"}
    if ap == IPHONE_AP and iphone_usb_ip():
        return {"link": "usb"}
    dev = route_interface(dest_ip) if dest_ip else None
    if not dev:
        return {"link": "unknown"}
    kind = interface_type(dev)
    path: dict = {"link": kind, "dev": dev}
    if kind == "wifi":
        w = host_wifi()
        path.update({k: w.get(k) for k in ("ssid", "channel", "band", "width_mhz", "phy")})
        path["ssid"] = host_ssid(dev) or path.get("ssid")     # fresh, not the cached one
    return path


def link_label(path: dict) -> str:
    """One word for the ledger: 'usb', 'ethernet', 'wifi-5-ch104', 'unknown'.
    ('not-usb' in older records meant the iPhone USB link was down.)"""
    if path.get("link") == "wifi":
        return f"wifi-{path.get('band') or '?'}-ch{path.get('channel') or '?'}"
    return str(path.get("link") or "unknown")


def host_link(dest_ip: str | None = None, ap: str = IPHONE_AP) -> str:
    return link_label(host_path(dest_ip, ap))


def wifi_scan() -> dict:
    """Ambient RF picture. Compared between repeats of a cell; a material change
    invalidates the run rather than quietly widening the spread."""
    if platform.system() != "Darwin" or not shutil.which("wdutil"):
        return {"available": False}
    try:
        out = subprocess.run(["sudo", "-n", "wdutil", "info"],
                             capture_output=True, text=True, timeout=15)
        return {"available": out.returncode == 0, "raw": out.stdout[:8000]}
    except (OSError, subprocess.SubprocessError):
        return {"available": False}


def collect(*, band: str, ap: str, rig_ceiling_mbps: float | None,
            device_stat: dict | None = None, idf_version: str | None = None,
            shielded: bool = False, host_path_info: dict | None = None) -> dict:
    """Assemble the `rf` block of a ledger record.

    RSSI, channel and the negotiated PHY rate come from the DEVICE, not the host: the
    host's own link is a different link. The PHY rate is also the evidence that settles
    plan §7.3b — whether the access point is actually offering 802.11ax. The host's link
    is recorded too (mac_link / host_path) because it is the second hop: on a router rig
    it must be on the other band, or wired.
    """
    st = device_stat or {}
    path = host_path_info if host_path_info is not None else host_path(None, ap)
    return {
        "band": band,
        "channel": st.get("channel"),
        "width_mhz": st.get("width_mhz"),
        "rssi_dbm": st.get("rssi"),
        "phy_rate": st.get("phy_rate"),
        "idf_version": idf_version,
        "tx_power": st.get("tx_power", "max"),
        "power_save": "off",
        "ap": ap,
        "mac_link": link_label(path),
        "host_path": path,
        "host_content_filters": content_filters_active(),
        "shielded": shielded,
        "rig_ceiling_mbps": rig_ceiling_mbps,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
