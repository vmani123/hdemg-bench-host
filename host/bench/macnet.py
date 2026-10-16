"""Put this Mac on the band a run is NOT measuring (docs/CAMPAIGN.md, "the rig").

The router offers the boards' network (one name, both bands) plus one band-locked
network per band for the Mac. Measuring 2.4 GHz, the Mac sits on the 5 GHz-only network;
measuring 5 GHz, on the 2.4 GHz-only one. The two hops then use different radios and
transmit at the same time, so the Mac's hop costs the board no airtime (test plan §10.2,
topology B).

macOS has no band setting of its own; it only picks among networks. So switching band
means switching network: `networksetup -setairportnetwork`, then waiting until the Mac
is really associated, has an address, and reports the expected band. networksetup's
exit code is not trusted — it returns 0 on some failures.
"""
from __future__ import annotations
import re
import subprocess
import time
from pathlib import Path

from . import rfmeta


class MacNetError(RuntimeError):
    pass


def current() -> dict:
    """The Mac's Wi-Fi association right now: ssid, band, channel, width, phy."""
    rfmeta._WIFI_CACHE["value"] = None                 # never a cached answer here
    w = rfmeta.host_wifi(max_age_s=0)
    dev = rfmeta.wifi_device()
    w["ssid"] = rfmeta.host_ssid(dev) or w.get("ssid")
    w["dev"] = dev
    w["ip"] = _ip(dev) if dev else None
    return w


def password_from_sdkconfig(path: str | Path) -> str | None:
    """The boards' Wi-Fi password (sdkconfig.local); the band-locked networks are set up
    with the same one, so it is not stored twice."""
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    m = re.search(r'^CONFIG_EXAMPLE_WIFI_PASSWORD="([^"]*)"', text, re.M)
    return m.group(1) if m else None


def switch(ssid: str, password: str | None = None, *, want_band: str | None = None,
           timeout_s: float = 90.0, log=print) -> dict:
    """Join `ssid` and wait until associated with an address (and on `want_band`, if
    given). Returns current(). Raises MacNetError if that does not happen in time."""
    now = current()
    if now.get("ssid") == ssid and now.get("ip") and \
            (want_band is None or str(now.get("band")) == str(want_band)):
        return now
    dev = now.get("dev") or rfmeta.wifi_device()
    if not dev:
        raise MacNetError("this Mac has no Wi-Fi interface")
    log(f">> moving this Mac to Wi-Fi network {ssid!r}"
        + (f" ({want_band} GHz)" if want_band else "") + " — the network drops for a moment")
    cmd = ["/usr/sbin/networksetup", "-setairportnetwork", dev, ssid]
    if password:
        cmd.append(password)
    for attempt in range(1, 4):
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        msg = (out.stdout + out.stderr).strip()
        deadline = time.monotonic() + timeout_s / 3
        while time.monotonic() < deadline:
            time.sleep(2.0)
            now = current()
            if now.get("ssid") == ssid and now.get("ip"):
                if want_band is None or str(now.get("band")) == str(want_band):
                    log(f">> this Mac is on {ssid!r}: {now.get('band')} GHz, channel "
                        f"{now.get('channel')}, {now.get('width_mhz')} MHz, {now.get('phy')}")
                    return now
        log(f">> join attempt {attempt} did not settle ({msg or 'no message'}); retrying")
    raise MacNetError(f"could not put this Mac on {ssid!r}"
                      + (f" at {want_band} GHz" if want_band else "")
                      + f"; it is on {now.get('ssid')!r} ({now.get('band')} GHz)")


def _ip(dev: str) -> str | None:
    try:
        out = subprocess.run(["/usr/sbin/ipconfig", "getifaddr", dev], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
