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
