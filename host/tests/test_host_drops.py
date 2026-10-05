"""The instrument must not be what loses the data. On the first real sweep a socket
content filter on the Mac (VPN / endpoint-security software) made the kernel discard
10-20 % of the board's datagrams before the harness read them, and that was recorded as
the board's UDP loss."""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench import gates, rfmeta
from bench.ledger import Ledger, run_key

POST = {"commanded_bps": 4_000_000, "achieved_bps": 4_000_000, "esp_reset_during": False,
        "heap_min": 100000, "heap_floor": 20000, "rig_ceiling_mbps": 60.0,
        "metrics": {"frames": 100000, "goodput_bps": 3.9e6, "reorder_count": 0}}


# -- the gate ----------------------------------------------------------------
def test_host_side_drops_invalidate_a_udp_run():
    g = gates.post_run({**POST, "host_udp_drops": 3442})
    assert not g.ok
    assert any("this host discarded 3442 datagrams" in f for f in g.failures)


def test_a_handful_of_host_drops_is_only_a_warning():
    """The kernel counter is system-wide: unrelated software must not void a run."""
    g = gates.post_run({**POST, "host_udp_drops": 6})
    assert g.ok
    assert any("this host discarded 6" in w for w in g.warnings)


def test_no_host_drops_and_unknown_host_drops_both_pass_silently():
    for v in (0, None):
        g = gates.post_run({**POST, "host_udp_drops": v})
        assert g.ok and not g.warnings


def test_udp_drop_counter_is_parsed_from_netstat(monkeypatch):
    out = ("udp:\n\t22438409 datagrams received\n\t\t11 with bad checksum\n"
           "\t\t133541 dropped due to no socket\n"
           "\t\t215911 dropped due to full socket buffers\n")
    monkeypatch.setattr(rfmeta.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(rfmeta, "_run", lambda cmd, timeout=15.0: out)
    assert rfmeta.host_udp_drops() == 215911


# -- withdrawing runs --------------------------------------------------------
def _rec(run_id, cell="s1-esp32c5-2.4-synth-udp-baseline", offered=4_000_000, rep=1,
         transport="udp", valid=True):
    return {"run_id": run_id, "timestamp": "2026-10-05T09:20:00Z", "git_sha": "abc",
            "target": "esp32c5", "variant": "baseline", "source": "synth", "link": "none",
            "transport": transport, "offered_bps": offered, "rate_hz": None,
            "duration_s": 60.0, "cell_id": cell, "repeat": rep, "rung": "r0-baseline",
            "fw_sha": "x", "parity": {"ok": True},
            "metrics": {"goodput_bps": 3.9e6, "frames": 1, "loss_pct": 3.1, "duration_s": 60.0},
            "rf": {"band": "2.4", "channel": 6, "width_mhz": 20, "rssi_dbm": -44,
                   "phy_rate": "HT", "ap": "x", "mac_link": "wifi-5-ch104",
                   "shielded": False, "rig_ceiling_mbps": 60.0, "idf_version": "v",
                   "tx_power": "max", "power_save": "off", "captured_at": "t"},
            "valid": valid, "reassociated": False, "source_limited": False,
            "gate_failures": [], "gate_warnings": []}


def test_voiding_is_append_only_and_makes_the_point_pending_again(tmp_path):
    led = Ledger(tmp_path / "runs.jsonl")
    led.append(_rec("a"))
    led.append(_rec("b", cell="s1-esp32c5-2.4-synth-tcp-baseline", transport="tcp"))
    before = (tmp_path / "runs.jsonl").read_text()
    assert len(led.completed_keys()) == 2

    assert led.void(["a"], "host dropped datagrams") == 1

    after = (tmp_path / "runs.jsonl").read_text()
    assert after.startswith(before), "earlier lines must not be rewritten"
    recs = {r["run_id"]: r for r in led.read()}
    assert recs["a"]["valid"] is False
    assert recs["a"]["voided"] == "host dropped datagrams"
    assert any("voided: host dropped datagrams" in f for f in recs["a"]["gate_failures"])
    assert recs["b"]["valid"] is True
    assert led.completed_keys() == {run_key(recs["b"])}


def test_a_reader_that_predates_markers_just_skips_them(tmp_path):
    """The sweep that is running when a marker is appended still holds the old code."""
    led = Ledger(tmp_path / "runs.jsonl")
    led.append(_rec("a"))
    led.void(["a"], "why")
    marker = json.loads((tmp_path / "runs.jsonl").read_text().splitlines()[-1])
    assert marker["valid"] is False and "cell_id" not in marker
    # The schema check the old reader applies rejects the marker, so it is skipped.
    from bench.ledger import validate, SchemaError
    try:
        validate(marker)
        raise AssertionError("marker must not validate as a run record")
    except SchemaError:
        pass


def test_a_rerun_after_voiding_counts_again(tmp_path):
    led = Ledger(tmp_path / "runs.jsonl")
    led.append(_rec("a"))
    led.void(["a"], "why")
    assert led.completed_keys() == set()
    led.append(_rec("a2"))
    assert len(led.completed_keys()) == 1
