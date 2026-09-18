"""Mirror of codec/include/hdemg_frame.h — the single wire-format definition.

14-byte packed little-endian header, then payload. This file and the firmware's
hdemg_frame.h are measurement-critical: parity.py enforces that both firmware repos
agree with each other, and test_frame.py pins the layout here.
"""
from __future__ import annotations
import struct
from typing import Iterator, NamedTuple

MAGIC = 0xA55A
TYPE_RAW16 = 0
TYPE_RMS16 = 1
TYPE_COMPRESSED = 2

# magic u16 | type u8 | chip_id u8 | seq u32 | t_stm u32 | n_ch u16
HDR = struct.Struct("<HBBIIH")
HDR_BYTES = HDR.size
assert HDR_BYTES == 14, "header layout changed — this is a contract break"

CHIP_COMBINED = 0xFF
RAW16_128CH_PAYLOAD = 256          # 128 channels x int16
RAW16_128CH_FRAME = HDR_BYTES + RAW16_128CH_PAYLOAD   # 270 bytes


class Frame(NamedTuple):
    type: int
    chip_id: int
    seq: int
    t_src: int
    n_ch: int
    payload: bytes


def pack(seq: int, t_src: int, payload: bytes, *, n_ch: int = 128,
         type_: int = TYPE_RAW16, chip_id: int = CHIP_COMBINED) -> bytes:
    return HDR.pack(MAGIC, type_, chip_id, seq & 0xFFFFFFFF,
                    t_src & 0xFFFFFFFF, n_ch) + payload


def frame_bytes_for(payload_bytes: int) -> int:
    return HDR_BYTES + payload_bytes


def iter_frames(buf: bytes, payload_bytes: int) -> tuple[list[Frame], bytes]:
    """Parse whole frames from a byte buffer. Returns (frames, unconsumed remainder).

    Fixed payload size: the bench stream uses one frame size per run, which is what
    lets a stream-oriented transport (TCP) be reframed without a length field.
    """
    out: list[Frame] = []
    total = HDR_BYTES + payload_bytes
    i = 0
    n = len(buf)
    while n - i >= total:
        magic, type_, chip, seq, t_src, n_ch = HDR.unpack_from(buf, i)
        if magic != MAGIC:
            # Resynchronise: scan forward for the next magic rather than discarding all.
            nxt = buf.find(b"\x5a\xa5", i + 1)
            if nxt < 0:
                return out, b""
            i = nxt
            continue
        out.append(Frame(type_, chip, seq, t_src, n_ch,
                         bytes(buf[i + HDR_BYTES: i + total])))
        i += total
    return out, bytes(buf[i:])
