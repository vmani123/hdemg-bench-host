"""The RSSI reference for a cell must be a settled reading. On the first real sweep the
S3 reported -55 dBm moments after its flash and -42 dBm from then on; with the reference
taken before the cell's first run, every later run was refused as a 13 dB drift."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench.drivers import SimDriver
from bench.fakeesp import FakeESP
from bench.ledger import Ledger
from bench.orchestrator import Matrix, Orchestrator


def _matrix(transports=("udp",)):
    return Matrix(stage=1, repeats=1,
                  sweep={"start_mbps": 8, "stop_mbps": 24, "steps": 3,
                         "hold_s": 1.0, "discard_s": 0.2},
                  cells=[{"chip": "esp32s3", "band": "2.4", "source": "synth",
                          "transport": list(transports), "tune": ["baseline"]}])


def test_a_boot_time_rssi_transient_does_not_poison_the_cell(tmp_path, monkeypatch):
    real_stat = FakeESP._stat
    calls = {"n": 0}

    def stat(self):
        d = real_stat(self)
        calls["n"] += 1
        d["rssi"] = -55 if calls["n"] == 1 else -42     # only the very first reading is off
        return d
    monkeypatch.setattr(FakeESP, "_stat", stat)

    drv = SimDriver(base_port=17300)
    led = Ledger(tmp_path / "runs.jsonl")
    orc = Orchestrator(_matrix(), drv, led, rig_ceilings={"2.4": 60.0}, recv_port=17333)
    try:
        orc.run_all()
    finally:
        drv.shutdown()
    recs = led.read()
    assert len(recs) == 3
    assert not any("RSSI drifted" in f for r in recs for f in r["gate_failures"]), \
        [r["gate_failures"] for r in recs]
    # The reference is the settled value, so a REAL change later is still caught.
    assert list(orc._first_rssi.values()) == [-42]


def test_a_real_rssi_change_after_the_first_run_is_still_refused(tmp_path, monkeypatch):
    real_stat = FakeESP._stat
    state = {"rssi": -42}

    def stat(self):
        d = real_stat(self)
        d["rssi"] = state["rssi"]
        return d
    monkeypatch.setattr(FakeESP, "_stat", stat)

    drv = SimDriver(base_port=17400)
    led = Ledger(tmp_path / "runs.jsonl")
    m = _matrix()
    orc = Orchestrator(m, drv, led, rig_ceilings={"2.4": 60.0}, recv_port=17433)
    try:
        runs = orc.pending()
        drv.request_band("2.4")              # run_all does this; run_one alone does not
        first = orc.run_one(runs[0])         # establishes the reference at -42
        assert first["valid"] is True, first["gate_failures"]
        state["rssi"] = -52                  # someone moved something
        rec = orc.run_one(runs[1])
    finally:
        drv.shutdown()
    assert rec["valid"] is False
    assert any("RSSI drifted 10.0 dB" in f for f in rec["gate_failures"])


def test_a_sweep_can_be_restricted_to_one_transport():
    m = _matrix(transports=("udp", "tcp"))
    assert {r.transport for r in m.expand()} == {"udp", "tcp"}
    tcp = m.expand(transports=["tcp"])
    assert tcp and {r.transport for r in tcp} == {"tcp"}
    assert len(tcp) * 2 == len(m.expand())


def test_drift_can_be_kept_as_a_warning_when_explicitly_asked(tmp_path, monkeypatch):
    """For a board whose REPORTED RSSI wanders by itself: the run is kept and labelled,
    and both readings travel with it so it can be filtered afterwards."""
    real_stat = FakeESP._stat
    state = {"rssi": -42}

    def stat(self):
        d = real_stat(self)
        d["rssi"] = state["rssi"]
        return d
    monkeypatch.setattr(FakeESP, "_stat", stat)

    drv = SimDriver(base_port=17500)
    led = Ledger(tmp_path / "runs.jsonl")
    orc = Orchestrator(_matrix(), drv, led, rig_ceilings={"2.4": 60.0}, recv_port=17533,
                       rssi_drift_warn_only=True)
    try:
        runs = orc.pending()
        drv.request_band("2.4")
        orc.run_one(runs[0])
        state["rssi"] = -55
        rec = orc.run_one(runs[1])
    finally:
        drv.shutdown()
    assert rec["valid"] is True, rec["gate_failures"]
    assert any("RSSI drifted 13.0 dB" in w for w in rec["gate_warnings"])
    assert (rec["rssi_ref_dbm"], rec["rssi_pre_dbm"]) == (-42, -55)


def test_the_drift_gate_is_strict_unless_told_otherwise():
    from bench import gates
    ctx = {"associated": True, "band": "2.4", "expected_band": "2.4", "rig_ceiling_mbps": 60.0,
           "offered_bps": 4_000_000, "rssi_dbm": -55, "first_rssi_dbm": -42}
    assert not gates.pre_run(ctx).ok
    g = gates.pre_run({**ctx, "rssi_drift_warn_only": True})
    assert g.ok and any("RSSI drifted" in w for w in g.warnings)
