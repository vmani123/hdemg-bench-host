"""A time budget is a promise: nothing is started that cannot finish inside it, a run is
never cut short part-way, and what was not reached stays pending."""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench import rfmeta
from bench.control import ControlClient
from bench.drivers import HardwareDriver, SimDriver
from bench.fakeesp import FakeESP
from bench.ledger import Ledger
from bench.orchestrator import Matrix, Orchestrator, Run


def _matrix(hold=1.0, steps=5):
    return Matrix(stage=1, repeats=1,
                  sweep={"start_mbps": 8, "stop_mbps": 40, "steps": steps,
                         "hold_s": hold, "discard_s": 0.2},
                  cells=[{"chip": "esp32c5", "band": "5", "source": "synth",
                          "transport": ["udp"], "tune": ["baseline"]}])


def test_the_sweep_stops_before_a_run_that_would_overrun(tmp_path, monkeypatch):
    monkeypatch.setattr(Orchestrator, "RUN_OVERHEAD_S", 0.6)
    drv = SimDriver(base_port=17700)
    led = Ledger(tmp_path / "runs.jsonl")
    budget = 4.0
    t0 = time.monotonic()
    orc = Orchestrator(_matrix(), drv, led, rig_ceilings={"5": 95.0}, recv_port=17733,
                       deadline=t0 + budget)
    try:
        res = orc.run_all()
    finally:
        drv.shutdown()
    took = time.monotonic() - t0
    done = len(led.read())
    assert res["stopped_for_time"] is True
    assert 1 <= done < 5, "some runs fit, not all"
    assert res["remaining"] == 5 - done, "what was not reached stays pending"
    assert took <= budget, f"overran the budget: {took:.2f} s"


def test_with_no_room_for_even_one_run_nothing_is_flashed_or_recorded(tmp_path):
    drv = SimDriver(base_port=17710)
    led = Ledger(tmp_path / "runs.jsonl")
    orc = Orchestrator(_matrix(hold=20.0), drv, led, rig_ceilings={"5": 95.0},
                       recv_port=17743, deadline=time.monotonic() + 5.0)
    try:
        res = orc.run_all()
    finally:
        drv.shutdown()
    assert res["stopped_for_time"] is True and res["remaining"] == 5
    assert drv.flashes == 0 and led.read() == []


def test_without_a_budget_nothing_changes(tmp_path):
    drv = SimDriver(base_port=17720)
    led = Ledger(tmp_path / "runs.jsonl")
    orc = Orchestrator(_matrix(steps=2), drv, led, rig_ceilings={"5": 95.0}, recv_port=17753)
    try:
        res = orc.run_all()
    finally:
        drv.shutdown()
    assert res["stopped_for_time"] is False and res["remaining"] == 0


def test_a_pending_flash_is_counted_in_what_a_run_costs(tmp_path):
    class NeedsFlash:
        def needs_flash(self, chip, source, tune):
            return tune == "tuned"
    orc = Orchestrator(_matrix(hold=20.0), NeedsFlash(), Ledger(tmp_path / "r.jsonl"))
    plain = orc.run_cost_s(Run("c", "esp32c5", "5", "synth", "udp", "baseline", 8_000_000, 1))
    flash = orc.run_cost_s(Run("c", "esp32c5", "5", "synth", "udp", "tuned", 8_000_000, 1))
    assert plain == 20.0 + Orchestrator.RUN_OVERHEAD_S
    assert flash == plain + Orchestrator.FLASH_COST_S


def test_the_hardware_driver_knows_when_the_next_run_needs_a_flash():
    class Stub(HardwareDriver):
        def _hostrun(self, action, **params):
            return "=== exit 0"
    esp = FakeESP("esp32c5", control_port=17760, rung="r0-baseline", band="2.4")
    esp.start()
    try:
        drv = Stub(ips={"esp32c5": "127.0.0.1"}, firmware_root=".", ports={"esp32c5": "cu.fake"},
                   host_ip="127.0.0.1", interactive_band=False, ap=rfmeta.HOTSPOT_WIFI_AP)
        drv._clients["esp32c5"] = ControlClient("127.0.0.1", 17760, timeout=0.5)
        drv.request_band("2.4")
        assert drv.needs_flash("esp32c5", "synth", "baseline") is True
        drv.ensure_flashed("esp32c5", "synth", "baseline")
        assert drv.needs_flash("esp32c5", "synth", "baseline") is False
        assert drv.needs_flash("esp32c5", "synth", "p1-tcp-sndbuf") is True
    finally:
        esp.shutdown()
