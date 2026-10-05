"""Regressions for the faults that would have cost an unattended night on real boards.

Each of these passed the loopback suite before it was fixed, because SimDriver does not
build, flash, discover or hand out DHCP addresses — so each test pins one of them here.
"""
import sys, pathlib, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench.control import ControlClient, discover
from bench.drivers import HardwareDriver, SimDriver
from bench.fakeesp import FakeESP
from bench.ledger import Ledger
from bench.orchestrator import Matrix, Orchestrator
from bench.rungs import Ladder, Rung

RUNGS = pathlib.Path(__file__).resolve().parents[1] / "rungs"


def _matrix(repeats=1, tunes=("baseline", "tuned"), transports=("udp",), band="5",
            chip="esp32c5", steps=2):
    return Matrix(stage=1, repeats=repeats,
                  sweep={"start_mbps": 8, "stop_mbps": 24, "steps": steps,
                         "hold_s": 1.0, "discard_s": 0.2},
                  cells=[{"chip": chip, "band": band, "source": "synth",
                          "transport": list(transports), "tune": list(tunes)}])


# -- tune label -> rung file -------------------------------------------------
def test_tune_labels_resolve_to_rung_files_that_exist():
    """hostrun builds rungs/<chip>/<rung>.yaml; 'tuned' is not a file name."""
    for chip, top in (("esp32s3", "r2-core-split"), ("esp32c5", "r2-iram")):
        lad = Ladder.load(RUNGS, chip)
        assert lad.rung_for_tune("baseline") == "r0-baseline"
        assert lad.rung_for_tune("tuned") == top
        assert lad.rung_for_tune("r1-cpu-opt") == "r1-cpu-opt"
        with pytest.raises(KeyError):
            lad.rung_for_tune("fastest")


def test_a_forked_ladder_refuses_to_guess_the_tuned_rung():
    lad = Ladder("esp32s3", [
        Rung(id="r0", applies_to="esp32s3"),
        Rung(id="r1a", applies_to="esp32s3", parent="r0"),
        Rung(id="r1b", applies_to="esp32s3", parent="r0"),
    ])
    with pytest.raises(ValueError):
        lad.rung_for_tune("tuned")


# -- discovery ---------------------------------------------------------------
def test_discover_finds_a_device_by_asking_hello():
    esp = FakeESP("esp32s3", control_port=16910, band="2.4")
    esp.start()
    try:
        found = discover(["127.0.0.1"], port=16910, timeout=0.5)
    finally:
        esp.shutdown()
    assert found["esp32s3"]["ip"] == "127.0.0.1"
    assert found["esp32s3"]["band"] == "2.4"


# -- hardware driver ---------------------------------------------------------
class _StubHostrun(HardwareDriver):
    def __init__(self, port, **kw):
        super().__init__(ips={"esp32c5": "127.0.0.1"}, firmware_root=".",
                         ports={"esp32c5": "cu.fake"}, host_ip="127.0.0.1", **kw)
        self._clients["esp32c5"] = ControlClient("127.0.0.1", port, timeout=0.5)
        self.jobs = []

    def _hostrun(self, action, **params):
        self.jobs.append((action, params))
        return "=== exit 0"


def test_flash_builds_the_mapped_rung_and_checks_the_board_runs_it():
    esp = FakeESP("esp32c5", control_port=16920, rung="r2-iram")
    esp.start()
    try:
        drv = _StubHostrun(16920)
        out = drv.ensure_flashed("esp32c5", "synth", "tuned")
        assert out == {"flashed": True, "chip": "esp32c5", "rung": "r2-iram"}
        assert drv.jobs[0] == ("esp_build", {"target": "esp32c5", "rung": "r2-iram",
                                             "ingress": "synth"})
        assert drv.jobs[1][0] == "esp_flash"
        # Same build again: no second flash.
        assert drv.ensure_flashed("esp32c5", "synth", "tuned")["flashed"] is False
    finally:
        esp.shutdown()


def test_a_board_still_running_the_old_build_is_refused():
    """The old failure: build refused, stale binary flashed, tuned runs measured
    baseline. Now the post-flash hello must report the requested rung."""
    esp = FakeESP("esp32c5", control_port=16930, rung="r0-baseline")
    esp.start()
    try:
        drv = _StubHostrun(16930)
        with pytest.raises(RuntimeError, match="did not take"):
            drv.ensure_flashed("esp32c5", "synth", "tuned")
    finally:
        esp.shutdown()


def test_hostrun_nonzero_exit_raises(tmp_path):
    drv = HardwareDriver(ips={}, firmware_root=".", ports={}, agent_dir=tmp_path,
                         host_ip="127.0.0.1")
    drv.HOSTRUN_WAIT_S = 10

    def runner():
        q = tmp_path / "queue"
        while True:
            jobs = list(q.glob("*.job")) if q.exists() else []
            if jobs:
                (tmp_path / "out" / f"{jobs[0].stem}.log").write_text(
                    "=== id x\nREFUSED: no rung file\n=== ---\n=== exit 64  now\n")
                return
            time.sleep(0.05)
    t = threading.Thread(target=runner)
    t.start()
    with pytest.raises(RuntimeError, match="exit 64"):
        drv._hostrun("esp_build", target="esp32c5", rung="tuned", ingress="synth")
    t.join()


# -- orchestrator ------------------------------------------------------------
def test_keepalive_actually_polls_between_runs(tmp_path):
    """It used to be created paused and never resumed: zero polls all night."""
    drv = SimDriver(base_port=16940)
    led = Ledger(tmp_path / "r.jsonl")
    orc = Orchestrator(_matrix(tunes=("tuned",)), drv, led,
                       rig_ceilings={"5": 95.0, "2.4": 60.0}, recv_port=16943,
                       keepalive=True)
    try:
        orc.run_all()
    finally:
        drv.shutdown()
    assert max(r["keepalive"]["polls"] for r in led.read()) >= 1


def test_a_cell_that_cannot_be_flashed_is_skipped_not_fatal(tmp_path):
    class BrokenTuned(SimDriver):
        def ensure_flashed(self, chip, source, tune):
            if tune == "tuned":
                raise RuntimeError("hostrun esp_build failed (exit 66)")
            return super().ensure_flashed(chip, source, tune)

    drv = BrokenTuned(base_port=16950)
    led = Ledger(tmp_path / "r.jsonl")
    orc = Orchestrator(_matrix(), drv, led, rig_ceilings={"5": 95.0, "2.4": 60.0},
                       recv_port=16953)
    try:
        res = orc.run_all()
    finally:
        drv.shutdown()
    assert res["errors"] == 1                       # one attempt, then the cell is skipped
    assert list(res["abandoned_cells"]) == ["s1-esp32c5-5-synth-udp-tuned"]
    assert res["valid"] == 2                        # baseline still measured
    assert {r["variant"] for r in led.read()} == {"baseline"}
    assert res["remaining"] == 2                    # tuned points left for the next pass


def test_run_order_reflashes_once_per_repeat_after_the_first():
    runs = _matrix(repeats=3, transports=("udp", "tcp")).expand()
    tunes = [r.tune for r in runs]
    flashes = 1 + sum(a != b for a, b in zip(tunes, tunes[1:]))
    assert flashes == 4          # was 12: baseline/tuned alternated with transport
    assert [r.repeat for r in runs] == sorted(r.repeat for r in runs)
