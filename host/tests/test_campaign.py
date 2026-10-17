"""The campaign engine: gating on the wiring check, resume, and the pre-written rules."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench import campaign as cp                       # noqa: E402
from bench.drivers import SimDriver                    # noqa: E402
from bench.wiring import FakeWiring                    # noqa: E402

HOST = pathlib.Path(__file__).resolve().parents[1]


def _campaign(tmp_path, faults=None, base_port=14400):
    cfg = (HOST / "campaign" / "followup.yaml").read_text().replace(
        "state_dir: results/campaign/followup", f"state_dir: {tmp_path}/state").replace(
        "ledger: results/campaign/followup.jsonl", f"ledger: {tmp_path}/ledger.jsonl")
    p = tmp_path / "plan.yaml"
    p.write_text(cfg)
    drv = SimDriver(wiring=FakeWiring(faults=faults), base_port=base_port)
    return cp.Campaign(p, driver=drv, host_dir=tmp_path, sim=True, log=lambda *a: None), drv


def test_a_broken_wire_fails_the_check_and_blocks_stage_2(tmp_path):
    c, drv = _campaign(tmp_path, faults={"open": [3]})
    try:
        rc = c.run(only=["W.1", "6.1", "6.2"])
    finally:
        drv.shutdown()
    assert rc == 1
    assert c.st("W.1")["status"] == "failed" and "#3 open" in c.st("W.1")["error"]
    assert c.st("6.1")["status"] == "skipped" and c.st("6.2")["status"] == "skipped"
    assert "W.1" in (c.state_dir / "summary.md").read_text()


def test_resume_skips_done_tests_and_redo_gives_new_repeats(tmp_path):
    c, drv = _campaign(tmp_path, base_port=14420)
    try:
        assert c.run(only=["0.3"]) == 0
        n = len(c.ledger.read())
        assert c.run(only=["0.3"]) == 0                 # done: nothing measured again
        assert len(c.ledger.read()) == n
        c.redo("0.3")
        assert c.run(only=["0.3"]) == 0
        assert len(c.ledger.read()) == 2 * n            # a new round, new cell ids
    finally:
        drv.shutdown()


def _sc(mbps, spread=0.2, heap=None, phy="HT ch1 bw20 sta20"):
    return {"mbps": mbps, "spread": spread, "n": 2, "heap_min": heap, "phy_rate": phy,
            "loss_pct": 0.0}


def test_kept_psram_rule(tmp_path):
    c, drv = _campaign(tmp_path, base_port=14440)
    drv.shutdown()
    ref = {chip: {"ref": {"tcp": {"60.0": _sc(30, heap=20000)},
                          "udp": {"60.0": _sc(50, heap=20000)}}} for chip in cp.CHIPS}
    c.st("0.3").update(status="done", result={"scores": ref})

    def variant(tid, chip, key, tcp, udp, heap):
        c.st(tid).update(status="done", result={"rung": f"{chip}-{key}", "scores": {chip: {
            key: {"tcp": {"60.0": _sc(tcp, heap=heap)}, "udp": {"60.0": _sc(udp, heap=heap)}}}}})
    variant("2.1", "esp32c5", "p0", 30, 50, 21000)      # frees too little
    variant("2.2", "esp32c5", "p1", 29.9, 50.1, 52000)  # frees 32 KB, same speed: kept
    variant("2.3", "esp32c5", "p2", 25, 44, 90000)      # frees more but loses throughput
    variant("2.5", "esp32s3", "p1", 26, 40, 60000)      # loses throughput
    kept = c.kept_psram()
    assert kept["esp32c5"]["rung"] == "esp32c5-p1"
    assert kept["esp32s3"]["rung"] is None
    assert c.soak_design("esp32c5") == "esp32c5-p1"
    assert c.soak_design("esp32s3") == c.design("esp32s3")


def test_best_setup_rule(tmp_path):
    c, drv = _campaign(tmp_path, base_port=14460)
    drv.shutdown()
    c.st("4.1").update(status="done", result={"in_effect_40": True, "scores": {"esp32s3": {
        "20mhz": {"udp": {"120.0": _sc(44)}}, "40mhz": {"udp": {"120.0": _sc(70)}}}}})
    c.st("4.2").update(status="done", result={"in_effect_40": False, "scores": {"esp32c5": {
        "20mhz": {"udp": {"120.0": _sc(54)}}, "40mhz": {"udp": {"120.0": _sc(80)}}}}})
    c.st("5.2").update(status="done", result={"rung": "c-c5-base", "scores": {"esp32c5": {
        "base": {"udp": {"120.0": _sc(54.3)}}}}})
    c._update_best()
    b = c.state["derived"]["best"]
    assert b["esp32s3"] == {**b["esp32s3"], "rung": "c-s3-ht40", "band": "2.4"}
    # 40 MHz was not in effect on the C5 (not counted); 5 GHz at 54.3 vs 2.4 GHz at 54.0
    # is inside the spread, so the tie goes to the plain design at 2.4 GHz.
    assert b["esp32c5"]["rung"] == "c-c5-base" and b["esp32c5"]["band"] == "2.4"


def test_best_setup_without_width_tests_uses_the_reference(tmp_path):
    c, drv = _campaign(tmp_path, base_port=14480)
    drv.shutdown()
    c.st("0.3").update(status="done", result={"scores": {
        "esp32s3": {"ref": {"udp": {"60.0": _sc(38)}}},
        "esp32c5": {"ref": {"udp": {"60.0": _sc(38)}}}}})
    c.st("5.2").update(status="done", result={"rung": "c-c5-base", "scores": {"esp32c5": {
        "base": {"udp": {"120.0": _sc(55)}}}}})
    c._update_best()
    b = c.state["derived"]["best"]
    assert b["esp32s3"]["rung"] == "c-s3-base" and b["esp32s3"]["band"] == "2.4"
    # 5 GHz beats the 2.4 GHz reference by more than the spread: the C5 moves to 5 GHz.
    assert b["esp32c5"]["band"] == "5"
    assert "c-c5-base@2.4=38.0" in b["esp32c5"]["why"]


def test_long_run_host_drops_within_the_share_count_and_starved_scores_fail():
    from bench import gates
    rec = {"valid": False, "gate_failures": ["this host discarded 103 datagrams at its own "
                                             "socket during the run"],
           "metrics": {"host_udp_drops": 103, "duration_s": 1800.0, "frames": 3463442}}
    assert gates.counts_as_valid(rec)                    # 103 <= 0.1 % of 3.46 M / 30
    assert not gates.counts_as_valid({**rec, "metrics": {**rec["metrics"], "duration_s": 20}})
    assert not gates.counts_as_valid({**rec, "gate_failures": rec["gate_failures"]
                                      + ["no frames received"]})
    sc = {"esp32c5": {"wr-540": {"tcp": {"60.0": _sc(0.02)}}},
          "esp32s3": {"wr-540": {"tcp": {"60.0": _sc(30)}}}}
    assert cp._starved(sc) == ["esp32c5 wr-540 tcp at 60.0 Mbit/s gave 0.02"]
