"""The knee is the headline number of every cell, so its aggregation rule is pinned here.

The rule: apply the loss threshold to the MEDIAN across repeats, not to each record.
Judging records individually lets one lucky repeat set the knee for the whole cell, which
inflates every derived quantity and leaves no trace in the output.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench.orchestrator import knee


def rec(load_mbps, loss_pct, good_mbps, valid=True):
    return {"offered_bps": load_mbps * 1e6, "valid": valid,
            "metrics": {"loss_pct": loss_pct, "goodput_bps": good_mbps * 1e6}}


def test_one_lucky_repeat_does_not_set_the_knee():
    recs = [rec(20, 0.0, 20), rec(20, 0.0, 20), rec(20, 0.0, 20),
            rec(40, 0.05, 40), rec(40, 9.0, 30), rec(40, 8.0, 31)]   # 1 of 3 passed
    assert knee(recs)["knee_offered_bps"] == 20e6


def test_a_load_that_passes_on_median_is_the_knee():
    recs = [rec(20, 0.0, 20)] * 3 + [rec(40, 0.02, 40), rec(40, 0.05, 39), rec(40, 9.0, 30)]
    k = knee(recs)
    assert k["knee_offered_bps"] == 40e6
    assert k["goodput_bps"] == 39e6           # median of 40, 39, 30


def test_goodput_is_the_median_not_an_arbitrary_repeat():
    recs = [rec(36, 0.0, 27.7), rec(36, 0.0, 31.4), rec(36, 0.0, 36.0)]
    assert knee(recs)["goodput_bps"] == 31.4e6


def test_spread_is_reported_so_disagreeing_repeats_are_visible():
    tight = knee([rec(20, 0.0, 20), rec(20, 0.0, 20), rec(20, 0.0, 20)])
    wide = knee([rec(20, 0.0, 10), rec(20, 0.0, 20), rec(20, 0.0, 30)])
    assert tight["goodput_mad_bps"] == 0.0
    assert wide["goodput_mad_bps"] == 10e6
    assert tight["repeats"] == wide["repeats"] == 3


def test_invalid_records_are_excluded():
    recs = [rec(20, 0.0, 20)] * 3 + [rec(40, 0.0, 40, valid=False)] * 3
    assert knee(recs)["knee_offered_bps"] == 20e6


def test_no_passing_load_returns_nothing_rather_than_guessing():
    k = knee([rec(20, 50.0, 5)] * 3)
    assert k["knee_offered_bps"] is None and k["goodput_bps"] is None
