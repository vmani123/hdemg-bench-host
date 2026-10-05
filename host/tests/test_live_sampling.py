"""The achieved source rate and the CPU-idle figure must be sampled while the device is
still streaming. On the first real sweep they were read from the `stop` summary: the
firmware had already zeroed its achieved rate, so every run was recorded as
"achieved 0.0", labelled source-limited, and given the idle figure of a board at rest."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench.drivers import SimDriver
from bench.ledger import Ledger
from bench.orchestrator import Matrix, Orchestrator


def test_achieved_rate_and_idle_come_from_the_running_stream(tmp_path):
    m = Matrix(stage=1, repeats=1,
               sweep={"start_mbps": 8, "stop_mbps": 16, "steps": 2,
                      "hold_s": 1.2, "discard_s": 0.2},
               cells=[{"chip": "esp32c5", "band": "5", "source": "synth",
                       "transport": ["udp"], "tune": ["baseline"]}])
    drv = SimDriver(base_port=17200)       # fake ceiling for the C5 at 5 GHz: 46 Mbit/s
    led = Ledger(tmp_path / "runs.jsonl")
    orc = Orchestrator(m, drv, led, rig_ceilings={"5": 95.0}, recv_port=17233)
    try:
        orc.run_all()
    finally:
        drv.shutdown()
    recs = led.read()
    assert len(recs) == 2
    for r in recs:
        # Well below the ceiling, the source keeps up: achieved == commanded.
        assert r["metrics"]["achieved_bps"] == r["offered_bps"], r["metrics"]
        assert r["source_limited"] is False
        assert not any("source-limited" in w for w in r["gate_warnings"])
        # The idle figure is the loaded one, not the ~99 % of a board that has stopped.
        assert r["metrics"]["idle_pct"][0] < 95.0, r["metrics"]["idle_pct"]
