"""bench tune — the one-lever-at-a-time climber behind the max-throughput study.

The decisions are tested against a fake runner that writes ledger records from a known
performance model, so every keep/reject below is checkable by hand. The plumbing through
the real orchestrator and fake ESPs is covered by the last test."""
import json
import time
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
import yaml

from bench import tune
from bench.ledger import Ledger
from bench.orchestrator import Matrix
from bench.rungs import Ladder

HOST = pathlib.Path(__file__).resolve().parents[1]


def _plan(tmp_path, climbs, **over) -> pathlib.Path:
    raw = {
        "study": "max-tput", "ap": "verizon-router", "ssid": None,
        "expected_idf": "v6.0-test", "frame_bytes": 270,
        "repeats": 2, "hold_s": 20, "discard_s": 3, "transports": ["tcp", "udp"],
        "saturate_mbps": 60, "gain_pct": 2.0, "stop_after_misses": 3,
        "climbs": climbs, **over,
    }
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(raw, sort_keys=False))
    return p


def _rungs(tmp_path) -> pathlib.Path:
    root = tmp_path / "rungs"
    for short, chip in (("s3", "esp32s3"), ("c5", "esp32c5")):
        (root / short).mkdir(parents=True)
        (root / short / "r0-baseline.yaml").write_text(
            f"id: r0-baseline\napplies_to: {chip}\nconfig: {{}}\n")
    return root


def _cand(cid, **config):
    return {"id": cid, "lever": cid, "why": f"test {cid}",
            "config": config or {f"CONFIG_{cid.upper().replace('-', '_')}": "y"}}


class FakeRunner:
    """Writes one valid ledger record per expanded run, like a radio: goodput is the
    rung's capacity from `model` {(chip, rung): {"tcp": Mbit/s, "udp": Mbit/s}}, or 97 %
    of the offered load if that is less. Repeats differ by +-`jitter`. `phy` maps a rung
    to the phy_rate it reports."""

    def __init__(self, model, jitter=0.1, invalid=(), phy=None):
        self.model, self.jitter, self.invalid = model, jitter, set(invalid)
        self.phy = phy or {}
        self.calls = []

    def __call__(self, matrix_path, ledger_path, log_path):
        self.calls.append(pathlib.Path(matrix_path).name)
        m = Matrix.load(matrix_path)
        led = Ledger(ledger_path)
        done = led.completed_keys()
        for r in m.expand():
            if r.key in done:
                continue
            perf = self.model.get((r.chip, r.tune))
            if perf is None or r.tune in self.invalid:
                continue              # a failed build: nothing reaches the ledger
            good = min(perf[r.transport], 0.97 * r.offered_bps / 1e6) + \
                (self.jitter if r.repeat % 2 else -self.jitter)
            led.append({
                "run_id": f"{r.cell_id}__{r.offered_bps}__r{r.repeat}",
                "timestamp": "2026-10-12T00:00:00Z", "git_sha": "test", "fw_sha": "test",
                "cell_id": r.cell_id, "target": r.chip, "variant": r.tune,
                "source": r.source, "link": "none", "duration_s": 17.0,
                "transport": r.transport, "offered_bps": r.offered_bps,
                "repeat": r.repeat, "rung": r.tune, "valid": True, "parity": {"ok": True},
                "metrics": {"goodput_bps": good * 1e6, "loss_pct": 0.0, "frames": 1000,
                            "idle_pct": [50.0]},
                "rf": {"band": r.band, "channel": 6, "rssi_dbm": -44, "idf_version": "t",
                       "tx_power": "max", "power_save": "off",
                       "phy_rate": self.phy.get(r.tune, "HT ch6 bw20 sta20"),
                       "ap": "verizon-router",
                       "mac_link": "wifi-5-ch104", "shielded": False,
                       "rig_ceiling_mbps": 60.0},
                "gate_failures": [], "gate_warnings": [],
            })
        return 0


def _engine(tmp_path, plan, runner, band="2.4", **kw):
    return tune.Engine(tune.Plan.load(plan), band=band, rungs_root=tmp_path / "rungs",
                       state_dir=tmp_path / "state", ledger=tmp_path / "tune.jsonl",
                       runner=runner, **kw)


# ---- matrix ------------------------------------------------------------------------

def test_a_cell_can_carry_its_own_loads(tmp_path):
    """TCP needs one saturating load; a sweep-wide load list would multiply its runs."""
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({
        "stage": 1, "repeats": 1,
        "sweep": {"start_mbps": 4, "stop_mbps": 60, "steps": 8},
        "cells": [{"chip": "esp32s3", "band": "2.4", "transport": ["tcp"],
                   "tune": ["r0-baseline"], "loads_mbps": [60]}]}))
    runs = Matrix.load(p).expand()
    assert [r.offered_bps for r in runs] == [60_000_000]


# ---- the climb ---------------------------------------------------------------------

def test_first_step_measures_the_starting_rung_then_tries_candidates_on_top(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("win")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 5, "udp": 30},
                      ("esp32s3", "t24-01-win"): {"tcp": 40, "udp": 30}})
    _engine(tmp_path, plan, run).run()
    rung = yaml.safe_load((tmp_path / "rungs/s3/t24-01-win.yaml").read_text())
    assert rung["parent"] == "r0-baseline"
    assert rung["study"] == "max-tput"
    assert rung["config"] == {"CONFIG_WIN": "y"}
    assert len(run.calls) == 2


def test_each_run_offers_one_saturating_load_per_transport(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": []}])
    _engine(tmp_path, plan, FakeRunner({("esp32s3", "r0-baseline"):
                                        {"tcp": 5, "udp": 30}})).run()
    recs = Ledger(tmp_path / "tune.jsonl").read()
    assert sorted((r["transport"], r["offered_bps"], r["repeat"]) for r in recs) == [
        ("tcp", 60_000_000, 1), ("tcp", 60_000_000, 2),
        ("udp", 60_000_000, 1), ("udp", 60_000_000, 2)]


def test_a_winner_becomes_the_parent_of_the_next_candidate(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("win"), _cand("next")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 5, "udp": 30},
                      ("esp32s3", "t24-01-win"): {"tcp": 40, "udp": 30},
                      ("esp32s3", "t24-02-next"): {"tcp": 40, "udp": 30}})
    res = _engine(tmp_path, plan, run).run()
    nxt = yaml.safe_load((tmp_path / "rungs/s3/t24-02-next.yaml").read_text())
    assert nxt["parent"] == "t24-01-win"
    climb = res["climbs"]["esp32s3/2.4"]
    assert climb["incumbent"] == "t24-01-win"
    assert [s["decision"] for s in climb["steps"]] == ["start", "kept", "rejected"]


def test_a_loser_is_recorded_and_the_incumbent_stays(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("lose"), _cand("after")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 10, "udp": 30},
                      ("esp32s3", "t24-01-lose"): {"tcp": 10.1, "udp": 30},
                      ("esp32s3", "t24-02-after"): {"tcp": 10, "udp": 30}})
    _engine(tmp_path, plan, run).run()
    after = yaml.safe_load((tmp_path / "rungs/s3/t24-02-after.yaml").read_text())
    assert after["parent"] == "r0-baseline"
    lose = yaml.safe_load((tmp_path / "rungs/s3/t24-01-lose.yaml").read_text())
    assert lose["measured_gain_pct"] == pytest.approx(1.0, abs=0.01)
    assert lose["measured"]["decision"] == "rejected"


def test_a_gain_inside_the_repeat_spread_is_not_a_gain(tmp_path):
    """+5 % looks like a win, but two repeats 20 % apart cannot resolve it."""
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("noisy")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 10, "udp": 30},
                      ("esp32s3", "t24-01-noisy"): {"tcp": 10.5, "udp": 30}}, jitter=1.0)
    res = _engine(tmp_path, plan, run).run()
    step = res["climbs"]["esp32s3/2.4"]["steps"][1]
    assert step["decision"] == "rejected"
    assert "spread" in step["reason"]


def test_a_gain_on_one_transport_does_not_buy_a_regression_on_the_other(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("trade")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 10, "udp": 30},
                      ("esp32s3", "t24-01-trade"): {"tcp": 20, "udp": 25}})
    res = _engine(tmp_path, plan, run).run()
    step = res["climbs"]["esp32s3/2.4"]["steps"][1]
    assert step["decision"] == "rejected"
    assert "udp" in step["reason"]


def test_the_climb_stops_after_consecutive_misses(tmp_path):
    _rungs(tmp_path)
    cands = [_cand(f"m{i}") for i in range(1, 6)]
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": cands}], stop_after_misses=3)
    model = {("esp32s3", "r0-baseline"): {"tcp": 10, "udp": 30}}
    for i in range(1, 6):
        model[("esp32s3", f"t24-{i:02d}-m{i}")] = {"tcp": 10, "udp": 30}
    res = _engine(tmp_path, plan, FakeRunner(model)).run()
    climb = res["climbs"]["esp32s3/2.4"]
    assert len(climb["steps"]) == 4                    # start + 3 misses
    assert climb["done"] and "3 consecutive" in climb["done_reason"]


def test_a_candidate_that_never_measured_is_not_a_miss(tmp_path):
    """A build Kconfig rejects leaves nothing in the ledger. That says nothing about the
    lever, so it must not count toward the stop rule — it is flagged for a person."""
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("broken"), _cand("win")]}],
                 stop_after_misses=1)
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 10, "udp": 30},
                      ("esp32s3", "t24-02-win"): {"tcp": 20, "udp": 30}},
                     invalid={"t24-01-broken"})
    res = _engine(tmp_path, plan, run).run()
    climb = res["climbs"]["esp32s3/2.4"]
    assert [s["decision"] for s in climb["steps"]] == ["start", "unmeasured", "kept"]
    assert climb["incumbent"] == "t24-02-win"


def test_a_start_that_cannot_be_measured_blocks_the_climb(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("x")]}])
    res = _engine(tmp_path, plan, FakeRunner({})).run()
    climb = res["climbs"]["esp32s3/2.4"]
    assert climb["blocked"]
    assert len(climb["steps"]) == 1


def test_an_unsaturated_measurement_is_repeated_at_a_higher_load(tmp_path):
    """Goodput near the offered load means the load set the number, not the chip: it is
    not a result. The load goes up until the chip, not the load, is the limit."""
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": []}])
    res = _engine(tmp_path, plan, FakeRunner({("esp32s3", "r0-baseline"):
                                              {"tcp": 59, "udp": 30}})).run()
    step = res["climbs"]["esp32s3/2.4"]["steps"][0]
    assert step["offered_mbps"] == 90
    assert step["tcp"]["mbps"] == pytest.approx(59.0)
    assert step["tcp"]["unsaturated"] is False


def test_a_start_still_unsaturated_at_the_rig_ceiling_blocks_the_climb(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("x")]}])
    res = _engine(tmp_path, plan, FakeRunner({("esp32s3", "r0-baseline"):
                                              {"tcp": 70, "udp": 30}}),
                  max_load_mbps=60).run()
    climb = res["climbs"]["esp32s3/2.4"]
    assert "ceiling" in climb["blocked"]
    assert len(climb["steps"]) == 1


def test_a_candidate_unsaturated_at_the_rig_ceiling_is_unmeasured_not_a_miss(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("fast"), _cand("next")]}],
                 stop_after_misses=1)
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 40, "udp": 40},
                      ("esp32s3", "t24-01-fast"): {"tcp": 70, "udp": 40},
                      ("esp32s3", "t24-02-next"): {"tcp": 50, "udp": 40}})
    res = _engine(tmp_path, plan, run, max_load_mbps=60).run()
    steps = res["climbs"]["esp32s3/2.4"]["steps"]
    assert steps[1]["decision"] == "unmeasured"
    assert "ceiling" in steps[1]["reason"]
    assert steps[2]["decision"] == "kept"


def test_a_candidate_offered_more_is_compared_with_the_incumbent_at_the_same_load(tmp_path):
    """A lever that does nothing must not win just because it was offered more: the
    incumbent is re-measured at the candidate's load before they are compared."""
    _rungs(tmp_path)
    noop = {**_cand("noop"), "saturate_mbps": 120}
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [noop]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 40, "udp": 64},
                      ("esp32s3", "t24-01-noop"): {"tcp": 40, "udp": 64}})
    res = _engine(tmp_path, plan, run).run()
    steps = res["climbs"]["esp32s3/2.4"]["steps"]
    assert steps[1]["decision"] == "rejected"
    assert steps[0]["offered_mbps"] == 120                 # the incumbent, re-measured
    assert steps[0]["history"][0]["offered_mbps"] == 90    # 60 was unsaturated for udp 64


def test_a_higher_saturating_load_carries_forward_once_it_wins(tmp_path):
    """After a wider channel wins at 120 Mbit/s offered, everything stacked on it must be
    offered as much, or the plan's 60 would cap it and every later lever read as nothing."""
    _rungs(tmp_path)
    wide = {**_cand("wide"), "saturate_mbps": 120}
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [wide, _cand("after")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 30, "udp": 40},
                      ("esp32s3", "t24-01-wide"): {"tcp": 60, "udp": 80},
                      ("esp32s3", "t24-02-after"): {"tcp": 60, "udp": 80}})
    res = _engine(tmp_path, plan, run).run()
    steps = res["climbs"]["esp32s3/2.4"]["steps"]
    assert [s["offered_mbps"] for s in steps] == [120, 120, 120]
    assert steps[1]["decision"] == "kept"


def test_a_lever_not_in_effect_is_unmeasured_not_a_miss(tmp_path):
    """A 40 MHz build on a 20 MHz access point measures fine and tests nothing."""
    _rungs(tmp_path)
    wide = {**_cand("wide"), "expect_phy": "bw40 sta40"}
    plan = _plan(tmp_path, [{"chip": "esp32c5", "band": "2.4", "start": "r0-baseline",
                             "candidates": [wide]}], stop_after_misses=1)
    run = FakeRunner({("esp32c5", "r0-baseline"): {"tcp": 30, "udp": 40},
                      ("esp32c5", "t24-01-wide"): {"tcp": 30, "udp": 40}},
                     phy={"t24-01-wide": "HT ch6 bw20 sta40"})
    res = _engine(tmp_path, plan, run).run()
    step = res["climbs"]["esp32c5/2.4"]["steps"][1]
    assert step["decision"] == "unmeasured"
    assert "not in effect" in step["reason"] and "bw20 sta40" in step["reason"]
    assert res["climbs"]["esp32c5/2.4"]["misses"] == 0


def test_two_unmeasured_candidates_in_a_row_block_the_climb(tmp_path):
    """A failure that hits every candidate (a content filter, a lapsed flash window) must
    not quietly burn the whole list."""
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("a"), _cand("b"), _cand("c")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 30, "udp": 40}})
    res = _engine(tmp_path, plan, run).run()
    climb = res["climbs"]["esp32s3/2.4"]
    assert [s["decision"] for s in climb["steps"]] == ["start", "unmeasured", "unmeasured"]
    assert "2 candidates in a row" in climb["blocked"]


def test_unmeasured_candidates_are_retried_on_the_next_run(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("a")]}])
    model = {("esp32s3", "r0-baseline"): {"tcp": 30, "udp": 40}}
    _engine(tmp_path, plan, FakeRunner(model)).run()          # a: unmeasured
    model[("esp32s3", "t24-02-a")] = {"tcp": 40, "udp": 40}   # the rig is fixed
    res = _engine(tmp_path, plan, FakeRunner(model)).run()
    climb = res["climbs"]["esp32s3/2.4"]
    assert [s["decision"] for s in climb["steps"]] == ["start", "unmeasured", "kept"]
    assert climb["incumbent"] == "t24-02-a"


def test_5ghz_waits_for_the_24_climb_to_finish(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [
        {"chip": "esp32c5", "band": "2.4", "start": "r0-baseline",
         "candidates": [_cand("a"), _cand("b")]},
        {"chip": "esp32c5", "band": "5", "start": "best:2.4", "candidates": []},
    ])
    model = {("esp32c5", "r0-baseline"): {"tcp": 8, "udp": 30},
             ("esp32c5", "t24-01-a"): {"tcp": 30, "udp": 30}}
    _engine(tmp_path, plan, FakeRunner(model)).run(max_steps=2)   # 2.4 not finished
    res = _engine(tmp_path, plan, FakeRunner(model), band="5").run()
    assert "not finished" in res["climbs"]["esp32c5/5"]["blocked"]


def test_a_step_on_a_different_access_point_is_unmeasured_not_a_miss(tmp_path):
    """House WiFi has a second, distant AP: a board that joined it (ch6, -88 dBm) after a
    reflash measured the other AP, not the lever."""
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32c5", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("far")]}], stop_after_misses=1)
    run = FakeRunner({("esp32c5", "r0-baseline"): {"tcp": 20, "udp": 35},
                      ("esp32c5", "t24-01-far"): {"tcp": 2, "udp": 3}},
                     phy={"r0-baseline": "HE ch1 bw20 sta20",
                          "t24-01-far": "HE ch6 bw20 sta20"})
    res = _engine(tmp_path, plan, run).run()
    climb = res["climbs"]["esp32c5/2.4"]
    step = climb["steps"][1]
    assert step["decision"] == "unmeasured"
    assert "ch6" in step["reason"] and "ch1" in step["reason"]
    assert climb["misses"] == 0


def test_a_decided_step_can_be_reopened_with_a_recorded_reason(tmp_path):
    """How a decision later shown to be invalid (wrong AP) is withdrawn: never by editing
    state.json, and the false miss no longer counts toward the stop rule."""
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32c5", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("a")]}])
    model = {("esp32c5", "r0-baseline"): {"tcp": 20, "udp": 35},
             ("esp32c5", "t24-01-a"): {"tcp": 2, "udp": 3}}
    _engine(tmp_path, plan, FakeRunner(model)).run()            # a: rejected
    eng = _engine(tmp_path, plan, FakeRunner(model))
    eng.reopen("esp32c5/2.4", "t24-01-a", "joined the far AP (ch6)")
    state = json.loads((tmp_path / "state" / "state.json").read_text())
    step = state["climbs"]["esp32c5/2.4"]["steps"][1]
    assert step["decision"] == "unmeasured"
    assert "joined the far AP" in step["reason"]
    assert state["climbs"]["esp32c5/2.4"]["misses"] == 0
    model[("esp32c5", "t24-02-a")] = {"tcp": 25, "udp": 35}
    res = _engine(tmp_path, plan, FakeRunner(model)).run()        # retried as t24-02-a
    assert res["climbs"]["esp32c5/2.4"]["incumbent"] == "t24-02-a"


def test_a_study_can_be_limited_to_one_chip(tmp_path):
    """The S3 is shared with other work (different firmware on it). Climbing only the
    C5 must never build, flash or measure the S3."""
    _rungs(tmp_path)
    plan = _plan(tmp_path, [
        {"chip": "esp32s3", "band": "2.4", "start": "r0-baseline", "candidates": []},
        {"chip": "esp32c5", "band": "2.4", "start": "r0-baseline", "candidates": []},
    ])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 10, "udp": 30},
                      ("esp32c5", "r0-baseline"): {"tcp": 10, "udp": 30}})
    res = _engine(tmp_path, plan, run, chips=["esp32c5"]).run()
    assert list(res["climbs"]) == ["esp32c5/2.4"]
    assert run.calls and all(c.startswith("c5-") for c in run.calls)


def test_a_board_lock_is_taken_with_an_owner_file_and_released(tmp_path):
    """The shared-board protocol: mkdir is the lock, `owner` says who and until when."""
    with tune.board_lock("esp32s3", "cu.usbmodem5B8F0676581", owner="tune-loop S3 climb",
                         expected_s=3600, root=tmp_path) as d:
        assert d == tmp_path / ".esp32s3-usbmodem5B8F0676581.lock"
        text = (d / "owner").read_text()
        assert "owner tune-loop S3 climb" in text and "acquired" in text
        assert "expected_release" in text
    assert not d.exists()


def test_a_held_board_lock_is_never_broken(tmp_path):
    held = tmp_path / ".esp32s3-usbmodem5B8F0676581.lock"
    held.mkdir()
    (held / "owner").write_text("owner intan-chain wiring test\n")
    with pytest.raises(tune.TuneError, match="intan-chain wiring test"):
        with tune.board_lock("esp32s3", "cu.usbmodem5B8F0676581", owner="x",
                             expected_s=60, root=tmp_path):
            pass
    assert (held / "owner").exists()


def test_tune_refuses_to_start_while_a_board_it_needs_is_locked(tmp_path):
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": []}],
                 ledger=str(tmp_path / "l.jsonl"), state_dir=str(tmp_path / "st"))
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "heartbeat").write_text("now\n")
    locks = tmp_path / "locks"
    (locks / ".esp32s3-usbmodem5B8F0676581.lock").mkdir(parents=True)
    (locks / ".esp32s3-usbmodem5B8F0676581.lock" / "owner").write_text("owner chain test\n")
    out = _cli("--plan", str(plan), "--band", "2.4", "--ceilings", "2.4=72",
               "--agent-dir", str(agent), "--lock-dir", str(locks),
               "--s3-port", "cu.usbmodem5B8F0676581")
    assert out.returncode == 2
    assert "chain test" in out.stderr
    assert not (tmp_path / "l.jsonl").exists()


def test_an_interrupted_runner_stops_its_bench_run_child(tmp_path, monkeypatch):
    """Otherwise a stopped study could leave a child that flashes a board after its lock
    was released."""
    (tmp_path / "heartbeat").write_text("now\n")

    class Child:
        terminated = False

        def __init__(self, *a, **k):
            self.stdout = self

        def __iter__(self):
            raise KeyboardInterrupt

        def terminate(self):
            Child.terminated = True

        def wait(self, timeout=None):
            return -15

    monkeypatch.setattr(tune.subprocess, "Popen", Child)
    r = tune.HardwareRunner(band="2.4", ceilings="2.4=72", s3_port="a", c5_port="b",
                            agent_dir=tmp_path)
    with pytest.raises(KeyboardInterrupt):
        r(tmp_path / "m.yaml", tmp_path / "l.jsonl", tmp_path / "log")
    assert Child.terminated


def test_another_engine_on_the_same_study_is_refused(tmp_path):
    import fcntl
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": []}])
    (tmp_path / "state").mkdir()
    with open(tmp_path / "state" / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(tune.TuneError, match="already running"):
            _engine(tmp_path, plan, FakeRunner({})).run()


def test_a_plan_without_a_study_name_is_refused(tmp_path):
    """Without `study:` its rungs would count as formal rungs and change Stage 1's tuned."""
    p = _plan(tmp_path, [], study=None)
    with pytest.raises(tune.TuneError, match="study"):
        tune.Plan.load(p)


def test_rerunning_resumes_without_remeasuring(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("win")]}])
    model = {("esp32s3", "r0-baseline"): {"tcp": 5, "udp": 30},
             ("esp32s3", "t24-01-win"): {"tcp": 40, "udp": 30}}
    _engine(tmp_path, plan, FakeRunner(model)).run()
    again = FakeRunner(model)
    res = _engine(tmp_path, plan, again).run()
    assert again.calls == []
    assert res["climbs"]["esp32s3/2.4"]["incumbent"] == "t24-01-win"


def test_a_candidate_added_to_the_plan_later_is_picked_up(tmp_path):
    """How the supervisor steers: it edits the plan, and the next run continues."""
    _rungs(tmp_path)
    climb = {"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
             "candidates": [_cand("win")]}
    plan = _plan(tmp_path, [climb])
    model = {("esp32s3", "r0-baseline"): {"tcp": 5, "udp": 30},
             ("esp32s3", "t24-01-win"): {"tcp": 40, "udp": 30},
             ("esp32s3", "t24-02-more"): {"tcp": 50, "udp": 30}}
    _engine(tmp_path, plan, FakeRunner(model)).run()
    climb["candidates"].append(_cand("more"))
    plan = _plan(tmp_path, [climb])
    res = _engine(tmp_path, plan, FakeRunner(model)).run()
    assert res["climbs"]["esp32s3/2.4"]["incumbent"] == "t24-02-more"


def test_only_the_requested_band_is_climbed_and_5ghz_can_start_from_the_24_winner(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [
        {"chip": "esp32c5", "band": "2.4", "start": "r0-baseline",
         "candidates": [_cand("win")]},
        {"chip": "esp32c5", "band": "5", "start": "best:2.4", "candidates": []},
    ])
    model = {("esp32c5", "r0-baseline"): {"tcp": 8, "udp": 30},
             ("esp32c5", "t24-01-win"): {"tcp": 30, "udp": 30}}
    r24 = _engine(tmp_path, plan, FakeRunner(model), band="2.4").run()
    assert list(r24["climbs"]) == ["esp32c5/2.4"]
    run5 = FakeRunner(model)
    r5 = _engine(tmp_path, plan, run5, band="5").run()
    assert r5["climbs"]["esp32c5/5"]["steps"][0]["rung"] == "t24-01-win"


def test_a_study_never_forks_the_formal_ladder(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("a"), _cand("b")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 5, "udp": 30},
                      ("esp32s3", "t24-01-a"): {"tcp": 5, "udp": 30},
                      ("esp32s3", "t24-02-b"): {"tcp": 5, "udp": 30}})
    _engine(tmp_path, plan, run).run()
    lad = Ladder.load(tmp_path / "rungs", "esp32s3")
    assert lad.rung_for_tune("tuned") == "r0-baseline"
    assert lad.resolve("t24-02-b") == {"CONFIG_B": "y"}


def test_summary_reports_the_best_number_per_transport(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("tcpwin"), _cand("udpwin")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 5, "udp": 30},
                      ("esp32s3", "t24-01-tcpwin"): {"tcp": 40, "udp": 30},
                      ("esp32s3", "t24-02-udpwin"): {"tcp": 40, "udp": 45}})
    res = _engine(tmp_path, plan, run).run()
    best = res["climbs"]["esp32s3/2.4"]["best"]
    assert best["tcp"]["rung"] in ("t24-01-tcpwin", "t24-02-udpwin")
    assert best["tcp"]["mbps"] == pytest.approx(40.0)
    assert best["udp"] == {"rung": "t24-02-udpwin", "mbps": pytest.approx(45.0),
                           "loss_pct": 0.0}
    state = json.loads((tmp_path / "state/state.json").read_text())
    assert state["climbs"]["esp32s3/2.4"]["incumbent"] == "t24-02-udpwin"
    md = (tmp_path / "state/summary.md").read_text()
    assert "t24-02-udpwin" in md and "45.0" in md


def test_an_existing_rung_file_with_different_content_is_never_overwritten(tmp_path):
    root = _rungs(tmp_path)
    (root / "s3" / "t24-01-win.yaml").write_text(
        "id: t24-01-win\nparent: r0-baseline\nstudy: other\nconfig: {CONFIG_X: y}\n")
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("win")]}])
    run = FakeRunner({("esp32s3", "r0-baseline"): {"tcp": 5, "udp": 30}})
    with pytest.raises(tune.TuneError, match="t24-01-win"):
        _engine(tmp_path, plan, run).run()


# ---- the shipped plan --------------------------------------------------------------

def test_the_shipped_plan_loads_and_every_candidate_resolves():
    plan = tune.Plan.load(HOST / "tuning" / "max-tput.yaml")
    assert plan.repeats == 2 and plan.hold_s == 20
    chips = {(c.chip, c.band) for c in plan.climbs}
    assert ("esp32s3", "2.4") in chips and ("esp32c5", "2.4") in chips
    assert ("esp32c5", "5") in chips
    for c in plan.climbs:
        ids = [k.id for k in c.candidates]
        assert len(ids) == len(set(ids)), f"duplicate candidate id in {c.key}"
        for k in c.candidates:
            assert k.config, f"{c.key}/{k.id} has no config"
            assert all(s.startswith("CONFIG_") for s in k.config)


def test_every_candidate_symbol_in_the_shipped_plan_exists_in_this_esp_idf():
    """A typo in a candidate would cost a build before the driver caught it."""
    import os
    from bench.rungs import known_symbols
    idf = next((pathlib.Path(p) for p in (os.environ.get("IDF_PATH"),
                str(pathlib.Path.home() / "Desktop/Research/esp-idf"))
                if p and (pathlib.Path(p) / "components").is_dir()), None)
    if idf is None:
        pytest.skip("no ESP-IDF checkout to read Kconfig from")
    known = known_symbols(idf, HOST.parent / "firmware")
    plan = tune.Plan.load(HOST / "tuning" / "max-tput.yaml")
    for c in plan.climbs:
        for k in c.candidates:
            unknown = [s for s in k.config if s not in known]
            assert not unknown, f"{c.key}/{k.id}: {unknown} are not Kconfig symbols"


# ---- the command -------------------------------------------------------------------

def _cli(*argv):
    import subprocess
    return subprocess.run([sys.executable, "-m", "cli.bench", "tune", *argv], cwd=HOST,
                          capture_output=True, text=True, timeout=300)


def test_tune_refuses_a_plan_with_an_unknown_symbol_before_anything_runs(tmp_path):
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("typo", CONFIG_ESP_WIFI_TASK_CORE_ID_0="y")]}],
                 ledger=str(tmp_path / "l.jsonl"), state_dir=str(tmp_path / "st"))
    import os
    if not any(p and (pathlib.Path(p) / "components").is_dir() for p in
               (os.environ.get("IDF_PATH"), str(pathlib.Path.home() / "Desktop/Research/esp-idf"))):
        pytest.skip("no ESP-IDF checkout to read Kconfig from")
    out = _cli("--plan", str(plan), "--band", "2.4", "--ceilings", "2.4=60")
    assert out.returncode == 2
    assert "CONFIG_ESP_WIFI_TASK_CORE_ID_0" in out.stderr
    assert not (tmp_path / "l.jsonl").exists()


def test_tune_refuses_to_start_without_a_running_hostrun(tmp_path):
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": []}],
                 ledger=str(tmp_path / "l.jsonl"), state_dir=str(tmp_path / "st"))
    out = _cli("--plan", str(plan), "--band", "2.4", "--ceilings", "2.4=60",
               "--agent-dir", str(tmp_path / "no-agent"))
    assert out.returncode == 2
    assert "hostrun" in out.stderr


def test_tune_needs_the_ceiling_of_the_band_it_measures(tmp_path):
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": []}],
                 ledger=str(tmp_path / "l.jsonl"), state_dir=str(tmp_path / "st"))
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / "heartbeat").write_text("now\n")
    out = _cli("--plan", str(plan), "--band", "2.4", "--ceilings", "5=95",
               "--agent-dir", str(agent))
    assert out.returncode == 2
    assert "ceiling" in out.stderr


def test_the_hardware_runner_uses_this_checkouts_hostrun(tmp_path):
    r = tune.HardwareRunner(band="2.4", ceilings="2.4=60", s3_port="a", c5_port="b",
                            agent_dir=tmp_path)
    assert r.args[r.args.index("--agent-dir") + 1] == str(tmp_path)


def test_a_silent_hostrun_stops_the_study_instead_of_timing_out_every_step(tmp_path):
    """hostrun stops beating while it waits at its flash prompt; each build would then
    wait out a 30-minute timeout."""
    import os
    (tmp_path / "heartbeat").write_text("then\n")
    old = time.time() - 600
    os.utime(tmp_path / "heartbeat", (old, old))
    r = tune.HardwareRunner(band="2.4", ceilings="2.4=60", s3_port="a", c5_port="b",
                            agent_dir=tmp_path)
    with pytest.raises(tune.TuneError, match="hostrun"):
        r(tmp_path / "m.yaml", tmp_path / "l.jsonl", tmp_path / "log")


def test_tune_sim_runs_against_fake_esps_without_touching_the_real_rungs(tmp_path):
    """A rehearsal of the real plan: its 20 s holds, IDF version and network are the
    rig's, so the sim replaces them — fast, and not refused by gates fakes cannot pass."""
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": [_cand("x", CONFIG_BENCH_PIPE_NUM_BUFS=8)]}],
                 ssid="Some_Network", ledger=str(tmp_path / "l.jsonl"),
                 state_dir=str(tmp_path / "st"))
    out = _cli("--plan", str(plan), "--band", "2.4", "--sim")
    assert out.returncode == 0, out.stderr
    state = json.loads((tmp_path / "st-sim" / "state.json").read_text())
    assert [s["decision"] for s in state["climbs"]["esp32s3/2.4"]["steps"]][0] == "start"
    recs = Ledger(tmp_path / "l-sim.jsonl").read()
    assert recs and all(r["duration_s"] < 5 for r in recs)
    assert not (HOST / "rungs" / "s3" / "t24-01-x.yaml").exists()


# ---- plumbing: the real orchestrator against fake ESPs --------------------------------

def test_one_step_through_the_simulator(tmp_path):
    _rungs(tmp_path)
    plan = _plan(tmp_path, [{"chip": "esp32s3", "band": "2.4", "start": "r0-baseline",
                             "candidates": []}], hold_s=1, discard_s=0.2,
                 expected_idf=None)
    runner = tune.SimRunner()
    res = _engine(tmp_path, plan, runner).run()
    step = res["climbs"]["esp32s3/2.4"]["steps"][0]
    assert step["decision"] == "start"
    assert step["tcp"]["mbps"] > 0 and step["udp"]["mbps"] > 0
