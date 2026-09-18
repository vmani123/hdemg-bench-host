"""Host-side receiver and metric extraction (spec §2).

Counts goodput at exactly one place — the moment a whole frame is parsed out of the
socket buffer. That counting point is part of the measurement contract; if it moves,
numbers from before and after the move are not comparable.
"""
from __future__ import annotations
import socket
import statistics
import threading
import time
from dataclasses import dataclass, field

from .frame import HDR_BYTES, iter_frames

U32 = 1 << 32


@dataclass
class RunMetrics:
    transport: str = ""
    duration_s: float = 0.0
    frames: int = 0
    payload_bytes: int = 0
    goodput_bps: float = 0.0
    expected_frames: int = 0
    lost_frames: int = 0
    loss_pct: float = 0.0
    reorder_count: int = 0
    latency_p50_us: float | None = None
    latency_p99_us: float | None = None
    jitter_us: float | None = None
    per_second: list[float] = field(default_factory=list)
    first_seq: int | None = None
    last_seq: int | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["per_second"] = [round(v, 1) for v in self.per_second]
        return d


class Receiver:
    """Run in a thread; `metrics` is valid once `join()` returns."""

    def __init__(self, transport: str, port: int, payload_bytes: int,
                 duration_s: float, discard_s: float = 3.0,
                 bind_host: str = "0.0.0.0"):
        self.transport = transport
        self.port = port
        self.payload_bytes = payload_bytes
        self.duration_s = duration_s
        self.discard_s = discard_s
        self.bind_host = bind_host
        self.metrics = RunMetrics(transport=transport, payload_bytes=payload_bytes)
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def join(self, timeout: float | None = None) -> RunMetrics:
        if self._thread:
            self._thread.join(timeout if timeout is not None
                              else self.duration_s + self.discard_s + 10.0)
        return self.metrics

    # -- internals ---------------------------------------------------------
    def _run(self) -> None:
        try:
            if self.transport == "udp":
                self._run_udp()
            else:
                self._run_tcp()
        except Exception as e:                       # noqa: BLE001 - reported, not raised
            self.metrics.error = f"{type(e).__name__}: {e}"
            self._ready.set()

    def _run_udp(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        s.bind((self.bind_host, self.port))
        s.settimeout(0.5)
        self._ready.set()
        self._collect(lambda: s.recvfrom(65535)[0], s)

    def _run_tcp(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind_host, self.port))
        srv.listen(1)
        srv.settimeout(15.0)
        self._ready.set()
        conn, _ = srv.accept()
        conn.settimeout(0.5)
        try:
            self._collect(lambda: conn.recv(1 << 16), conn)
        finally:
            conn.close()
            srv.close()

    def _collect(self, recv, sock) -> None:
        payload = self.payload_bytes
        frame_len = HDR_BYTES + payload
        buf = b""
        arrivals: list[tuple[int, float]] = []     # (t_src, arrival_us)
        seqs: list[int] = []
        per_sec: list[float] = []

        t_start = None
        t_bucket = None
        bucket_bytes = 0
        frames = 0
        deadline = None
        last_seq = None
        reorder = 0
        arrival_rel: list[float] = []      # seconds since t_start, per frame

        while True:
            now = time.perf_counter()
            if deadline is not None and now >= deadline:
                break
            try:
                chunk = recv()
            except socket.timeout:
                if t_start is None:
                    continue
                if deadline is not None and time.perf_counter() >= deadline:
                    break
                continue
            except OSError:
                break
            if not chunk:
                break
            arrival_us = time.perf_counter() * 1e6
            buf += chunk
            got, buf = iter_frames(buf, payload)
            if not got:
                continue
            if t_start is None:
                t_start = time.perf_counter()
                t_bucket = t_start
                deadline = t_start + self.duration_s
            rel = time.perf_counter() - t_start
            for f in got:
                frames += 1
                bucket_bytes += frame_len
                seqs.append(f.seq)
                arrivals.append((f.t_src, arrival_us))
                arrival_rel.append(rel)
                if last_seq is not None and f.seq < last_seq:
                    reorder += 1
                last_seq = f.seq
            # roll the 1 s bucket
            nowp = time.perf_counter()
            while t_bucket is not None and nowp - t_bucket >= 1.0:
                per_sec.append(bucket_bytes * 8.0)
                bucket_bytes = 0
                t_bucket += 1.0

        try:
            sock.close()
        except OSError:
            pass

        m = self.metrics
        m.frames = frames
        m.per_second = per_sec
        if not seqs or t_start is None:
            m.error = m.error or "no frames received"
            return

        elapsed = max(1e-6, (deadline or time.perf_counter()) - t_start)
        m.duration_s = round(elapsed, 3)
        m.first_seq, m.last_seq = seqs[0], max(seqs)
        m.expected_frames = m.last_seq - m.first_seq + 1
        m.lost_frames = max(0, m.expected_frames - len(set(seqs)))
        m.loss_pct = (100.0 * m.lost_frames / m.expected_frames) if m.expected_frames else 0.0
        m.reorder_count = reorder

        # Headline goodput is computed from the total bytes that landed inside the
        # measured window, not from an average of whole-second buckets: bucket
        # averaging silently drops every frame after the last complete second, which
        # on a short run is a large fraction and on any run biases the number low.
        # per_second remains a series, for the curve only.
        d = min(float(self.discard_s), max(0.0, elapsed - 0.1))
        in_window = [t for t in arrival_rel if t >= d]
        window_s = max(1e-6, elapsed - d)
        m.goodput_bps = len(in_window) * frame_len * 8.0 / window_s

        lat = _latencies_us(arrivals)
        if lat:
            lat.sort()
            m.latency_p50_us = round(_pct(lat, 50), 1)
            m.latency_p99_us = round(_pct(lat, 99), 1)
            m.jitter_us = round(statistics.pstdev(lat), 1) if len(lat) > 1 else 0.0


def _latencies_us(arrivals: list[tuple[int, float]]) -> list[float]:
    """Relative one-way latency.

    t_src is a free-running 32-bit counter on the device with an unknown offset from the
    host clock, so absolute latency is not available. Subtracting the minimum observed
    difference gives latency relative to the fastest frame in the run, which is what the
    p50/p99 spread and jitter are computed from. Reported as such — never as absolute.
    """
    if not arrivals:
        return []
    diffs = []
    base_src = arrivals[0][0]
    wraps = 0
    prev = base_src
    for t_src, arr in arrivals:
        if t_src < prev - (U32 // 2):
            wraps += 1
        prev = t_src
        diffs.append(arr - (t_src + wraps * U32))
    floor = min(diffs)
    return [d - floor for d in diffs]


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)
