"""Validity gates (spec §6).

A tripped gate marks a run invalid and re-queues it. The point is that a run which was
not measuring what it claimed never gets averaged into a median — the silent-corruption
failure mode is the one that produces a confident wrong answer.
"""
from __future__ import annotations
from dataclasses import dataclass

RSSI_DRIFT_DB = 3.0
SOURCE_RATE_TOLERANCE = 0.05     # achieved vs commanded
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
    proto_ok, parity_ok."""
    f: list[str] = []
    w: list[str] = []

    if not ctx.get("associated", False):
        f.append("device is not associated with the access point")
    if str(ctx.get("band")) != str(ctx.get("expected_band")):
        f.append(f"hotspot on band {ctx.get('band')}, expected {ctx.get('expected_band')}")

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
    metrics (RunMetrics-as-dict), rig_ceiling_mbps."""
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
    return GateResult(not f, f, w)


def ingress(ctx: dict) -> GateResult:
    """Stage 2 wired ingress (guide §8). ctx: h7 (the generator's run summary), esp (the
    ESP's ingress counters), h7_error (why the generator did not start, if it did not).

    Corruption on the wire is a RIG FAULT, not a result: a run with any of it is invalid.
    Frames the generator had to drop because the link gave it no credit are a RESULT —
    the link could not keep up at that load — and are labelled, like source_limited."""
    f: list[str] = []
    w: list[str] = []
    h7, esp = ctx.get("h7") or {}, ctx.get("esp") or {}

    if ctx.get("h7_error"):
        f.append(f"generator did not start: {ctx['h7_error']}")
        return GateResult(False, f, w)
    if not h7:
        f.append("no run summary from the H7 generator — the offered load is unverified")
    if not esp:
        f.append("no ingress counters from the ESP — link integrity is unverified")
    if f:
        return GateResult(False, f, w)

    if esp.get("init_err"):
        f.append(f"ESP ingress link failed to initialise (err {esp['init_err']})")
    bad = {k: esp.get(k, 0) for k in ("len_errors", "magic_errors", "seq_backwards",
                                      "payload_errors") if esp.get(k, 0)}
    if bad:
        f.append("link corruption seen by the ESP: "
                 + ", ".join(f"{k}={v}" for k, v in sorted(bad.items())))
    for k in ("link_errors", "credit_errors"):
        if h7.get(k, 0):
            f.append(f"H7 link reported {k}={h7[k]}")
    # Every gap the ESP sees between consecutive frames must be a frame the generator
    # itself dropped at its ring. Any more than that went missing on the wire.
    drops, gaps = int(h7.get("ring_drops", 0)), int(esp.get("seq_gaps", 0))
    if gaps > drops:
        f.append(f"{gaps - drops} frames lost on the link (ESP saw {gaps} missing, "
                 f"the generator dropped {drops})")

    frames = int(h7.get("frames", 0))
    if drops:
        w.append(f"ingress-limited: the link refused {drops} of {frames} frames "
                 f"({100.0 * drops / max(1, frames):.2f}%) — dropped at the H7 ring")
    if h7.get("ready_timeouts", 0):
        w.append(f"{h7['ready_timeouts']} waits of more than 10 ms for link credit")
    if esp.get("pool_starved", 0):
        w.append(f"ESP buffer pool ran dry {esp['pool_starved']} times — the sink, "
                 f"not the link, was pushing back")
    return GateResult(not f, f, w)


def ingress_limited(h7: dict | None) -> bool:
    return bool(h7 and int(h7.get("ring_drops", 0)) > 0)


def source_limited(ctx: dict) -> bool:
    cmd = float(ctx.get("commanded_bps") or 0)
    ach = ctx.get("achieved_bps")
    if cmd <= 0 or ach is None:
        return False
    return abs(float(ach) - cmd) / cmd > SOURCE_RATE_TOLERANCE
