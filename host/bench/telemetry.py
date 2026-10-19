"""Device telemetry while a run streams: one `stat` per period, and what it adds up to.

A soak is judged on how the board's memory and counters MOVE over half an hour, not on
one reading at the end: a leak is a slope, fragmentation is the largest free block
shrinking while the free total holds, a near-exhaustion is a dip a single read would
miss. The sampler polls on its own short-timeout client (control.quick), so a board that
is rejoining after a link drop costs it a missed sample, never a stalled run.
"""
from __future__ import annotations
import statistics
import threading
import time

COUNTERS = ("udp_enomem", "send_errs", "tcp_opens", "tcp_open_fails", "wifi_disc",
            "linkdrops")


class Sampler:
    def __init__(self, client, period_s: float = 1.0):
        self.client = client
        self.period_s = float(period_s)
        self.samples: list[dict] = []
        self._stop = threading.Event()
        self._t0 = None
        self._last_ok: float | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "Sampler":
        self._t0 = time.perf_counter()
        self._last_ok = self._t0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> list[dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        return self.samples

    def unanswered_s(self) -> float:
        """Seconds since the board last answered a sample (since start() if never)."""
        return time.perf_counter() - (self._last_ok or time.perf_counter())

    def _run(self) -> None:
        nxt = time.perf_counter()
        while not self._stop.is_set():
            t = round(time.perf_counter() - self._t0, 2)
            try:
                st = self.client.stat()
                self._last_ok = time.perf_counter()
                st.pop("ok", None)
                self.samples.append({"t_s": t, **st})
            except Exception as e:                        # noqa: BLE001
                self.samples.append({"t_s": t, "error": f"{type(e).__name__}: {e}"})
            nxt += self.period_s
            self._stop.wait(max(0.0, nxt - time.perf_counter()))


def summarize(samples: list[dict]) -> dict:
    """What the samples add up to. Counter deltas survive a reboot (a counter that falls
    back means the board restarted: the segment before it is added, not lost)."""
    good = [s for s in samples if "error" not in s]
    out: dict = {"samples": len(samples), "missed": len(samples) - len(good)}
    if not good:
        return out

    def series(k):
        return [s[k] for s in good if isinstance(s.get(k), (int, float)) and s[k] >= 0]

    lows = series("heap_win_min") + series("heap_min")
    out["heap_low_B"] = min(lows) if lows else None
    for k, name in (("heap", "heap_free_min_B"), ("heap_largest", "heap_largest_min_B"),
                    ("psram_min", "psram_low_B")):
        v = series(k)
        out[name] = min(v) if v else None
    # Leak indicator: least-squares slope of free heap against time, in bytes per hour.
    # Only over the second half of the run, so start-up allocations do not read as a leak.
    pts = [(s["t_s"], s["heap"]) for s in good if isinstance(s.get("heap"), (int, float))]
    pts = pts[len(pts) // 2:]
    if len(pts) >= 10:
        mt = statistics.fmean(p[0] for p in pts)
        mh = statistics.fmean(p[1] for p in pts)
        var = sum((p[0] - mt) ** 2 for p in pts)
        if var > 0:
            slope = sum((p[0] - mt) * (p[1] - mh) for p in pts) / var
            out["heap_slope_B_per_h"] = round(slope * 3600.0, 1)
    # Reboots: uptime going backwards between consecutive good samples.
    resets, reasons = 0, []
    prev = None
    for s in good:
        up = s.get("uptime_s")
        if isinstance(up, (int, float)) and prev is not None and up < prev:
            resets += 1
            reasons.append(s.get("reset_reason"))
        if isinstance(up, (int, float)):
            prev = up
    out["resets"] = resets
    if reasons:
        out["reset_reasons"] = reasons
    for k in COUNTERS:
        vals = [s.get(k) for s in good if isinstance(s.get(k), (int, float))]
        if len(vals) >= 2:
            d = 0
            for a, b in zip(vals, vals[1:]):
                d += (b - a) if b >= a else b       # counter restarted with the board
            out[f"{k}_delta"] = int(d)
    stacks: dict = {}
    for s in good:
        for task, v in (s.get("stack_min") or {}).items():
            if isinstance(v, (int, float)):
                stacks[task] = min(stacks.get(task, v), v)
    if stacks:
        out["stack_min_B"] = stacks
    rssi = series_any(good, "rssi")
    if rssi:
        out["rssi_dbm"] = {"min": min(rssi), "median": statistics.median(rssi),
                           "max": max(rssi)}
    phys = sorted({s.get("phy_rate") for s in good if s.get("phy_rate")})
    if phys:
        out["phy_rates"] = phys
    # Gaps in the sample series: the board did not answer (a link drop, or worse).
    errs = [s["t_s"] for s in samples if "error" in s]
    if errs:
        out["unreachable_s"] = round(len(errs) * _period(samples), 1)
    return out


def series_any(samples, key):
    return [s[key] for s in samples if isinstance(s.get(key), (int, float)) and s[key] != 0]


def _period(samples: list[dict]) -> float:
    ts = [s["t_s"] for s in samples]
    if len(ts) < 2:
        return 1.0
    return max(0.01, statistics.median(b - a for a, b in zip(ts, ts[1:])))
