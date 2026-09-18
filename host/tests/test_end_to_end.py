"""The loopback self-test (spec §8): the whole harness with zero hardware."""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench.drivers import SimDriver
from bench.ledger import Ledger
from bench.orchestrator import Matrix, Orchestrator, knee
from bench import report


@pytest.fixture
def fast_matrix(tmp_path):
    m = Matrix(stage=1, repeats=2,
               sweep={"start_mbps": 8, "stop_mbps": 56, "steps": 4,
                      "hold_s": 1.2, "discard_s": 0.2},
               cells=[
                   {"chip": "esp32c5", "band": "5", "source": "synth",
                    "transport": ["udp"], "tune": ["baseline", "tuned"]},
                   {"chip": "esp32s3", "band": "2.4", "source": "synth",
                    "transport": ["udp"], "tune": ["baseline", "tuned"]},
               ])
    return m


def test_matrix_expansion_orders_by_band_and_spaces_repeats(fast_matrix):
    runs = fast_matrix.expand()
    assert len(runs) == 2 * 2 * 4 * 2          # cells x tunes x steps x repeats
    # Repeats of a cell are never consecutive: the whole matrix runs once, then again.
    reps = [r.repeat for r in runs]
    assert reps == sorted(reps), "repeat must be the outer loop (thermal drift -> spread)"
    # Within a repeat, all runs of one band are contiguous (one hotspot switch).
    for rep in (1, 2):
        bands = [r.band for r in runs if r.repeat == rep]
        assert bands == sorted(bands, key=str)


def test_full_sweep_produces_a_knee_and_a_report(fast_matrix, tmp_path):
    drv = SimDriver(base_port=14400)
    led = Ledger(tmp_path / "runs.jsonl")
    orc = Orchestrator(fast_matrix, drv, led,
                       rig_ceilings={"5": 95.0, "2.4": 60.0}, recv_port=14333)
    try:
        res = orc.run_all()
    finally:
        drv.shutdown()

    assert res["attempted"] == 32
    assert res["valid"] > 0
    recs = led.read()
    assert len(recs) == 32

    c5 = [r for r in recs if r["target"] == "esp32c5" and r["variant"] == "tuned"]
    k = knee(c5)
    assert k["knee_offered_bps"], "a swept sim must yield a knee"

    out = report.render(tmp_path / "runs.jsonl", tmp_path / "report")
    assert out["records"] == 32
    assert any(f.endswith("rate_vs_ratio.light.png") for f in out["files"])
    assert (tmp_path / "report" / "report.md").exists()


def test_resume_skips_completed_runs(fast_matrix, tmp_path):
    drv = SimDriver(base_port=14500)
    led = Ledger(tmp_path / "runs.jsonl")
    orc = Orchestrator(fast_matrix, drv, led,
                       rig_ceilings={"5": 95.0, "2.4": 60.0}, recv_port=14433)
    try:
        orc.run_all(limit=5)
        remaining_before = len(orc.pending())
        assert remaining_before == 32 - 5
        orc.run_all(limit=3)
        assert len(orc.pending()) == 32 - 8
    finally:
        drv.shutdown()


def test_resume_survives_a_truncated_ledger(fast_matrix, tmp_path):
    p = tmp_path / "runs.jsonl"
    drv = SimDriver(base_port=14600)
    led = Ledger(p)
    orc = Orchestrator(fast_matrix, drv, led,
                       rig_ceilings={"5": 95.0, "2.4": 60.0}, recv_port=14533)
    try:
        orc.run_all(limit=4)
        with p.open("a") as f:
            f.write('{"run_id": "half-written')       # killed mid-write at 3 a.m.
        assert len(orc.pending()) == 32 - 4           # unchanged, not corrupted
    finally:
        drv.shutdown()


def test_flash_count_stays_low(fast_matrix, tmp_path):
    """The runtime control plane is the point: rate and load change without a reflash.
    32 measurement points must not mean 32 flashes."""
    drv = SimDriver(base_port=14700)
    orc = Orchestrator(fast_matrix, drv, Ledger(tmp_path / "runs.jsonl"),
                       rig_ceilings={"5": 95.0, "2.4": 60.0}, recv_port=14633)
    try:
        orc.run_all()
    finally:
        drv.shutdown()
    assert drv.flashes <= 8, f"{drv.flashes} flashes for 32 runs — control plane is not working"


def test_gate_refuses_load_above_the_rig_ceiling(fast_matrix, tmp_path):
    drv = SimDriver(base_port=14800)
    led = Ledger(tmp_path / "runs.jsonl")
    orc = Orchestrator(fast_matrix, drv, led,
                       rig_ceilings={"5": 20.0, "2.4": 20.0}, recv_port=14733)
    try:
        orc.run_all(limit=8)
    finally:
        drv.shutdown()
    recs = led.read()
    blocked = [r for r in recs if not r["valid"]]
    assert blocked, "loads above the rig ceiling must be refused, not measured"
    assert any("rig ceiling" in f for r in blocked for f in r["gate_failures"])
