"""Host-side receiver and metric extraction (spec §2).

Counts goodput at exactly one place — the moment a whole frame is parsed out of the
socket buffer. That counting point is part of the measurement contract; if it moves,
numbers from before and after the move are not comparable.

Two modes share that counting point:

  * SHORT runs (the 20-60 s sweep and tuning runs) keep every arrival in Python lists
    and compute exactly what they always have, so numbers stay comparable with every
    earlier session.
  * LONG runs (soaks, 30 min and more) cannot: an hour at T2 is ~69 M frames, which as
    Python objects is tens of GB. They keep two compact float32 arrays per frame and a
    sequence bitmap instead (~8 B per frame), and two things only a long run needs:
      - per-window records (default 10 s): goodput, loss, latency, the longest silence;
      - drift-corrected latency. The device's clock and this host's differ by tens of
        ppm, which over an hour is a ramp of tens of ms in (arrival - t_src). A short run
        subtracts one minimum and lives with a ramp of a fraction of a millisecond; a
        long one fits the ramp to the lower envelope of the delays and removes it.

A TCP receiver normally ends when its one connection closes. With `reconnect` it keeps
accepting new connections until the deadline instead, which is what a stress run needs:
the device's connection is reset on purpose (restart_connection) or breaks when its link
is dropped, and the same run must keep counting through the reconnect. pause() stops
reading for a while, which for TCP closes the receive window: the sender's data piles up
in its own memory, the stress the soak tests exist to apply.
"""
from __future__ import annotations
import socket
import statistics
import struct
import threading
import time
from array import array
from dataclasses import dataclass, field

from .frame import HDR_BYTES, iter_frames

U32 = 1 << 32
LONG_RUN_S = 120.0            # above this hold, a run is measured in long mode


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
    # Long mode / stress only (None or empty otherwise).
    latency_method: str = "min-subtracted"
    latency_max_us: float | None = None
    clock_skew_ppm: float | None = None
    longest_gap_s: float | None = None
    connections: int | None = None
    windows: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)

    def as_dict(self, include_windows: bool = False) -> dict:
        d = self.__dict__.copy()
        d["per_second"] = [round(v, 1) for v in self.per_second]
        if not include_windows:
            # Windows belong in the run's time-series file, not in every ledger line.
            d.pop("windows", None)
        return d


class Receiver:
    """Run in a thread; `metrics` is valid once `join()` returns."""

    def __init__(self, transport: str, port: int, payload_bytes: int,
                 duration_s: float, discard_s: float = 3.0,
                 bind_host: str = "0.0.0.0", *, long_run: bool | None = None,
                 window_s: float = 10.0, reconnect: bool = False,
                 tcp_rcvbuf: int | None = None):
        self.transport = transport
        self.port = port
        self.payload_bytes = payload_bytes
        self.duration_s = duration_s
        self.discard_s = discard_s
        self.bind_host = bind_host
        self.long_run = (duration_s > LONG_RUN_S) if long_run is None else bool(long_run)
        self.window_s = float(window_s)
        self.reconnect = bool(reconnect)
        # A small TCP receive buffer makes a pause bite at once: macOS autotunes the
        # buffer to megabytes, which at 4.32 Mbit/s would absorb a several-second pause
        # without the sender ever seeing its window close. Stress runs only.
        self.tcp_rcvbuf = tcp_rcvbuf
        # A run that never sees a frame must still end and free its port: the device
        # may be wedged, and the next run binds the same port.
        self._give_up = time.perf_counter() + duration_s + max(60.0, discard_s + 30.0)
        self.metrics = RunMetrics(transport=transport, payload_bytes=payload_bytes)
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pause_until = 0.0
        self._restart = False
        self._t0: float | None = None          # perf_counter() of the first frame

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def join(self, timeout: float | None = None) -> RunMetrics:
        if self._thread:
            self._thread.join(timeout if timeout is not None
                              else self.duration_s + self.discard_s + 10.0)
        return self.metrics

    # -- stress hooks (called from another thread) -----------------------------------
    def elapsed_s(self) -> float | None:
        """Seconds since the first frame arrived, or None before it."""
        return None if self._t0 is None else time.perf_counter() - self._t0

    def pause(self, seconds: float) -> None:
        """Stop reading for `seconds`. TCP: the receive window closes and the sender's
        unsent data accumulates in ITS memory. UDP: datagrams pile up in, then overflow,
        this host's socket buffer — the device never notices, which is why a UDP stress
        run drops the device's link instead."""
        with self._lock:
            self._pause_until = time.perf_counter() + float(seconds)
        self._event("pause", seconds=float(seconds))

    def restart_connection(self) -> None:
        """TCP: reset the current connection (RST, as a crashed or restarted receiving
        application would) and accept the device's next one. UDP: nothing to reset."""
        with self._lock:
            self._restart = True
        self._event("restart")

    def _event(self, kind: str, **kw) -> None:
        self.metrics.events.append({"t_s": None if self._t0 is None else
                                    round(time.perf_counter() - self._t0, 3),
                                    "kind": kind, **kw})

    def _paused(self) -> bool:
        with self._lock:
            return time.perf_counter() < self._pause_until

    def _take_restart(self) -> bool:
        with self._lock:
            r, self._restart = self._restart, False
            return r

    # -- internals ---------------------------------------------------------------------
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
        col = self._collector()
        try:
            while not col.done():
                if self._paused():
                    time.sleep(0.02)
                    continue
                try:
                    chunk = s.recvfrom(65535)[0]
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                col.feed(chunk)
        finally:
            s.close()
            col.finish()

    def _run_tcp(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if self.tcp_rcvbuf:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(self.tcp_rcvbuf))
        srv.bind((self.bind_host, self.port))
        srv.listen(4)
        srv.settimeout(15.0)
        self._ready.set()
        col = self._collector()
        conn = None
        connections = 0
        try:
            conn, _ = srv.accept()            # the first connection: as before, 15 s
            conn.settimeout(0.5)
            connections = 1
            srv.settimeout(0.5)
            while not col.done():
                if conn is None:
                    try:
                        conn, _ = srv.accept()
                    except socket.timeout:
                        continue
                    conn.settimeout(0.5)
                    connections += 1
                    col.new_stream()
                    self._event("connected", n=connections)
                if self._take_restart():
                    _reset(conn)
                    conn = None
                    col.new_stream()
                    continue
                if self._paused():
                    time.sleep(0.02)
                    continue
                try:
                    chunk = conn.recv(1 << 16)
                except socket.timeout:
                    continue
                except OSError as e:
                    if not self.reconnect:
                        break
                    self._event("conn_error", error=str(e))
                    _close(conn)
                    conn = None
                    continue
                if not chunk:
                    if not self.reconnect:
                        break
                    self._event("conn_closed")
                    _close(conn)
                    conn = None
                    continue
                col.feed(chunk)
        finally:
            if conn is not None:
                _close(conn)
            srv.close()
            self.metrics.connections = connections if (self.reconnect or self.long_run) \
                else None
            col.finish()

    def _collector(self):
        cls = _LongCollector if self.long_run else _ShortCollector
        return cls(self)


def _reset(conn: socket.socket) -> None:
    """Close with RST rather than FIN, as a crashed receiving application would."""
    try:
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except OSError:
        pass
    _close(conn)


def _close(conn: socket.socket) -> None:
    try:
        conn.close()
    except OSError:
        pass


class _ShortCollector:
    """Exactly the original measurement: every arrival kept in lists."""

    def __init__(self, rx: Receiver):
        self.rx = rx
        self.frame_len = HDR_BYTES + rx.payload_bytes
        self.buf = b""
        self.arrivals: list[tuple[int, float]] = []     # (t_src, arrival_us)
        self.seqs: list[int] = []
        self.per_sec: list[float] = []
        self.arrival_rel: list[float] = []              # seconds since t_start, per frame
        self.t_start = None
        self.t_bucket = None
        self.bucket_bytes = 0
        self.frames = 0
        self.deadline = None
        self.last_seq = None
        self.reorder = 0

    def done(self) -> bool:
        now = time.perf_counter()
        if self.deadline is None:
            return now >= self.rx._give_up
        return now >= self.deadline

    def new_stream(self) -> None:
        self.buf = b""            # a partial frame from the old connection is gone

    def feed(self, chunk: bytes) -> None:
        arrival_us = time.perf_counter() * 1e6
        self.buf += chunk
        got, self.buf = iter_frames(self.buf, self.rx.payload_bytes)
        if not got:
            return
        if self.t_start is None:
            self.t_start = time.perf_counter()
            self.rx._t0 = self.t_start
            self.t_bucket = self.t_start
            self.deadline = self.t_start + self.rx.duration_s
        rel = time.perf_counter() - self.t_start
        for f in got:
            self.frames += 1
            self.bucket_bytes += self.frame_len
            self.seqs.append(f.seq)
            self.arrivals.append((f.t_src, arrival_us))
            self.arrival_rel.append(rel)
            if self.last_seq is not None and f.seq < self.last_seq:
                self.reorder += 1
            self.last_seq = f.seq
        # roll the 1 s bucket
        nowp = time.perf_counter()
        while self.t_bucket is not None and nowp - self.t_bucket >= 1.0:
            self.per_sec.append(self.bucket_bytes * 8.0)
            self.bucket_bytes = 0
            self.t_bucket += 1.0

    def finish(self) -> None:
        m = self.rx.metrics
        m.frames = self.frames
        m.per_second = self.per_sec
        if not self.seqs or self.t_start is None:
            m.error = m.error or "no frames received"
            return
        elapsed = max(1e-6, (self.deadline or time.perf_counter()) - self.t_start)
        m.duration_s = round(elapsed, 3)
        m.first_seq, m.last_seq = self.seqs[0], max(self.seqs)
        m.expected_frames = m.last_seq - m.first_seq + 1
        m.lost_frames = max(0, m.expected_frames - len(set(self.seqs)))
        m.loss_pct = (100.0 * m.lost_frames / m.expected_frames) if m.expected_frames else 0.0
        m.reorder_count = self.reorder

        # Headline goodput is computed from the total bytes that landed inside the
        # measured window, not from an average of whole-second buckets: bucket
        # averaging silently drops every frame after the last complete second, which
        # on a short run is a large fraction and on any run biases the number low.
        # per_second remains a series, for the curve only.
        d = min(float(self.rx.discard_s), max(0.0, elapsed - 0.1))
        in_window = [t for t in self.arrival_rel if t >= d]
        window_s = max(1e-6, elapsed - d)
        m.goodput_bps = len(in_window) * self.frame_len * 8.0 / window_s

        lat = _latencies_us(self.arrivals)
        if lat:
            lat.sort()
            m.latency_p50_us = round(_pct(lat, 50), 1)
            m.latency_p99_us = round(_pct(lat, 99), 1)
            m.jitter_us = round(statistics.pstdev(lat), 1) if len(lat) > 1 else 0.0


class _LongCollector:
    """Bounded per-frame state: a seq bitmap and two float32 arrays, windows at the end."""

    def __init__(self, rx: Receiver):
        self.rx = rx
        self.frame_len = HDR_BYTES + rx.payload_bytes
        self.buf = b""
        self.t_start = None
        self.deadline = None
        self.frames = 0
        self.bucket_bytes = 0
        self.t_bucket = None
        self.per_sec: list[float] = []
        # seq bookkeeping (32-bit counter, unwrapped)
        self.seq_base: int | None = None
        self.seq_wraps = 0
        self.prev_seq_raw = None
        self.bits = bytearray()
        self.max_seq = -1                # highest seq offset seen (relative to the first)
        self.reorder = 0
        self.uniques = 0
        # t_src unwrap and per-frame arrays
        self.src_wraps = 0
        self.prev_src = None
        self.d0: float | None = None
        self.delay = array("f")          # (arrival_us - t_src_unwrapped) - d0
        self.trel = array("f")           # arrival, seconds since t_start
        # per-window loss bookkeeping (gaps opened / filled while that window was open)
        self.win_gaps: dict[int, int] = {}
        self.win_late: dict[int, int] = {}
        self.last_arrival = None
        self.longest_gap = 0.0

    def done(self) -> bool:
        now = time.perf_counter()
        if self.deadline is None:
            return now >= self.rx._give_up
        return now >= self.deadline

    def new_stream(self) -> None:
        self.buf = b""

    def feed(self, chunk: bytes) -> None:
        now = time.perf_counter()
        arrival_us = now * 1e6
        self.buf += chunk
        got, self.buf = iter_frames(self.buf, self.rx.payload_bytes)
        if not got:
            return
        if self.t_start is None:
            self.t_start = now
            self.rx._t0 = now
            self.t_bucket = now
            self.deadline = now + self.rx.duration_s
        rel = now - self.t_start
        if self.last_arrival is not None:
            self.longest_gap = max(self.longest_gap, now - self.last_arrival)
        self.last_arrival = now
        win = int(rel // self.rx.window_s)
        for f in got:
            self.frames += 1
            self.bucket_bytes += self.frame_len
            # -- sequence: unwrap, then mark in the bitmap
            s = f.seq
            if self.prev_seq_raw is not None and s < self.prev_seq_raw - (U32 // 2):
                self.seq_wraps += 1
            self.prev_seq_raw = s
            su = s + self.seq_wraps * U32
            if self.seq_base is None:
                self.seq_base = su
            off = su - self.seq_base
            if off < 0:
                self.reorder += 1          # older than the first frame: counted, not lost
            else:
                if off > self.max_seq + 1:
                    self.win_gaps[win] = self.win_gaps.get(win, 0) + (off - self.max_seq - 1)
                elif off <= self.max_seq:
                    self.reorder += 1
                    self.win_late[win] = self.win_late.get(win, 0) + 1
                need = off // 8 + 1
                if need > len(self.bits):
                    self.bits.extend(b"\0" * max(need - len(self.bits), 1 << 16))
                byte, bit = divmod(off, 8)
                if not self.bits[byte] & (1 << bit):
                    self.bits[byte] |= (1 << bit)
                    self.uniques += 1
                if off > self.max_seq:
                    self.max_seq = off
            # -- delay: unwrap t_src, keep relative to the first frame's delay
            t = f.t_src
            if self.prev_src is not None and t < self.prev_src - (U32 // 2):
                self.src_wraps += 1
            self.prev_src = t
            d = arrival_us - (t + self.src_wraps * U32)
            if self.d0 is None:
                self.d0 = d
            self.delay.append(d - self.d0)
            self.trel.append(rel)
        while self.t_bucket is not None and now - self.t_bucket >= 1.0:
            self.per_sec.append(self.bucket_bytes * 8.0)
            self.bucket_bytes = 0
            self.t_bucket += 1.0

    def finish(self) -> None:
        import numpy as np
        rx, m = self.rx, self.rx.metrics
        m.frames = self.frames
        m.per_second = self.per_sec
        m.latency_method = "drift-corrected (lower-envelope fit)"
        if self.frames == 0 or self.t_start is None:
            m.error = m.error or "no frames received"
            return
        elapsed = max(1e-6, (self.deadline or time.perf_counter()) - self.t_start)
        m.duration_s = round(elapsed, 3)
        m.first_seq = int(self.seq_base % U32)
        m.last_seq = int((self.seq_base + self.max_seq) % U32)
        m.expected_frames = self.max_seq + 1
        m.lost_frames = max(0, m.expected_frames - self.uniques)
        m.loss_pct = 100.0 * m.lost_frames / m.expected_frames if m.expected_frames else 0.0
        m.reorder_count = self.reorder
        m.longest_gap_s = round(self.longest_gap, 3)

        trel = np.frombuffer(self.trel, dtype=np.float32).astype(np.float64)
        delay = np.frombuffer(self.delay, dtype=np.float32).astype(np.float64)
        d = min(float(rx.discard_s), max(0.0, elapsed - 0.1))
        in_win = int(np.count_nonzero(trel >= d))
        m.goodput_bps = in_win * self.frame_len * 8.0 / max(1e-6, elapsed - d)

        lat, slope = _detrend(trel, delay)
        m.clock_skew_ppm = round(slope, 2) if slope is not None else None   # µs/s = ppm
        if lat.size:
            m.latency_p50_us = round(float(np.percentile(lat, 50)), 1)
            m.latency_p99_us = round(float(np.percentile(lat, 99)), 1)
            m.latency_max_us = round(float(lat.max()), 1)
            m.jitter_us = round(float(lat.std()), 1)

        # Per-window records: what a soak is actually read from.
        w = rx.window_s
        nwin = int(np.ceil(elapsed / w)) if elapsed > 0 else 0
        idx = np.minimum((trel // w).astype(np.int64), max(0, nwin - 1))
        counts = np.bincount(idx, minlength=nwin) if nwin else np.zeros(0, dtype=np.int64)
        wins = []
        for i in range(nwin):
            sel = lat[idx == i] if lat.size else lat
            span = min(w, elapsed - i * w)
            lost = max(0, self.win_gaps.get(i, 0) - self.win_late.get(i, 0))
            n = int(counts[i])
            wins.append({
                "t_s": round(i * w, 1),
                "frames": n,
                "goodput_bps": round(n * self.frame_len * 8.0 / max(1e-6, span), 1),
                "lost": lost,
                "loss_pct": round(100.0 * lost / max(1, n + lost), 4),
                "lat_p50_us": round(float(np.percentile(sel, 50)), 1) if sel.size else None,
                "lat_p99_us": round(float(np.percentile(sel, 99)), 1) if sel.size else None,
                "lat_max_us": round(float(sel.max()), 1) if sel.size else None,
            })
        m.windows = wins


def _detrend(t, delay):
    """Remove the clock-skew ramp from one-way delays.

    The fastest frames in any stretch of the run are the ones that met no queue, so the
    LOWER ENVELOPE of (arrival - t_src) against time traces the clock skew alone. Take
    the minimum delay in each of ~1 s bins, fit a straight line through those minima
    robustly (Theil-Sen: the median of pairwise slopes, then the median intercept), shift
    it down onto the envelope, and subtract. Returns (latency >= 0 relative to the
    fastest frame after correction, slope in µs per s = ppm) — slope None if the run was
    too short to fit."""
    import numpy as np
    if delay.size == 0:
        return delay, None
    span = float(t[-1] - t[0]) if t.size > 1 else 0.0
    if span < 20.0:
        return delay - delay.min(), None
    nb = int(min(600, max(20, span)))
    edges = np.linspace(t.min(), t.max() + 1e-9, nb + 1)
    b = np.digitize(t, edges) - 1
    xs, ys = [], []
    for i in range(nb):
        sel = b == i
        if np.any(sel):
            j = np.argmin(np.where(sel, delay, np.inf))
            xs.append(t[j])
            ys.append(delay[j])
    xs, ys = np.asarray(xs), np.asarray(ys)
    if xs.size < 3:
        return delay - delay.min(), None
    i, j = np.triu_indices(xs.size, 1)
    dx = xs[j] - xs[i]
    ok = dx > 0
    slope = float(np.median((ys[j] - ys[i])[ok] / dx[ok])) if np.any(ok) else 0.0
    corrected = delay - slope * t
    return corrected - corrected.min(), slope


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
