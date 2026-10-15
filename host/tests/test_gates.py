import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench import gates


def _pre(**kw):
    base = dict(associated=True, band="5", expected_band="5", rig_ceiling_mbps=95.0,
                offered_bps=20e6, rssi_dbm=-42, first_rssi_dbm=-42, ambient_ok=True,
                esp_reset_since_last=False, idf_version="v6.0.2",
                expected_idf="v6.0.2", proto_ok=True, parity_ok=True)
    base.update(kw)
    return base


def test_clean_context_passes():
    assert gates.pre_run(_pre()).ok


def test_offered_above_rig_ceiling_fails():
    r = gates.pre_run(_pre(offered_bps=120e6))
    assert not r.ok and any("rig ceiling" in f for f in r.failures)


def test_missing_rig_ceiling_fails():
    assert not gates.pre_run(_pre(rig_ceiling_mbps=None)).ok


def test_high_airtime_warns_but_does_not_fail():
    r = gates.pre_run(_pre(offered_bps=90e6))
    assert r.ok and r.warnings


def _post(**kw):
    base = dict(commanded_bps=20e6, achieved_bps=20e6, esp_reset_during=False,
                heap_min=100000, heap_floor=20000,
                metrics={"frames": 100, "goodput_bps": 20e6}, rig_ceiling_mbps=60.0,
                host_udp_drops=0)
    base.update(kw)
    return base


def test_a_heap_low_water_below_the_floor_warns_without_voiding_the_run():
    """The throughput number is still real; what it says is that this configuration nearly
    ran out of memory (the C5 went offline at 4.4 KB). The study decides what to do."""
    r = gates.post_run(_post(heap_min=4440))
    assert r.ok
    assert any("4440" in w and "heap" in w for w in r.warnings)


def test_rssi_drift_fails():
    assert not gates.pre_run(_pre(rssi_dbm=-52)).ok


def test_wrong_band_fails():
    assert not gates.pre_run(_pre(band="2.4")).ok


def test_parity_failure_refuses_the_run():
    r = gates.pre_run(_pre(parity_ok=False))
    assert not r.ok and any("parity" in f for f in r.failures)


def test_source_limited_warns_and_is_flagged_not_failed():
    ctx = dict(commanded_bps=40e6, achieved_bps=25e6, metrics={"frames": 10},
               rig_ceiling_mbps=95.0)
    r = gates.post_run(ctx)
    assert r.ok and any("source-limited" in w for w in r.warnings)
    assert gates.source_limited(ctx)


def test_goodput_above_ceiling_fails():
    r = gates.post_run(dict(commanded_bps=40e6, achieved_bps=40e6,
                            metrics={"frames": 10, "goodput_bps": 200e6},
                            rig_ceiling_mbps=95.0))
    assert not r.ok


def test_no_frames_fails():
    assert not gates.post_run(dict(commanded_bps=1e6, achieved_bps=1e6,
                                   metrics={"frames": 0})).ok
