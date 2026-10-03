"""Association keepalive (harness spec §3.7).

Apple disconnects third-party Personal Hotspot clients after **90 seconds without
traffic**. The gaps between measurement runs — building, flashing, metadata capture, gate
checks, the inter-run settle — routinely exceed that. Without this, a board silently
drops off the hotspot partway through an overnight sweep and every subsequent run fails
for a reason that has nothing to do with the device under test.

The keepalive does the cheapest thing that generates bidirectional traffic: it polls the
device's own control plane. A `stat` request and its reply are a few hundred bytes, far
below any measurement floor, and they exercise the same path the orchestrator depends on
— so a keepalive failure is also an early warning that the device has gone away.

It is **paused during measurement runs**. Even a trivial packet every few seconds is
traffic the receiver would count, and a measurement must not include the instrument.
"""
from __future__ import annotations
import threading
import time
from contextlib import contextmanager

from .control import ControlClient, ControlError

DEFAULT_INTERVAL_S = 30.0      # comfortably inside Apple's 90 s idle window
DEFAULT_TIMEOUT_S = 2.0


class Keepalive:
    """Background association keeper for one device.

    Tracks losses so a re-association can be recorded as run metadata rather than
    quietly absorbed: a run that began just after the device rejoined is a different
    kind of run, and the ledger should say so.
    """

    def __init__(self, client: ControlClient, *, interval_s: float = DEFAULT_INTERVAL_S,
                 on_event=None):
        self.client = client
        self.interval_s = interval_s
        self.on_event = on_event or (lambda *_a, **_k: None)

        self._stop = threading.Event()
        self._paused = threading.Event()
        self._paused.set()                 # starts paused; run_all unpauses
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self.polls = 0
        self.failures = 0
        self.reassociations = 0
        self._was_down = False
        self.last_ok: float | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> "Keepalive":
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="keepalive")
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval_s + 5.0)
            self._thread = None

    def resume(self) -> None:
        self._paused.clear()

    def pause(self) -> None:
        self._paused.set()

    @contextmanager
    def paused(self):
        """Hold the keepalive quiet for the duration of a measurement."""
        was_running = not self._paused.is_set()
        self.pause()
        try:
            yield self
        finally:
            if was_running:
                self.resume()

    # -- state the orchestrator records ------------------------------------
    def take_reassociation(self) -> bool:
        """True once per observed re-association, then cleared.

        Consumed by the next run so the event lands on exactly one ledger record
        instead of being repeated or lost.
        """
        with self._lock:
            if self.reassociations and not self._reported:
                self._reported = True
                return True
        return False

    _reported = True   # nothing to report until a drop is actually seen

    def snapshot(self) -> dict:
        with self._lock:
            return {"polls": self.polls, "failures": self.failures,
                    "reassociations": self.reassociations,
                    "seconds_since_ok": (time.time() - self.last_ok)
                    if self.last_ok else None}

    # -- internals ---------------------------------------------------------
    TICK_S = 0.05

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(self.TICK_S)
                continue
            self._poll_once()
            # Sleep in slices rather than one long wait, so a pause takes effect within
            # a tick instead of up to a full interval later. A keepalive packet landing
            # inside a measurement window would be counted by the receiver.
            deadline = time.monotonic() + self.interval_s
            while (time.monotonic() < deadline and not self._stop.is_set()
                   and not self._paused.is_set()):
                self._stop.wait(self.TICK_S)

    def _poll_once(self) -> None:
        try:
            self.client.stat()
        except (ControlError, OSError) as e:
            with self._lock:
                self.polls += 1
                self.failures += 1
                self._was_down = True
            self.on_event("keepalive_lost", error=str(e))
            return
        with self._lock:
            self.polls += 1
            self.last_ok = time.time()
            if self._was_down:
                self._was_down = False
                self.reassociations += 1
                self._reported = False
        if self.reassociations and not self._reported:
            self.on_event("keepalive_reassociated",
                          count=self.reassociations)


class NullKeepalive:
    """Stand-in for runs with no device to keep alive (the loopback simulator)."""

    def start(self): return self
    def stop(self): pass
    def resume(self): pass
    def pause(self): pass

    @contextmanager
    def paused(self):
        yield self

    def take_reassociation(self) -> bool: return False
    def snapshot(self) -> dict: return {"polls": 0, "failures": 0, "reassociations": 0,
                                        "seconds_since_ok": None}
