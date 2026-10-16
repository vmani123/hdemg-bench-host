"""Long-run receiver mode: bounded memory, windows, drift correction, reconnects, pauses."""
import socket
import sys
import pathlib
import threading
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench.frame import pack                       # noqa: E402
from bench.receiver import Receiver, _detrend      # noqa: E402

PAYLOAD = bytes(256)


class Sender:
    """A minimal device stand-in: paced frames, and over TCP it reconnects after an
    error the way the firmware's sink reopens its socket."""

    def __init__(self, transport, port, rate_fps=2000, skew_ppm=0.0, gap=None):
        self.transport, self.port, self.rate = transport, port, rate_fps
        self.skew = skew_ppm * 1e-6
        self.gap = gap or set()          # seqs to skip (simulated loss)
        self.stop = threading.Event()
        self.seq = 0
        self.connects = 0
        self.t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.t.start()
        return self

    def _sock(self):
        if self.transport == "udp":
            return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while not self.stop.is_set():
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=1.0)
                self.connects += 1
                return s
            except OSError:
                time.sleep(0.05)
        return None

    def _run(self):
        s = self._sock()
        t0 = time.perf_counter()
        n = 0
        while not self.stop.is_set() and s is not None:
            due = t0 + n / self.rate
            now = time.perf_counter()
            if now < due:
                time.sleep(min(0.002, due - now))
                continue
            n += 1
            self.seq += 1
            if self.seq in self.gap:
                continue
            # A device clock running fast by `skew` makes t_src advance faster than ours.
            t_src = int(now * 1e6 * (1.0 + self.skew)) & 0xFFFFFFFF
            buf = pack(self.seq, t_src, PAYLOAD)
            try:
                if self.transport == "udp":
                    s.sendto(buf, ("127.0.0.1", self.port))
                else:
                    s.sendall(buf)
            except OSError:
                try:
                    s.close()
                except OSError:
                    pass
                s = self._sock()
        if s is not None:
            s.close()


def test_long_mode_counts_windows_and_loss():
    port = 16101
    rx = Receiver("udp", port, 256, duration_s=3.0, discard_s=0.2, long_run=True,
                  window_s=0.5)
    rx.start()
    tx = Sender("udp", port, rate_fps=2000, gap={500, 501, 502}).start()
    m = rx.join()
    tx.stop.set()
    assert m.error is None
    assert m.latency_method.startswith("drift-corrected")
    assert m.lost_frames == 3
    assert len(m.windows) == 6
    assert sum(w["lost"] for w in m.windows) == 3
    assert all(w["frames"] > 0 for w in m.windows)
    assert m.latency_p50_us is not None and m.latency_p99_us >= m.latency_p50_us
    assert "windows" not in m.as_dict() and m.as_dict(include_windows=True)["windows"]


def test_short_mode_is_unchanged_by_default():
    port = 16103
    rx = Receiver("udp", port, 256, duration_s=1.0, discard_s=0.2)
    rx.start()
    tx = Sender("udp", port).start()
    m = rx.join()
    tx.stop.set()
    assert m.latency_method == "min-subtracted"
    assert m.windows == [] and m.connections is None


def test_detrend_recovers_a_clock_skew():
    rng = np.random.default_rng(1)
    t = np.linspace(0, 1800, 200_000)
    queue = rng.exponential(400.0, t.size)          # µs of queueing, many near zero
    delay = 35.0 * t + 1000.0 + queue               # 35 ppm ramp
    lat, slope = _detrend(t, delay)
    assert abs(slope - 35.0) < 0.5
    assert lat.min() >= 0
    # with the ramp removed, the median latency is the median queueing delay
    assert abs(np.median(lat) - np.median(queue)) < 30


def test_tcp_reconnect_keeps_counting_through_a_reset():
    port = 16105
    rx = Receiver("tcp", port, 256, duration_s=3.0, discard_s=0.2, reconnect=True,
                  long_run=True, window_s=1.0)
    rx.start()
    tx = Sender("tcp", port, rate_fps=1500).start()
    time.sleep(1.2)
    rx.restart_connection()
    m = rx.join()
    tx.stop.set()
    assert m.error is None
    assert m.connections == 2 and tx.connects == 2
    assert any(e["kind"] == "restart" for e in m.events)
    assert m.frames > 1500 * 2          # frames from both connections were counted


def test_tcp_pause_stops_reading_and_shows_as_a_silence():
    port = 16107
    rx = Receiver("tcp", port, 256, duration_s=3.0, discard_s=0.2, long_run=True,
                  window_s=1.0, tcp_rcvbuf=16384)
    rx.start()
    tx = Sender("tcp", port, rate_fps=1500).start()
    time.sleep(0.8)
    rx.pause(1.0)
    m = rx.join()
    tx.stop.set()
    assert any(e["kind"] == "pause" for e in m.events)
    assert m.longest_gap_s >= 0.8
    assert m.lost_frames == 0           # TCP: a pause delays data, it does not lose it


def test_receiver_gives_up_when_nothing_arrives():
    rx = Receiver("udp", 16109, 256, duration_s=0.5, discard_s=0.1)
    rx._give_up = time.perf_counter() + 1.0
    rx.start()
    m = rx.join(timeout=5.0)
    assert m.error == "no frames received"
