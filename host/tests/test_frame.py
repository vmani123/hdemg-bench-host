import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench.frame import HDR_BYTES, MAGIC, RAW16_128CH_FRAME, iter_frames, pack


def test_header_layout_is_pinned():
    # A change here is a contract break, not a refactor.
    assert HDR_BYTES == 14
    assert RAW16_128CH_FRAME == 270
    assert MAGIC == 0xA55A


def test_roundtrip_and_stream_reframing():
    payload = bytes(range(256))
    stream = b"".join(pack(i, i * 1000, payload) for i in range(5))
    frames, rest = iter_frames(stream, 256)
    assert rest == b""
    assert [f.seq for f in frames] == list(range(5))
    assert frames[0].payload == payload


def test_partial_frame_is_held_over():
    payload = bytes(256)
    whole = pack(1, 1, payload)
    frames, rest = iter_frames(whole[:100], 256)
    assert frames == [] and len(rest) == 100
    frames, rest = iter_frames(rest + whole[100:], 256)
    assert len(frames) == 1 and rest == b""


def test_resyncs_after_garbage():
    payload = bytes(256)
    stream = b"\x00" * 7 + pack(9, 9, payload)
    frames, _ = iter_frames(stream, 256)
    assert [f.seq for f in frames] == [9]
