"""The three things that decide whether a sweep survives the night unattended."""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench.control import ControlError
from bench.drivers import SimDriver
from bench.keepalive import Keepalive, NullKeepalive
from bench.ledger import Ledger
from bench.orchestrator import Matrix, Orchestrator


@pytest.fixture
def mx():
    return Matrix(stage=1, repeats=1,
                  sweep={"start_mbps": 8, "stop_mbps": 24, "steps": 2,
                         "hold_s": 1.0, "discard_s": 0.2},
                  cells=[
                      {"chip": "esp32c5", "band": "5", "source": "synth",
                       "transport": ["udp"], "tune": ["tuned"]},
                      {"chip": "esp32c5", "band": "2.4", "source": "synth",
                       "transport": ["udp"], "tune": ["tuned"]},
                      {"chip": "esp32s3", "band": "2.4", "source": "synth",
                       "transport": ["udp"], "tune": ["tuned"]},
                  ])


# -- band filtering: one band is the unattended unit ------------------------
def test_unfiltered_expansion_covers_every_band(mx):
    assert {r.band for r in mx.expand()} == {"5", "2.4"}


def test_band_filter_restricts_the_sweep(mx):
    runs = mx.expand(["2.4"])
    assert runs and {r.band for r in runs} == {"2.4"}
    assert {r.chip for r in runs} == {"esp32c5", "esp32s3"}


def test_band_filter_reaches_pending_and_resume(mx, tmp_path):
    drv = SimDriver(base_port=16100)
    try:
        orc = Orchestrator(mx, drv, Ledger(tmp_path / "r.jsonl"),
                           rig_ceilings={"5": 95.0, "2.4": 60.0},
                           bands=["5"], recv_port=16033)
        pend = orc.pending()
        assert pend and all(r.band == "5" for r in pend)
    finally:
        drv.shutdown()


# -- keepalive -------------------------------------------------------------
class FlakyClient:
    """Answers, then stops, then answers again — one re-association."""
    def __init__(self, fail_after=1, recover_after=3):
        self.calls = 0
        self.fail_after, self.recover_after = fail_after, recover_after

    def stat(self):
        self.calls += 1
        if self.fail_after < self.calls <= self.recover_after:
            raise ControlError("no reply")
        return {"ok": True}


def test_keepalive_counts_a_reassociation_once():
    ka = Keepalive(FlakyClient(), interval_s=0.01)
    for _ in range(5):
        ka._poll_once()
    assert ka.failures == 2
    assert ka.reassociations == 1
    assert ka.take_reassociation() is True
    assert ka.take_reassociation() is False, "must be reported on exactly one run"


def test_keepalive_is_quiet_while_paused():
    """A measurement must not contain the instrument."""
    c = FlakyClient(fail_after=99)
    ka = Keepalive(c, interval_s=0.02).start()
    ka.resume()
    time.sleep(0.15)
    before = c.calls
    assert before > 0, "should poll when running"
    with ka.paused():
        time.sleep(0.15)
        assert c.calls == before, "polled during a measurement window"
    time.sleep(0.1)
    assert c.calls > before, "should resume after the measurement"
    ka.stop()


def test_null_keepalive_is_a_safe_no_op():
    ka = NullKeepalive()
    with ka.paused():
        pass
    assert ka.take_reassociation() is False
    assert ka.snapshot()["reassociations"] == 0


# -- recovery: a wedged board costs one point, not the night ---------------
class DeadDriver(SimDriver):
    """Never answers; recovery always fails."""
    def control(self, chip):
        class Dead:
            def hello(self): raise ControlError("device did not answer")
            def stat(self): raise ControlError("device did not answer")
        return Dead()

    def recover(self, chip):
        return False


def test_a_dead_board_records_an_invalid_run_and_the_sweep_continues(mx, tmp_path):
    drv = DeadDriver(base_port=16200)
    led = Ledger(tmp_path / "r.jsonl")
    try:
        orc = Orchestrator(mx, drv, led, rig_ceilings={"5": 95.0, "2.4": 60.0},
                           bands=["5"], recv_port=16133)
        res = orc.run_all()
    finally:
        drv.shutdown()
    assert res["attempted"] > 0
    assert res["invalid"] == res["attempted"], "every point should be recorded, not raised"
    recs = led.read()
    assert recs and all(not r["valid"] for r in recs)
    assert any("did not answer" in f for r in recs for f in r["gate_failures"])
