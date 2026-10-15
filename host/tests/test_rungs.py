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


# ---- tuning-study rungs (bench tune) ---------------------------------------------------

def _formal_plus_study():
    return Ladder("esp32s3", [
        Rung(id="r0", applies_to="esp32s3"),
        Rung(id="r1", applies_to="esp32s3", parent="r0", measured_gain_pct=5.0),
        # a study climbs deeper than the formal ladder, and its losers are siblings
        Rung(id="t1", applies_to="esp32s3", parent="r1", study="max-tput"),
        Rung(id="t2", applies_to="esp32s3", parent="t1", study="max-tput",
             measured_gain_pct=-3.0),
        Rung(id="t3", applies_to="esp32s3", parent="t1", study="max-tput",
             measured_gain_pct=1.0),
    ])


def test_study_rungs_do_not_change_what_tuned_resolves_to():
    """Stage 1's `tuned` must stay the formal ladder's leaf, however deep or forked a
    tuning study's rungs are."""
    assert _formal_plus_study().rung_for_tune("tuned") == "r1"


def test_study_rungs_stay_resolvable_through_their_formal_parents():
    lad = Ladder("esp32s3", [
        Rung(id="r0", applies_to="esp32s3", config={"A": "y"}),
        Rung(id="t1", applies_to="esp32s3", parent="r0", study="s", config={"B": 1}),
    ])
    assert lad.resolve("t1") == {"A": "y", "B": 1}


def test_study_rungs_are_outside_the_formal_stop_rule_and_effort_counts():
    lad = _formal_plus_study()
    assert lad.unmeasured() == ["r0"]
    assert not lad.plateaued()


def test_study_field_loads_from_yaml(tmp_path):
    d = tmp_path / "s3"
    d.mkdir()
    (d / "r0-baseline.yaml").write_text("id: r0-baseline\nconfig: {}\n")
    (d / "t01-x.yaml").write_text("id: t01-x\nparent: r0-baseline\nstudy: max-tput\n")
    lad = Ladder.load(tmp_path, "esp32s3")
    assert lad.rungs["t01-x"].study == "max-tput"
    assert lad.rungs["r0-baseline"].study is None


# ---- did the build actually take the rung? ---------------------------------------------

SDKCONFIG = """\
CONFIG_IDF_TARGET="esp32s3"
CONFIG_LWIP_TCP_SND_BUF_DEFAULT=65535
CONFIG_ESP_WIFI_TASK_PINNED_TO_CORE_0=y
# CONFIG_ESP_WIFI_POWER_SAVE_MIN_MODEM is not set
"""


def test_applied_symbols_pass():
    from bench.rungs import symbols_not_applied
    cfg = {"CONFIG_LWIP_TCP_SND_BUF_DEFAULT": 65535,
           "CONFIG_ESP_WIFI_TASK_PINNED_TO_CORE_0": "y",
           "CONFIG_ESP_WIFI_POWER_SAVE_MIN_MODEM": "n"}
    assert symbols_not_applied(cfg, SDKCONFIG) == []


def test_a_symbol_kconfig_dropped_is_reported():
    """The CONFIG_ESP_WIFI_TASK_CORE_ID_0 failure: a name that does not exist is dropped
    by Kconfig without an error, and the binary silently measures the parent rung."""
    from bench.rungs import symbols_not_applied
    cfg = {"CONFIG_ESP_WIFI_TASK_CORE_ID_0": "y"}
    assert symbols_not_applied(cfg, SDKCONFIG) == ["CONFIG_ESP_WIFI_TASK_CORE_ID_0"]


def test_a_value_kconfig_clamped_is_reported():
    """An out-of-range int is replaced by the default, also without an error."""
    from bench.rungs import symbols_not_applied
    cfg = {"CONFIG_LWIP_TCP_SND_BUF_DEFAULT": 1000000}
    assert symbols_not_applied(cfg, SDKCONFIG) == ["CONFIG_LWIP_TCP_SND_BUF_DEFAULT"]


def _idf():
    import os
    for p in (os.environ.get("IDF_PATH"),
              str(pathlib.Path.home() / "Desktop/Research/esp-idf")):
        if p and (pathlib.Path(p) / "components").is_dir():
            return pathlib.Path(p)
    pytest.skip("no ESP-IDF checkout to read Kconfig from")


def test_every_shipped_rung_symbol_exists_in_this_esp_idf():
    """A name Kconfig does not define is dropped silently. Both r0-baselines once set
    CONFIG_ESP_WIFI_POWER_SAVE_NONE, which no IDF v6 Kconfig defines — every rung
    inherited a symbol that never reached a binary, and the build check refused them all."""
    from bench.rungs import known_symbols
    repo = pathlib.Path(__file__).resolve().parents[2]
    known = known_symbols(_idf(), repo / "firmware")
    for chip in ("esp32s3", "esp32c5"):
        lad = Ladder.load(repo / "host" / "rungs", chip)
        for rid in lad.order:
            unknown = [k for k in lad.rungs[rid].config if k not in known]
            assert not unknown, f"{chip}/{rid}: {unknown} are not Kconfig symbols"


def test_known_symbols_include_the_bench_hooks():
    from bench.rungs import known_symbols
    repo = pathlib.Path(__file__).resolve().parents[2]
    known = known_symbols(_idf(), repo / "firmware")
    assert {"CONFIG_BENCH_UDP_DGRAM_MAX", "CONFIG_BENCH_WIFI_BW_40",
            "CONFIG_LWIP_TCP_SND_BUF_DEFAULT"} <= known
    assert "CONFIG_ESP_WIFI_POWER_SAVE_NONE" not in known


def test_a_disabled_symbol_may_be_absent():
    from bench.rungs import symbols_not_applied
    assert symbols_not_applied({"CONFIG_NOT_A_THING": "n"}, SDKCONFIG) == []
    assert symbols_not_applied({"CONFIG_NOT_A_THING": False}, SDKCONFIG) == []
