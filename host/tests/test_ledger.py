import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench.ledger import Ledger, SchemaError, run_key, validate


def _rec(**kw):
    base = {
        "run_id": "x", "timestamp": "2026-09-16T00:00:00Z", "git_sha": "abc",
        "target": "esp32c5", "variant": "tuned", "source": "synth",
        "offered_bps": 10_000_000, "link": "none", "transport": "udp",
        "duration_s": 60.0, "cell_id": "c", "repeat": 1, "rung": "r1",
        "fw_sha": "deadbeef", "parity": {"ok": True},
        "metrics": {"goodput_bps": 9.9e6, "frames": 100, "loss_pct": 0.0},
        "rf": {"band": "5", "channel": 36, "rssi_dbm": -42, "idf_version": "v6.0.2",
               "tx_power": "max", "power_save": "off", "phy_rate": "HE20 MCS9",
               "ap": "iphone", "mac_link": "usb", "shielded": False,
               "rig_ceiling_mbps": 95.0},
    }
    base.update(kw)
    return base


def test_valid_record_passes():
    validate(_rec())


def test_missing_required_key_is_rejected_at_write_time():
    r = _rec(); del r["git_sha"]
    with pytest.raises(SchemaError):
        validate(r)


def test_rf_block_is_required_in_full():
    r = _rec(); del r["rf"]["rig_ceiling_mbps"]
    with pytest.raises(SchemaError):
        validate(r)


def test_truncated_final_line_is_skipped(tmp_path):
    p = tmp_path / "runs.jsonl"
    led = Ledger(p)
    led.append(_rec())
    with p.open("a") as f:
        f.write('{"run_id": "b", "timesta')      # killed mid-write
    assert len(led.read()) == 1                   # resume stays safe


def test_completed_keys_excludes_invalid(tmp_path):
    led = Ledger(tmp_path / "runs.jsonl")
    led.append(_rec(cell_id="c1", valid=True))
    led.append(_rec(cell_id="c2", valid=False))
    keys = led.completed_keys()
    assert ("c1", 10_000_000, 1) in keys
    assert ("c2", 10_000_000, 1) not in keys


def test_run_key_identity():
    assert run_key(_rec()) == ("c", 10_000_000, 1)
