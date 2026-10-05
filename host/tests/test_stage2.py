"""Stage 2 (STM32H745 wired ingress): the host side, with no hardware.

What these pin down is the part that cannot be seen in the data afterwards: a run with
corruption on the wire must never be counted, a link that is simply too slow must be
labelled rather than failed, and a cell with nothing on the other end of the wire must
cost seconds, not a night.
"""
import struct
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from bench import gates, linktest
from bench.drivers import SimDriver
from bench.frame import PAYLOAD_WORDS, wired_payload
from bench.h7 import H7Control, H7Error
from bench.ledger import Ledger
from bench.orchestrator import Matrix, Orchestrator


def _matrix(source="qspi", chip="esp32s3", band="2.4", steps=2, repeats=1):
    return Matrix(stage=2, repeats=repeats,
                  sweep={"start_mbps": 4, "stop_mbps": 8, "steps": steps,
                         "hold_s": 1.0, "discard_s": 0.2},
                  cells=[{"chip": chip, "band": band, "source": source,
                          "transport": ["udp"], "tune": ["tuned"]}])


def _orc(tmp_path, drv, port, **kw):
    return Orchestrator(_matrix(**kw), drv, Ledger(tmp_path / "runs.jsonl"),
                        rig_ceilings={"5": 95.0, "2.4": 60.0}, recv_port=port,
                        link_opts={"qspi_hz": 25000000})


# -- payload: host mirror of hdemg_link.h ---------------------------------------
def test_wired_payload_matches_the_firmware_header():
    """Vectors computed by compiling firmware/bench_common/include/hdemg_link.h.
    (seq, seed) -> first and last payload word. The third has seq ^ seed == 0, where
    xorshift would stick at zero and the header substitutes a constant."""
    for seq, seed, w0, w63 in ((1, 0, 270369, 932611783),
                               (0x12345678, 0xDEADBEEF, 3236264466, 518367434),
                               (7, 7, 1359758873, 3128921280)):
        words = struct.unpack(f"<{PAYLOAD_WORDS}I", wired_payload(seq, seed))
        assert (words[0], words[-1]) == (w0, w63)
    assert len(wired_payload(1, 0)) == 256


# -- gates ----------------------------------------------------------------------
H7_OK = {"frames": 1000, "ring_drops": 0, "link_errors": 0, "credit_errors": 0,
         "achieved_bps": 4_000_000, "link_bytes": 270_000}
ESP_OK = {"init_err": 0, "len_errors": 0, "magic_errors": 0, "seq_backwards": 0,
          "payload_errors": 0, "seq_gaps": 0, "frames_in": 1000}


def test_clean_link_passes_the_ingress_gate():
    g = gates.ingress({"h7": H7_OK, "esp": ESP_OK})
    assert g.ok and not g.failures and not g.warnings


@pytest.mark.parametrize("field", ["len_errors", "magic_errors", "seq_backwards",
                                   "payload_errors"])
def test_corruption_on_the_wire_invalidates_the_run(field):
    g = gates.ingress({"h7": H7_OK, "esp": {**ESP_OK, field: 1}})
    assert not g.ok and field in g.failures[0]


def test_frames_that_vanish_on_the_wire_invalidate_the_run():
    """The ESP may only be missing frames the generator itself dropped at its ring."""
    g = gates.ingress({"h7": {**H7_OK, "ring_drops": 5}, "esp": {**ESP_OK, "seq_gaps": 8}})
    assert not g.ok and "3 frames lost on the link" in g.failures[0]


def test_a_link_that_is_too_slow_is_a_result_not_a_fault():
    h7 = {**H7_OK, "ring_drops": 50}
    g = gates.ingress({"h7": h7, "esp": {**ESP_OK, "seq_gaps": 50}})
    assert g.ok and any("ingress-limited" in w for w in g.warnings)
    assert gates.ingress_limited(h7) and not gates.ingress_limited(H7_OK)


def test_unverifiable_runs_are_invalid():
    assert not gates.ingress({"h7": {}, "esp": ESP_OK}).ok
    assert not gates.ingress({"h7": H7_OK, "esp": {}}).ok
    assert not gates.ingress({"h7": H7_OK, "esp": ESP_OK, "h7_error": "no slave"}).ok
    assert not gates.ingress({"h7": {**H7_OK, "link_errors": 2}, "esp": ESP_OK}).ok
    assert not gates.ingress({"h7": H7_OK, "esp": {**ESP_OK, "init_err": 258}}).ok


# -- H7 control client ------------------------------------------------------------
class _Wire:
    """Scripted serial port: what the board 'says' in reply to each write."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self._pending = []

    def write(self, data):
        self.sent.append(data.decode().strip())
        self._pending = list(self.replies.pop(0)) if self.replies else []

    def readline(self, timeout):
        return self._pending.pop(0) if self._pending else None


def test_h7_client_skips_noise_and_reads_the_reply():
    w = _Wire([[b"\x00garbage", b'{"partial":', b'{"ok":true,"proto":1,"chip":"stm32h745","fw_sha":"abc"}']])
    assert H7Control(transport=w).hello()["fw_sha"] == "abc"
    assert w.sent == ["hello"]


def test_h7_client_raises_on_refusal_and_on_silence():
    w = _Wire([[b'{"ok":false,"err":"qspi: no slave answered (bad magic)","read_value":0}']])
    with pytest.raises(H7Error) as e:
        H7Control(transport=w).start()
    assert "no slave" in str(e.value) and e.value.reply["read_value"] == 0
    with pytest.raises(H7Error):
        H7Control(transport=_Wire([[], []]), timeout=0.05).stat()


def test_h7_client_refuses_a_port_that_is_not_the_generator():
    w = _Wire([[b'{"ok":true,"proto":1,"chip":"esp32s3"}']])
    with pytest.raises(H7Error):
        H7Control(transport=w).hello()


def test_h7_cfg_formats_key_value_pairs():
    w = _Wire([[b'{"ok":true,"applied":{"rate_bps":4000000}}']])
    assert H7Control(transport=w).cfg(rate_bps=4000000, link="qspi") == {"rate_bps": 4000000}
    assert w.sent == ["cfg link=qspi rate_bps=4000000"]


# -- matrix filters -------------------------------------------------------------
def test_matrix_can_be_restricted_to_wired_chips_and_sources():
    m = Matrix(stage=2, repeats=1, sweep={"start_mbps": 4, "stop_mbps": 4, "steps": 1,
                                          "hold_s": 1, "discard_s": 0},
               cells=[{"chip": "esp32c5", "band": "2.4", "source": "qspi"},
                      {"chip": "esp32s3", "band": "2.4", "source": "qspi"},
                      {"chip": "esp32c5", "band": "2.4", "source": "sdio"}])
    assert {(r.chip, r.source) for r in m.expand(chips=["esp32s3"])} == {("esp32s3", "qspi")}
    assert {(r.chip, r.source) for r in m.expand(sources=["sdio"])} == {("esp32c5", "sdio")}
    assert len(m.expand()) == 3


# -- orchestrator: a wired cell ---------------------------------------------------
def test_wired_run_records_both_ends_and_takes_the_rate_from_the_generator(tmp_path):
    drv = SimDriver(base_port=15400)
    try:
        res = _orc(tmp_path, drv, 15333).run_all()
    finally:
        drv.shutdown()
    assert res["valid"] == 2 and res["remaining"] == 0
    recs = Ledger(tmp_path / "runs.jsonl").read()
    for r in recs:
        assert r["source"] == "qspi" and r["link"] == "qspi" and r["valid"]
        assert r["ingress"]["h7"]["frames"] > 0 and "magic_errors" in r["ingress"]["esp"]
        assert r["ingress"]["opts"] == {"qspi_hz": 25000000}
        assert r["metrics"]["achieved_bps"] == r["offered_bps"]      # the H7's, exact
        assert r["ingress_limited"] is False
    assert drv.h7().cfg_kv["link"] == "qspi" and drv.h7().starts == 2


def test_corruption_makes_a_wired_run_invalid_so_it_is_retried(tmp_path):
    drv = SimDriver(base_port=15500)
    drv.h7_faults["magic_errors"] = 3
    try:
        res = _orc(tmp_path, drv, 15433, steps=1).run_all()
    finally:
        drv.shutdown()
    assert res["valid"] == 0 and res["invalid"] == 1 and res["remaining"] == 1
    rec = Ledger(tmp_path / "runs.jsonl").read()[0]
    assert not rec["valid"] and any("magic_errors=3" in f for f in rec["gate_failures"])


def test_ring_drops_are_labelled_and_the_run_stays_valid(tmp_path):
    drv = SimDriver(base_port=15600)
    drv.h7_faults["ring_drop_frac"] = 0.1
    try:
        res = _orc(tmp_path, drv, 15533, steps=1).run_all()
    finally:
        drv.shutdown()
    rec = Ledger(tmp_path / "runs.jsonl").read()[0]
    assert res["valid"] == 1 and rec["ingress_limited"] is True
    assert any("ingress-limited" in w for w in rec["gate_warnings"])


def test_an_unwired_cell_is_abandoned_before_a_hold_is_spent(tmp_path):
    drv = SimDriver(base_port=15700)
    drv.h7_faults["no_slave"] = True
    try:
        res = _orc(tmp_path, drv, 15633).run_all()
    finally:
        drv.shutdown()
    assert res["errors"] == 1 and res["valid"] == 0 and res["remaining"] == 2
    assert "no qspi link" in next(iter(res["abandoned_cells"].values()))
    assert Ledger(tmp_path / "runs.jsonl").read() == []        # nothing was measured
    assert drv.h7().starts == 0


def test_synthetic_cells_never_touch_the_generator(tmp_path):
    drv = SimDriver(base_port=15800)
    try:
        res = _orc(tmp_path, drv, 15733, source="synth", steps=1).run_all()
    finally:
        drv.shutdown()
    rec = Ledger(tmp_path / "runs.jsonl").read()[0]
    assert res["valid"] == 1 and rec["ingress"] is None and drv._h7 is None


# -- link test ---------------------------------------------------------------------
def test_link_test_verdict():
    h7 = {**H7_OK, "commanded_bps": 4_000_000}
    esp = {**ESP_OK, "link_test": 1, "seed": 42}
    assert linktest.verdict(h7, esp, 42)[0]
    assert not linktest.verdict(h7, {**esp, "payload_errors": 1}, 42)[0]
    assert not linktest.verdict(h7, {**esp, "frames_in": 990}, 42)[0]      # 10 unaccounted
    assert not linktest.verdict(h7, {**esp, "link_test": 0}, 42)[0]        # not verified
    assert not linktest.verdict(h7, esp, 43)[0]                            # wrong seed
    assert not linktest.verdict({**h7, "achieved_bps": 3_000_000}, esp, 42)[0]
    ok, _, notes = linktest.verdict({**h7, "ring_drops": 7}, {**esp, "seq_gaps": 7}, 42)
    assert ok and any("could not carry" in n for n in notes)


def test_link_test_runs_every_load_and_stops_at_the_first_failure(tmp_path):
    drv = SimDriver(base_port=15900)
    said = []
    try:
        res = linktest.run(drv, "esp32s3", "qspi", [4_000_000, 8_000_000], hold_s=0.3,
                           recv_port=15833, say=said.append, sleep=lambda s: __import__("time").sleep(min(s, 0.3)))
        assert res["passed"] and len(res["steps"]) == 2 and not res["meets_guide_hold"]
        assert all(s["esp"]["link_test"] == 1 for s in res["steps"])

        drv.h7_faults["payload_errors"] = 4
        bad = linktest.run(drv, "esp32s3", "qspi", [4_000_000, 8_000_000], hold_s=0.3,
                           recv_port=15833, say=said.append, sleep=lambda s: __import__("time").sleep(min(s, 0.3)))
        assert not bad["passed"] and len(bad["steps"]) == 1
        assert any("payload_errors=4" in f for f in bad["steps"][0]["failures"])
    finally:
        drv.shutdown()
