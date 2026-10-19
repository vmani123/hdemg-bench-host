"""The TCP receiver takes a new connection the moment one arrives (Report 3 §9, fault a).

17 Oct: the C5 dropped off Wi-Fi, its connection died without a reset reaching the Mac,
and the receiver waited on the dead connection for the rest of the run while the board's
new connections queued unaccepted. A new connection must replace the old one at once."""
import socket
import time

from bench import frame
from bench.frame import HDR_BYTES
from bench.receiver import Receiver

PAYLOAD = 270 - HDR_BYTES


def _frames(seq0: int, n: int) -> bytes:
    t = int(time.perf_counter() * 1e6) & 0xFFFFFFFF
    return b"".join(frame.pack(seq0 + i, t, bytes(PAYLOAD)) for i in range(n))


def _run(long_run: bool, reconnect: bool, port: int):
    rx = Receiver("tcp", port, PAYLOAD, duration_s=3.0, discard_s=0.0, long_run=long_run,
                  reconnect=reconnect)
    rx.start()
    a = socket.create_connection(("127.0.0.1", port))
    a.sendall(_frames(1, 50))
    time.sleep(0.3)
    # The old connection goes silent and is never closed: a dead link.
    b = socket.create_connection(("127.0.0.1", port))
    for k in range(10):
        b.sendall(_frames(51 + 20 * k, 20))
        time.sleep(0.1)
    m = rx.join(timeout=10)
    a.close()
    b.close()
    return m


def test_new_connection_replaces_a_silent_one_short_run():
    m = _run(long_run=False, reconnect=False, port=15531)
    assert m.frames == 250, m.frames


def test_new_connection_replaces_a_silent_one_long_run():
    m = _run(long_run=True, reconnect=True, port=15532)
    assert m.frames == 250, m.frames
    assert m.connections == 2
    # the stream stopped ~1 s into a 3 s run: the tail silence is the longest gap
    assert m.longest_gap_s > 1.0, m.longest_gap_s
