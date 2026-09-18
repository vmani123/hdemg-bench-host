import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
import yaml
from bench.rungs import Ladder, Rung, effort_report


def test_resolve_flattens_the_parent_chain():
    lad = Ladder("esp32c5", [
        Rung(id="r0", applies_to="esp32c5", config={"A": "y"}),
        Rung(id="r1", applies_to="esp32c5", parent="r0", config={"B": "y"}),
        Rung(id="r2", applies_to="esp32c5", parent="r1", config={"A": "n", "C": "y"}),
    ])
    assert lad.resolve("r2") == {"A": "n", "B": "y", "C": "y"}


def test_null_removes_a_symbol():
    """How a chip opts out of a lever that is invalid on it."""
    lad = Ladder("esp32c5", [
        Rung(id="r0", applies_to="esp32c5", config={"CONFIG_ESP_INTR_IN_IRAM": "y"}),
        Rung(id="r1", applies_to="esp32c5", parent="r0",
             config={"CONFIG_ESP_INTR_IN_IRAM": None}),
    ])
    assert "CONFIG_ESP_INTR_IN_IRAM" not in lad.resolve("r1")


def test_cycle_is_detected():
    lad = Ladder("x", [Rung(id="a", applies_to="x", parent="b"),
                       Rung(id="b", applies_to="x", parent="a")])
    with pytest.raises(ValueError):
        lad.resolve("a")


def test_stop_rule_needs_three_consecutive_small_gains():
    mk = lambda i, g: Rung(id=f"r{i}", applies_to="x", measured_gain_pct=g)
    assert not Ladder("x", [mk(0, 30.0), mk(1, 1.0), mk(2, 1.0)]).plateaued()
    assert Ladder("x", [mk(0, 30.0), mk(1, 1.0), mk(2, 1.0), mk(3, 0.5)]).plateaued()
    assert not Ladder("x", [mk(0, 1.0), mk(1, 1.0), mk(2, 9.0)]).plateaued()


def test_unmeasured_rungs_are_listed():
    lad = Ladder("x", [Rung(id="a", applies_to="x", measured_gain_pct=3.0),
                       Rung(id="b", applies_to="x")])
    assert lad.unmeasured() == ["b"]


def test_shipped_ladders_load_and_resolve():
    root = pathlib.Path(__file__).resolve().parents[1] / "rungs"
    for chip in ("esp32s3", "esp32c5"):
        lad = Ladder.load(root, chip)
        assert "r0-baseline" in lad.rungs
        for rid in lad.order:
            assert isinstance(lad.resolve(rid), dict)


def test_effort_report_flags_unattempted_levers():
    root = pathlib.Path(__file__).resolve().parents[1] / "rungs"
    rep = effort_report(root)
    # The shipped checklist starts entirely unattempted, and must say so loudly
    # rather than quietly passing.
    assert rep["equal_effort_ok"] is False
    assert rep["unattempted"]


def test_effort_report_passes_when_every_lever_is_accounted_for(tmp_path):
    (tmp_path / "s3").mkdir(); (tmp_path / "c5").mkdir()
    (tmp_path / "common-levers.yaml").write_text(yaml.safe_dump({
        "levers": [
            {"id": "a", "attempted": {"s3": "r1", "c5": "r1"}},
            {"id": "b", "attempted": {"s3": "r2", "c5": "n/a — single core"}},
        ]}))
    rep = effort_report(tmp_path)
    assert rep["equal_effort_ok"] is True
