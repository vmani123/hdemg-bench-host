"""Campaign cell options: per-cell hold, telemetry sampling, stress events, time series."""
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench.drivers import SimDriver            # noqa: E402
from bench.ledger import Ledger                # noqa: E402
from bench.orchestrator import Matrix, Orchestrator   # noqa: E402
from bench.telemetry import summarize          # noqa: E402


def _matrix(tmp_path, **cell):
    return Matrix(stage=1, repeats=1, ap="sim", expected_idf="v6.0.2",
                  sweep={"start_mbps": 4, "stop_mbps": 4, "steps": 1, "hold_s": 30,
                         "discard_s": 3},
                  timeseries_dir=str(tmp_path / "ts"),
                  cells=[{"chip": "esp32c5", "band": "5", "transport": ["tcp"],
                          "tune": ["tuned"], **cell}])


def test_cell_options_shape_the_run_and_land_in_the_record(tmp_path):
    m = _matrix(tmp_path, tag="soak", hold_s=3.0, discard_s=0.5, sample_s=0.5,
                long_run=True, window_s=1.0, reconnect=True,
                stress=[{"at_s": 1.0, "kind": "pause", "secs": 0.5}])
    runs = m.expand()
    assert runs[0].cell_id.endswith("-tuned-soak")
    assert runs[0].opts["hold_s"] == 3.0
    drv = SimDriver()
    led = Ledger(tmp_path / "r.jsonl")
    try:
        Orchestrator(m, drv, led, rig_ceilings={"5": 95.0}, recv_port=17501).run_all()
    finally:
        drv.shutdown()
    rec = led.read()[0]
    assert 2.5 <= rec["metrics"]["duration_s"] <= 3.5          # the cell's hold, not 30 s
    assert rec["metrics"]["latency_method"].startswith("drift-corrected")
    assert rec["telemetry"]["samples"] >= 4
    assert rec["stress"][0]["kind"] == "pause" and rec["stress"][0]["outcome"] == "ok"
    ts = json.loads(pathlib.Path(rec["timeseries"]).read_text())
    assert len(ts["windows"]) == 3 and ts["device"] and ts["stress"]


def test_summarize_counts_resets_and_counter_deltas():
    s = [{"t_s": 0, "heap": 50000, "heap_min": 40000, "uptime_s": 100, "wifi_disc": 1},
         {"t_s": 1, "heap": 49000, "heap_win_min": 30000, "uptime_s": 101, "wifi_disc": 2},
         {"t_s": 2, "error": "ControlError: no reply"},
         {"t_s": 3, "heap": 52000, "heap_min": 45000, "uptime_s": 2,
          "reset_reason": "PANIC", "wifi_disc": 0},
         {"t_s": 4, "heap": 52000, "uptime_s": 3, "wifi_disc": 1}]
    out = summarize(s)
    assert out["missed"] == 1 and out["resets"] == 1
    assert out["reset_reasons"] == ["PANIC"]
    assert out["heap_low_B"] == 30000
    assert out["wifi_disc_delta"] == 2      # 1->2, then restarted at 0, then 0->1
