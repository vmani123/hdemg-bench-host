"""Environment metadata capture (spec §3.5).

Everything here is captured automatically and never typed by a human — a number whose
conditions were remembered rather than recorded is not evidence.
"""
from __future__ import annotations
import platform
import shutil
import subprocess
import time


def host_link() -> str:
    """How the receiving host reaches the AP. 'usb'/'ethernet' means the second hop is
    wired and costs no airtime (plan §10)."""
    if platform.system() != "Darwin":
        return "unknown"
    try:
        out = subprocess.run(["/usr/sbin/networksetup", "-listnetworkserviceorder"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    low = out.lower()
    if "iphone usb" in low:
        return "usb"
    if "ethernet" in low or "lan" in low:
        return "ethernet"
    return "wifi"


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
            shielded: bool = False) -> dict:
    """Assemble the `rf` block of a ledger record.

    RSSI, channel and the negotiated PHY rate come from the DEVICE, not the host: the
    host's own link is a different link. The PHY rate is also the evidence that settles
    plan §7.3b — whether the hotspot is actually offering 802.11ax.
    """
    st = device_stat or {}
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
        "mac_link": host_link(),
        "shielded": shielded,
        "rig_ceiling_mbps": rig_ceiling_mbps,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
