"""Validity gates (spec §6).

A tripped gate marks a run invalid and re-queues it. The point is that a run which was
not measuring what it claimed never gets averaged into a median — the silent-corruption
failure mode is the one that produces a confident wrong answer.
"""
from __future__ import annotations
from dataclasses import dataclass

RSSI_DRIFT_DB = 3.0
SOURCE_RATE_TOLERANCE = 0.05     # achieved vs commanded
# Datagrams the HOST may drop at its own socket during a UDP run before the run is
# refused. The counter is system-wide, so a handful from unrelated software is tolerated;
# 50 datagrams keeps the host's share of the loss under the 0.1 % knee criterion at every
# load in the sweep (a datagram carries at most 30 frames).
HOST_UDP_DROP_LIMIT = 50
AIRTIME_WARN_PCT = 80.0


@dataclass
class GateResult:
    ok: bool
    failures: list[str]
    warnings: list[str]

    def __bool__(self) -> bool:
        return self.ok


def pre_run(ctx: dict) -> GateResult:
    """ctx: band, expected_band, associated, rig_ceiling_mbps, offered_bps, rssi_dbm,
    first_rssi_dbm, ambient_ok, esp_reset_since_last, idf_version, expected_idf,
    proto_ok, parity_ok, host_link, host_band, allow_shared_band."""
    f: list[str] = []
    w: list[str] = []

    if not ctx.get("associated", False):
        f.append("device is not associated with the access point")
    if str(ctx.get("band")) != str(ctx.get("expected_band")):
        f.append(f"board is on band {ctx.get('band')}, expected {ctx.get('expected_band')}")

    # The second hop (access point -> this host) must not share the first hop's air.
    # On a dual-band router the host sits on the OTHER band or on a wire; if it is on the
    # cell's band, every frame crosses one channel twice and every number is roughly
    # halved. Wired links and the iPhone USB link carry no band and always pass.
    if ctx.get("host_link") == "wifi":
        hb = ctx.get("host_band")
        if hb is None:
            w.append("could not read this host's own Wi-Fi band — cannot confirm it is "
                     "off the band being measured")
        elif str(hb) == str(ctx.get("expected_band")):
            msg = (f"this host is on Wi-Fi band {hb}, the band being measured — both hops "
                   f"share one channel's airtime; move the host to the other band or wire it")
            (w if ctx.get("allow_shared_band") else f).append(msg)

    ceiling = ctx.get("rig_ceiling_mbps")
    if ceiling in (None, 0):
        f.append("no rig ceiling measured for this band — measure it before trusting a run")
    else:
        offered_mbps = float(ctx.get("offered_bps", 0)) / 1e6
        if offered_mbps > float(ceiling):
            f.append(f"offered {offered_mbps:.1f} Mbit/s exceeds measured rig ceiling "
                     f"{float(ceiling):.1f} — the rig, not the device, would be the limit")
        elif offered_mbps > 0.8 * float(ceiling):
            w.append(f"offered load is {100*offered_mbps/float(ceiling):.0f}% of the rig "
                     f"ceiling; CSMA/CA efficiency degrades above ~{AIRTIME_WARN_PCT:.0f}%")

    rssi, first = ctx.get("rssi_dbm"), ctx.get("first_rssi_dbm")
    if rssi is not None and first is not None and abs(rssi - first) > RSSI_DRIFT_DB:
        f.append(f"RSSI drifted {abs(rssi-first):.1f} dB from this cell's first repeat")
    if ctx.get("ambient_ok") is False:
        f.append("ambient RF scan differs materially from this cell's first repeat")
    if ctx.get("esp_reset_since_last"):
        f.append("device reset since the previous run")
    if ctx.get("idf_version") and ctx.get("expected_idf") and \
            ctx["idf_version"] != ctx["expected_idf"]:
        f.append(f"IDF {ctx['idf_version']} != expected {ctx['expected_idf']}")
    if ctx.get("proto_ok") is False:
        f.append("control-plane protocol version mismatch")
    if ctx.get("parity_ok") is False:
        f.append("measurement-contract parity check failed — cross-chip comparison refused")
    return GateResult(not f, f, w)


def post_run(ctx: dict) -> GateResult:
    """ctx: commanded_bps, achieved_bps, esp_reset_during, heap_min, heap_floor,
    metrics (RunMetrics-as-dict), rig_ceiling_mbps, host_udp_drops."""
    f: list[str] = []
    w: list[str] = []

    cmd = float(ctx.get("commanded_bps") or 0)
    ach = ctx.get("achieved_bps")
    if cmd > 0 and ach is not None:
        if abs(float(ach) - cmd) / cmd > SOURCE_RATE_TOLERANCE:
            # Not a failure: a legitimate and important observation. The cell is
            # source-limited and must be labelled so it is not read as a radio ceiling.
            w.append(f"source-limited: achieved {float(ach)/1e6:.1f} vs commanded "
                     f"{cmd/1e6:.1f} Mbit/s")
    if ctx.get("esp_reset_during"):
        f.append("device reset during the run")
    hm, hf = ctx.get("heap_min"), ctx.get("heap_floor")
    if hm is not None and hf is not None and hm < hf:
        f.append(f"heap low-water {hm} crossed floor {hf}")

    m = ctx.get("metrics") or {}
    if m.get("error"):
        f.append(f"receiver error: {m['error']}")
    if m.get("frames", 0) == 0:
        f.append("no frames received")
    ceiling = ctx.get("rig_ceiling_mbps")
    if ceiling and m.get("goodput_bps", 0) / 1e6 > float(ceiling) * 1.05:
        f.append("goodput exceeded the measured rig ceiling — ceiling is stale, re-measure both")
    if m.get("reorder_count", 0) > 0.01 * max(1, m.get("frames", 1)):
        w.append(f"{m['reorder_count']} reordered frames (>1%) — reordering is not loss "
                 f"but can still break a downstream decoder")

    # The instrument must not be the thing that loses the data. If this host discarded
    # datagrams at its own socket during the window, the loss figure describes the host
    # (a full buffer, or a content filter holding data back), not the device or the air.
    hd = ctx.get("host_udp_drops")
    if hd is not None and hd > 0:
        msg = (f"this host discarded {hd} datagrams at its own socket during the run "
               f"(full socket buffer / content filter) — the loss figure is not the device's")
        (f if hd > HOST_UDP_DROP_LIMIT else w).append(msg)
    return GateResult(not f, f, w)


def source_limited(ctx: dict) -> bool:
    cmd = float(ctx.get("commanded_bps") or 0)
    ach = ctx.get("achieved_bps")
    if cmd <= 0 or ach is None:
        return False
    return abs(float(ach) - cmd) / cmd > SOURCE_RATE_TOLERANCE
