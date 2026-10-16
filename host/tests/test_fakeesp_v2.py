"""The fake ESP speaks control protocol v2 (docs/CAMPAIGN_PROTOCOL.md §1), and the
firmware's side of the contract agrees with the host's where it can be read from source:
protocol version, stat field names, and the Stage 2 link pin map (§2)."""
import json
import re
import socket
import sys
import time
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench import control, wiring                                    # noqa: E402
from bench.control import ControlClient, ControlError                 # noqa: E402
from bench.fakeesp import FakeESP, link_pins                          # noqa: E402
from bench.frame import iter_frames                                   # noqa: E402

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"

V2_STAT_FIELDS = ("heap_largest", "heap_win_min", "psram_free", "psram_min", "uptime_s",
                  "reset_reason", "udp_enomem", "send_errs", "tcp_opens", "tcp_open_fails",
                  "wifi_disc", "wifi_last_reason", "linkdrops", "stack_min",
                  "nodelay", "wr_max", "dgram")
STACK_TASKS = ("src", "sink", "ctl", "instr", "tiT", "wifi")


@pytest.fixture
def esp_factory():
    made = []

    def make(port, **kw):
        e = FakeESP(kw.pop("chip", "esp32c5"), control_port=port, **kw)
        e.start()
        made.append(e)
        return e, ControlClient("127.0.0.1", port, timeout=1.0)
    yield make
    for e in made:
        e.shutdown()


def _udp_sink():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.3)
    return s, s.getsockname()[1]


def _drain_udp(s, until_s):
    got = []
    end = time.monotonic() + until_s
    while time.monotonic() < end:
        try:
            got.append(s.recvfrom(65535)[0])
        except socket.timeout:
            pass
    return got


# ---- control plane -----------------------------------------------------------------

def test_hello_is_v2_and_the_host_accepts_it(esp_factory):
    _, c = esp_factory(18401)
    r = c.hello()
    assert r["proto"] == control.PROTOCOL_VERSION == 2
    assert r["ingress"] == "synth"


def test_cfg_v2_keys_default_to_v1_behaviour_and_are_echoed(esp_factory):
    _, c = esp_factory(18402)
    a = c.cfg(rate_bps=1000)["applied"]
    assert (a["nodelay"], a["wr_max"], a["dgram"]) == (1, 0, 0)
    a = c.cfg(nodelay=0, wr_max=1000, dgram=540)["applied"]
    assert (a["nodelay"], a["wr_max"], a["dgram"]) == (0, 1000, 540)
    # a key keeps its value until it is set again
    a = c.cfg(rate_bps=2000)["applied"]
    assert (a["nodelay"], a["wr_max"], a["dgram"]) == (0, 1000, 540)
    assert c.cfg(nodelay=7)["applied"]["nodelay"] == 1
    with pytest.raises(ControlError):
        c.cfg(wr_max="lots")


def test_stat_carries_every_v2_field_and_fits_one_datagram(esp_factory):
    _, c = esp_factory(18403, chip="esp32s3")
    st = c.stat()
    for k in V2_STAT_FIELDS:
        assert k in st, k
    assert set(st["stack_min"]) == set(STACK_TASKS)
    assert st["psram_free"] == st["psram_min"] == -1
    assert st["reset_reason"] == "POWERON"
    assert st["heap_win_min"] <= st["heap"] and st["heap_largest"] <= st["heap"]
    summary = c.stop()["summary"]
    for k in V2_STAT_FIELDS:
        assert k in summary, k
    assert len(json.dumps({"ok": True, "summary": summary}).encode()) < 1400


def test_reset_is_a_reboot(esp_factory):
    esp, c = esp_factory(18404)
    c.cfg(nodelay=0, dgram=540, label="x")
    esp.udp_enomem = 5
    c.reset()
    st = c.stat()
    assert st["reset_reason"] == "SW"
    assert st["udp_enomem"] == 0 and st["uptime_s"] == 0
    assert (st["nodelay"], st["dgram"], st["label"]) == (1, 0, "unset")


# ---- data plane ----------------------------------------------------------------------

@pytest.mark.parametrize("dgram,max_frames", [(0, 5), (540, 2), (100, 1)])
def test_udp_datagrams_are_whole_frames_up_to_dgram(esp_factory, dgram, max_frames):
    port = 18410 + max_frames
    _, c = esp_factory(port, ceiling_mbps=500.0)
    rx, rx_port = _udp_sink()
    try:
        # 40 Mbit/s is ~18,500 frames/s: several are due per loop pass, so they batch
        c.cfg(rate_bps=40_000_000, transport="udp", dst=f"127.0.0.1:{rx_port}",
              dur_s=1, frame_bytes=270, dgram=dgram)
        c.start()
        got = _drain_udp(rx, 0.6)
        c.stop()
    finally:
        rx.close()
    assert got
    assert all(len(d) % 270 == 0 for d in got)
    assert max(len(d) for d in got) == max_frames * 270
    frames, rest = iter_frames(b"".join(got), 256)
    assert rest == b"" and frames


def test_tcp_honours_nodelay_and_writes_in_wr_max_chunks(esp_factory, monkeypatch):
    sent: list[int] = []
    opts: list[tuple] = []
    real_sendall = socket.socket.sendall
    real_setsockopt = socket.socket.setsockopt

    class Spy(socket.socket):
        def sendall(self, data, *a):
            sent.append(len(data))
            return real_sendall(self, data, *a)

        def setsockopt(self, *a):
            opts.append(a)
            return real_setsockopt(self, *a)

    import bench.fakeesp as fakeesp
    monkeypatch.setattr(fakeesp.socket, "socket", Spy)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(3.0)
    _, c = esp_factory(18420, ceiling_mbps=500.0)
    c.cfg(rate_bps=40_000_000, transport="tcp", dst=f"127.0.0.1:{srv.getsockname()[1]}",
          dur_s=1, nodelay=0, wr_max=600)
    c.start()
    conn, _ = srv.accept()
    conn.settimeout(0.3)
    data = b""
    end = time.monotonic() + 0.6
    while time.monotonic() < end:
        try:
            data += conn.recv(1 << 16)
        except socket.timeout:
            pass
    st = c.stop()["summary"]
    conn.close()
    srv.close()
    assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 0) in opts
    assert sent and all(n % 270 == 0 and n <= 540 for n in sent)
    assert max(sent) == 540                    # 600 B rounds down to two whole frames
    frames, _ = iter_frames(data, 256)
    seqs = [f.seq for f in frames]
    assert seqs == sorted(seqs) and len(seqs) > 100
    assert st["tcp_opens"] == 1 and st["tcp_open_fails"] == 0


def test_tcp_open_failure_is_counted(esp_factory):
    _, c = esp_factory(18421)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()                                  # nothing listens there now
    c.cfg(rate_bps=1_000_000, transport="tcp", dst=f"127.0.0.1:{dead}", dur_s=1)
    c.start()
    time.sleep(0.2)
    st = c.stat()
    assert st["tcp_open_fails"] == 1 and st["tcp_opens"] == 0


# ---- linkdrop --------------------------------------------------------------------------

def test_linkdrop_replies_first_then_the_board_is_unreachable(esp_factory):
    _, c = esp_factory(18430)
    rx, rx_port = _udp_sink()
    try:
        c.cfg(rate_bps=2_000_000, transport="udp", dst=f"127.0.0.1:{rx_port}", dur_s=3)
        c.start()
        time.sleep(0.3)
        assert c._rpc("linkdrop 700") == {"ok": True, "ms": 700}
        with pytest.raises(ControlError):
            ControlClient("127.0.0.1", 18430, timeout=0.2, retries=1).stat()
        got = _drain_udp(rx, 1.2)              # through the drop and out the other side
        st = c.stop()["summary"]
    finally:
        rx.close()
    assert st["linkdrops"] == 1 and st["wifi_disc"] == 1 and st["wifi_last_reason"] == 8
    assert st["send_errs"] > 0                 # UDP sends failed while the link was down
    frames, _ = iter_frames(b"".join(got), 256)
    seqs = sorted(f.seq for f in frames)
    gaps = [b - a for a, b in zip(seqs, seqs[1:]) if b - a > 1]
    assert gaps and max(gaps) > 0.5 * 2_000_000 / 8 / 270   # >= ~0.5 s of frames lost


def test_linkdrop_on_tcp_starves_the_source(esp_factory):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(3.0)
    _, c = esp_factory(18431)
    c.cfg(rate_bps=2_000_000, transport="tcp", dst=f"127.0.0.1:{srv.getsockname()[1]}",
          dur_s=3)
    c.start()
    conn, _ = srv.accept()
    c._rpc("linkdrop 400")
    time.sleep(0.6)
    st = c.stop()["summary"]
    conn.close()
    srv.close()
    assert st["dropped"] > 0 and st["send_errs"] == 0 and st["tcp_opens"] == 1


def test_linkdrop_clamps_and_needs_a_number(esp_factory):
    _, c = esp_factory(18432)
    with pytest.raises(ControlError):
        c._rpc("linkdrop")
    with pytest.raises(ControlError):
        c._rpc("linkdrop soon")
    assert c._rpc("linkdrop 5") == {"ok": True, "ms": 100}
    time.sleep(0.15)
    assert c._rpc("linkdrop 999999") == {"ok": True, "ms": 60000}


# ---- pins --------------------------------------------------------------------------

def test_link_pins_follow_the_wiring_sheet():
    for chip in ("esp32s3", "esp32c5"):
        assert list(link_pins(chip)) == wiring.esp_pins(chip)
    c5 = link_pins("esp32c5")
    assert c5[10] == ["qspi_cs", "sdio_cmd"] and c5[7] == ["qspi_d0", "sdio_d1"]
    assert link_pins("esp32s3")[7] == ["ready"]


def test_pins_are_refused_in_a_wired_build(esp_factory):
    _, c = esp_factory(18440, ingress="qspi")
    with pytest.raises(ControlError, match="pins are owned by the qspi link; flash a synth build"):
        c._rpc("pins list")


def test_pins_without_a_harness(esp_factory):
    _, c = esp_factory(18441, chip="esp32s3")
    r = c._rpc("pins list")
    assert list(r["pins"]) == [str(g) for g in wiring.esp_pins("esp32s3")]
    assert set(c._rpc("pins in")["levels"].values()) == {0}
    lv = c._rpc("pins drive 12 1")["levels"]
    assert lv["12"] == 1 and sum(lv.values()) == 1
    with pytest.raises(ControlError, match="not a link pin"):
        c._rpc("pins drive 4 1")
    with pytest.raises(ControlError, match="usage"):
        c._rpc("pins drive 12 2")
    assert c._rpc("pins release") == {"ok": True}
    assert set(c._rpc("pins read")["levels"].values()) == {0}


def test_pins_through_a_simulated_harness(esp_factory):
    """Both directions of the wiring check (§4) against FakeWiring, one wire per pin."""
    harness = wiring.FakeWiring()
    _, s3 = esp_factory(18442, chip="esp32s3", wiring=harness)
    _, c5 = esp_factory(18443, chip="esp32c5", wiring=harness)
    boards = {"esp32s3": s3, "esp32c5": c5}
    for w in wiring.WIRES:
        # H7 -> ESP: exactly the expected GPIO on the expected board reads 1
        for b in boards.values():
            b._rpc("pins in")
        harness.h7_drive(w.h7, 1)
        for chip, b in boards.items():
            high = [g for g, v in b._rpc("pins read")["levels"].items() if v]
            assert high == ([str(w.gpio)] if chip == w.chip else []), (w, chip)
        harness.h7_release_all()
        # ESP -> H7: exactly the expected H7 pin reads 1
        boards[w.chip]._rpc(f"pins drive {w.gpio} 1")
        assert [p for p in wiring.H7_PINS if harness.h7_level(p)] == [w.h7]
        boards[w.chip]._rpc("pins release")


def test_pins_see_a_swapped_pair(esp_factory):
    harness = wiring.FakeWiring(faults={"swap": [(3, 4)]})
    _, s3 = esp_factory(18444, chip="esp32s3", wiring=harness)
    s3._rpc("pins in")
    harness.h7_drive("PD11", 1)                # wire 3, meant for GPIO11
    lv = s3._rpc("pins read")["levels"]
    assert lv["13"] == 1 and lv["11"] == 0     # it lands on wire 4's GPIO


# ---- the firmware agrees -----------------------------------------------------------------

def test_firmware_protocol_version_matches_the_host():
    h = (FW / "bench_common" / "include" / "control.h").read_text()
    m = re.search(r"#define\s+CONTROL_PROTOCOL_VERSION\s+(\d+)", h)
    assert m and int(m.group(1)) == control.PROTOCOL_VERSION


def test_firmware_reports_every_v2_stat_field_and_cfg_key():
    instr = (FW / "bench_common" / "instr.c").read_text()
    ctl = (FW / "bench_common" / "control.c").read_text()
    for k in V2_STAT_FIELDS:
        assert f'\\"{k}\\"' in instr, k
    for t in STACK_TASKS:
        assert f'"{t}"' in instr, t
    for k in ("nodelay", "wr_max", "dgram"):
        assert f'"{k}"' in ctl and f'\\"{k}\\"' in ctl, k
    for verb in ("linkdrop", "pins"):
        assert f'!strcmp(rx, "{verb}")' in ctl, verb


def _defines(path: pathlib.Path) -> dict[str, int]:
    return {m.group(1): int(m.group(2)) for m in
            re.finditer(r"#define\s+(PIN_\w+)\s+(\d+)", path.read_text())}


@pytest.mark.parametrize("chip,short", [("esp32s3", "s3"), ("esp32c5", "c5")])
def test_firmware_link_pin_table_matches_the_wiring_sheet(chip, short):
    """app_main.c's k_link_pins (what `pins` reports) resolves through pins_<chip>.h to
    the wiring sheet's GPIOs, in wire order, with the same roles the fake reports."""
    main = FW / chip / "main"
    pins = _defines(main / f"pins_{short}.h")
    src = (main / "app_main.c").read_text()
    table = src[src.index("k_link_pins[] = {"):]
    table = table[:table.index("};")]
    rows = re.findall(r"\{\s*(PIN_\w+)\s*,\s*\{([^}]*)\}\s*\}", table)
    got = {pins[name]: re.findall(r'"(\w+)"', roles) for name, roles in rows}
    assert list(got) == wiring.esp_pins(chip)
    assert got == link_pins(chip)
