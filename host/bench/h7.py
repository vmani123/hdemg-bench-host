"""Clients for the Stage 2 wired-ingress rig (docs/STM32_STAGE2_GUIDE.md §8).

Two small things live here:

  * H7Control — the STM32H745 generator's control plane: line-oriented ASCII over the
    ST-LINK virtual COM port, one JSON object per reply, deliberately the same verbs as
    the ESP's UDP control plane (hello / cfg / start / stat / stop).
  * ingress_stat() — the ESP's ingress counters, served on a UDP port of their own so
    the measurement core's control plane is byte-identical between Stage 1 and Stage 2.

The serial port is opened with the standard library (termios), not pyserial: one fewer
dependency on the bench Mac, and nothing here needs more than raw 115200 8N1.
"""
from __future__ import annotations
import json
import os
import select
import socket
import time

INGRESS_STAT_PORT = 3335
H7_PROTO = 1
DEFAULT_H7_PORT = "cu.usbmodem21103"


class H7Error(RuntimeError):
    """The generator refused a command, or did not answer."""

    def __init__(self, msg: str, reply: dict | None = None):
        super().__init__(msg)
        self.reply = reply or {}


class SerialTransport:
    """Raw 115200 8N1 on a POSIX tty."""

    def __init__(self, device: str, baud: int = 115200):
        import termios
        if not device.startswith("/"):
            device = "/dev/" + device
        self.device = device
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attr = termios.tcgetattr(self.fd)
            speed = getattr(termios, f"B{baud}")
            attr[0] = 0                                           # iflag: raw
            attr[1] = 0                                           # oflag: raw
            attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag: 8N1, no modem lines
            attr[3] = 0                                           # lflag: raw
            attr[4] = speed
            attr[5] = speed
            attr[6][termios.VMIN] = 0
            attr[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attr)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except Exception:
            os.close(self.fd)
            raise
        self._buf = b""

    def write(self, data: bytes) -> None:
        end = time.monotonic() + 2.0
        while data:
            _, w, _ = select.select([], [self.fd], [], max(0.0, end - time.monotonic()))
            if not w:
                raise H7Error(f"serial write to {self.device} timed out")
            n = os.write(self.fd, data)
            data = data[n:]

    def readline(self, timeout: float) -> bytes | None:
        end = time.monotonic() + timeout
        while b"\n" not in self._buf:
            left = end - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([self.fd], [], [], left)
            if not r:
                return None
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            if chunk:
                self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line

    def drain(self) -> None:
        self._buf = b""
        while True:
            r, _, _ = select.select([self.fd], [], [], 0)
            if not r:
                return
            try:
                if not os.read(self.fd, 4096):
                    return
            except BlockingIOError:
                return

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


class H7Control:
    """`transport` needs write(bytes), readline(timeout) -> bytes | None, and optionally
    drain() / close(); tests pass a fake, the bench passes nothing and gets the VCP."""

    def __init__(self, device: str = DEFAULT_H7_PORT, *, transport=None,
                 timeout: float = 2.0, retries: int = 2):
        self.device = device
        self._t = transport
        self.timeout = timeout
        self.retries = retries

    def _transport(self):
        if self._t is None:
            try:
                self._t = SerialTransport(self.device)
            except OSError as e:
                raise H7Error(f"cannot open the H7 serial port {self.device}: {e}") from e
        return self._t

    def close(self) -> None:
        if self._t is not None and hasattr(self._t, "close"):
            self._t.close()
        self._t = None

    def _rpc(self, line: str, timeout: float | None = None) -> dict:
        t = self._transport()
        timeout = self.timeout if timeout is None else timeout
        last = "no reply"
        for _ in range(self.retries):
            if hasattr(t, "drain"):
                t.drain()
            t.write(line.encode() + b"\n")
            end = time.monotonic() + timeout
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    break
                raw = t.readline(left)
                if raw is None:
                    break
                try:
                    reply = json.loads(raw.decode(errors="replace").strip())
                except ValueError:
                    last = f"unparseable line {raw[:60]!r}"
                    continue                 # boot noise or a torn line: keep reading
                if not isinstance(reply, dict) or "ok" not in reply:
                    continue
                if reply["ok"] is False:
                    raise H7Error(str(reply.get("err", "generator refused the command")), reply)
                return reply
        raise H7Error(f"no reply from the H7 on {self.device} to {line.split()[0]!r} ({last})")

    def hello(self) -> dict:
        r = self._rpc("hello")
        if r.get("chip") != "stm32h745":
            raise H7Error(f"{self.device} answered as {r.get('chip')!r}, not the H7 generator", r)
        if r.get("proto") != H7_PROTO:
            raise H7Error(f"H7 protocol mismatch: host v{H7_PROTO}, device v{r.get('proto')}", r)
        return r

    def cfg(self, **kw) -> dict:
        parts = " ".join(f"{k}={v}" for k, v in sorted(kw.items()))
        return self._rpc(f"cfg {parts}").get("applied", {})

    def probe(self) -> dict:
        """Open the configured link, check a slave answers, close it again. Raises if not."""
        return self._rpc("probe", timeout=5.0)

    def start(self) -> dict:
        return self._rpc("start", timeout=5.0)

    def stat(self) -> dict:
        return self._rpc("stat")

    def stop(self) -> dict:
        return self._rpc("stop", timeout=3.0).get("summary", {})

    def reboot(self) -> None:
        try:
            self._rpc("reboot")
        except H7Error:
            pass


def ingress_stat(ip: str, port: int = INGRESS_STAT_PORT, timeout: float = 2.0,
                 retries: int = 3) -> dict:
    """The ESP's wired-ingress counters. Raises OSError/ValueError if it does not answer."""
    last: Exception | None = None
    for _ in range(retries):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(b"istat", (ip, port))
            data, _ = s.recvfrom(65535)
            reply = json.loads(data.decode())
            if reply.get("ok") is False:
                raise ValueError(reply.get("err", "ingress stat refused"))
            return reply
        except (socket.timeout, OSError) as e:
            last = e
        finally:
            s.close()
    raise OSError(f"no ingress stat from {ip}:{port}: {last}")


# ---- simulation ------------------------------------------------------------------------

class FakeH7:
    """In-process stand-in for the generator, for tests and `bench sim`: same replies,
    no serial port. `faults` lets a test inject what a broken rig would report."""

    def __init__(self, faults: dict | None = None):
        self.faults = dict(faults or {})
        self.cfg_kv: dict = {"rate_bps": 0, "dur_s": 62, "link": "none", "seed": 0}
        self.running = False
        self._t0 = 0.0
        self._last: dict = {}
        self.starts = 0

    def close(self) -> None:
        pass

    def hello(self) -> dict:
        return {"ok": True, "proto": H7_PROTO, "chip": "stm32h745", "fw_sha": "simh7",
                "state": "running" if self.running else "idle", "simulated": True}

    def cfg(self, **kw) -> dict:
        self.cfg_kv.update(kw)
        return dict(self.cfg_kv)

    def probe(self) -> dict:
        if self.faults.get("no_slave"):
            raise H7Error("qspi: no slave answered (bad magic)")
        return {"ok": True}

    def start(self) -> dict:
        if self.faults.get("no_slave"):
            raise H7Error("qspi: no slave answered (bad magic)")
        self.running = True
        self.starts += 1
        self._t0 = time.monotonic()
        return {"ok": True, "t0_us": 0, "seed": self.cfg_kv.get("seed", 0)}

    def _summary(self) -> dict:
        el = max(1e-6, time.monotonic() - self._t0)
        rate = float(self.cfg_kv.get("rate_bps", 0))
        frames = int(el * rate / (270 * 8))
        drops = int(frames * float(self.faults.get("ring_drop_frac", 0.0)))
        return {"state": "running" if self.running else "done", "link": self.cfg_kv.get("link"),
                "commanded_bps": int(rate), "achieved_bps": int(rate),
                "link_bps": int(rate * (1 - float(self.faults.get("ring_drop_frac", 0.0)))),
                "elapsed_us": int(el * 1e6), "frames": frames, "ring_drops": drops,
                "link_bytes": (frames - drops) * 270,
                "ring_max": 3, "xfers": frames // 30, "credit_waits": drops,
                "ready_timeouts": 0, "link_errors": int(self.faults.get("link_errors", 0)),
                "torn_reads": 0, "credit_errors": 0, "link_hz": 25000000,
                "seed": self.cfg_kv.get("seed", 0)}

    def stat(self) -> dict:
        return self._summary() if self.running else dict(self._last)

    def stop(self) -> dict:
        if self.running:
            self._last = self._summary()
            self.running = False
            self._last["state"] = "done"
        return dict(self._last)

    def reboot(self) -> None:
        self.running = False


def fake_ingress_stat(h7_summary: dict, faults: dict | None = None) -> dict:
    """What a healthy ESP would report for the run the fake H7 just made."""
    faults = faults or {}
    frames = int(h7_summary.get("frames", 0)) - int(h7_summary.get("ring_drops", 0))
    return {"ok": True, "link": h7_summary.get("link", "qspi"), "init_err": 0,
            "in_run": False, "link_test": int(faults.get("link_test", 0)),
            "seed": int(faults.get("seed", h7_summary.get("seed", 0))),
            "bytes_in": frames * 270, "bufs_in": frames // 30, "frames_in": frames,
            "len_errors": int(faults.get("len_errors", 0)),
            "magic_errors": int(faults.get("magic_errors", 0)),
            "seq_gaps": int(h7_summary.get("ring_drops", 0)) + int(faults.get("lost_frames", 0)),
            "seq_gap_events": 0, "seq_backwards": 0,
            "payload_errors": int(faults.get("payload_errors", 0)),
            "pool_starved": 0, "stray_bufs": 0, "loaded": 0, "completed": 0,
            "achieved_bps": int(h7_summary.get("link_bps", 0))}
