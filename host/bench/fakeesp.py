"""A host-side stand-in for an ESP running bench_fw (spec §8).

Speaks the real control protocol and emits real frames over loopback, so the entire
harness — sweep logic, knee detection, receiver, gates, ledger, resume, report — is
exercised with zero hardware. This is the thing that keeps the first bench session from
being spent debugging Python.

It models three behaviours that matter for testing the harness:
  * a link ceiling, above which offered load is shed (producing a knee and real loss);
  * a soft shoulder below the ceiling, so the knee is a curve rather than a cliff;
  * a CPU-idle curve, so the plan §2.3 discriminator has something to discriminate.
"""
from __future__ import annotations
import json
import os
import random
import socket
import threading
import time

from .control import CONTROL_PORT, PROTOCOL_VERSION
from .frame import HDR_BYTES, pack

U32 = 1 << 32


class FakeESP:
    def __init__(self, chip: str = "esp32c5", *, control_port: int = CONTROL_PORT,
                 ceiling_mbps: float = 45.0, cpu_bound_mbps: float | None = None,
                 idf: str = "v6.0.2", fw_sha: str = "fake0000",
                 rung: str = "r0-baseline", band: str = "5", seed: int = 0):
        self.chip = chip
        self.control_port = control_port
        self.ceiling_bps = ceiling_mbps * 1e6
        # Load at which CPU idle reaches zero. None => never CPU-bound (RF-limited part).
        self.cpu_bound_bps = cpu_bound_mbps * 1e6 if cpu_bound_mbps else None
        self.idf = idf
        self.fw_sha = fw_sha
        self.rung = rung
        self.band = band
        self.rng = random.Random(seed)

        self.cfg: dict = {"rate_bps": 0, "transport": "udp", "dst": "127.0.0.1:3333",
                          "payload": "rand", "dur_s": 10, "frame_bytes": 270,
                          "label": "unset"}
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._sender: threading.Thread | None = None
        self._srv: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self.sent_bytes = 0
        self.sent_frames = 0
        self.dropped = 0
        self.seq = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._srv = threading.Thread(target=self._serve, daemon=True)
        self._srv.start()
        time.sleep(0.05)

    def shutdown(self) -> None:
        self._stop.set()
        self.stop_stream()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    # -- control plane -----------------------------------------------------
    def _serve(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.control_port))
        s.settimeout(0.3)
        self._sock = s
        while not self._stop.is_set():
            try:
                data, peer = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                reply = self._handle(data.decode().strip())
            except Exception as e:                    # noqa: BLE001
                reply = {"ok": False, "err": f"{type(e).__name__}: {e}"}
            try:
                s.sendto(json.dumps(reply).encode(), peer)
            except OSError:
                pass

    def _handle(self, line: str) -> dict:
        verb, _, rest = line.partition(" ")
        if verb == "hello":
            return {"ok": True, "proto": PROTOCOL_VERSION, "chip": self.chip,
                    "idf": self.idf, "fw_sha": self.fw_sha, "ingress": "synth",
                    "rung": self.rung, "band": self.band, "simulated": True}
        if verb == "cfg":
            for tok in rest.split():
                k, _, v = tok.partition("=")
                if k not in self.cfg:
                    return {"ok": False, "err": f"unknown cfg key '{k}'"}
                self.cfg[k] = _coerce(v)
            return {"ok": True, "applied": dict(self.cfg)}
        if verb == "start":
            self.start_stream()
            return {"ok": True, "t0_us": int(time.perf_counter() * 1e6) % U32}
        if verb == "stop":
            self.stop_stream()
            return {"ok": True, "summary": self._stat()}
        if verb == "stat":
            return {"ok": True, **self._stat()}
        if verb == "reset":
            self.stop_stream()
            self.sent_bytes = self.sent_frames = self.dropped = self.seq = 0
            return {"ok": True}
        return {"ok": False, "err": f"unknown command '{verb}'"}

    def _stat(self) -> dict:
        offered = float(self.cfg["rate_bps"]) or 1.0
        if self.cpu_bound_bps:
            idle = max(0.0, 100.0 * (1.0 - offered / self.cpu_bound_bps))
        else:
            idle = max(25.0, 100.0 - 45.0 * offered / max(self.ceiling_bps, 1.0))
        cores = [round(idle, 1)] if self.chip == "esp32c5" else [round(idle, 1), 85.0]
        return {"bytes": self.sent_bytes, "frames": self.sent_frames,
                "dropped": self.dropped, "idle_pct": cores,
                "retries": int(self.sent_frames * 0.01),
                "rssi": -42, "phy_rate": "HE20 MCS9" if self.band == "5" else "HT20 MCS7",
                "heap": 180000, "achieved_bps": self._achieved_bps()}

    def _achieved_bps(self) -> float:
        offered = float(self.cfg["rate_bps"])
        return min(offered, self.ceiling_bps)

    # -- data plane --------------------------------------------------------
    def start_stream(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._sender = threading.Thread(target=self._send_loop, daemon=True)
        self._sender.start()

    def stop_stream(self) -> None:
        with self._lock:
            self._running = False
        t = self._sender
        if t and t.is_alive():
            t.join(timeout=3.0)

    def _send_loop(self) -> None:
        host, _, port = self.cfg["dst"].partition(":")
        port = int(port)
        frame_bytes = int(self.cfg["frame_bytes"])
        payload_bytes = frame_bytes - HDR_BYTES
        payload = os.urandom(payload_bytes)
        offered = float(self.cfg["rate_bps"])
        if offered <= 0:
            return

        tcp = self.cfg["transport"] == "tcp"
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
        try:
            if tcp:
                s.connect((host, port))
        except OSError:
            with self._lock:
                self._running = False
            return

        # Shed load above the ceiling, with a soft shoulder beneath it so the
        # loss-vs-load curve has a knee instead of a cliff.
        util = offered / self.ceiling_bps
        if util <= 0.85:
            deliver_frac = 1.0
        elif util <= 1.0:
            deliver_frac = 1.0 - 0.06 * (util - 0.85) / 0.15
        else:
            deliver_frac = min(1.0, self.ceiling_bps / offered) * 0.985

        interval = frame_bytes * 8.0 / offered
        t0 = time.perf_counter()
        deadline = t0 + float(self.cfg["dur_s"]) + 1.0
        next_at = t0
        n = 0
        while True:
            with self._lock:
                if not self._running:
                    break
            now = time.perf_counter()
            if now >= deadline:
                break
            if now < next_at:
                sleep = next_at - now
                if sleep > 0.0008:
                    time.sleep(sleep * 0.9)
                continue
            n += 1
            next_at = t0 + n * interval
            self.seq += 1
            if self.rng.random() > deliver_frac:
                self.dropped += 1
                continue
            t_src = int(time.perf_counter() * 1e6) % U32
            buf = pack(self.seq, t_src, payload)
            try:
                if tcp:
                    s.sendall(buf)
                else:
                    s.sendto(buf, (host, port))
            except OSError:
                break
            self.sent_frames += 1
            self.sent_bytes += frame_bytes
        try:
            s.close()
        except OSError:
            pass
        with self._lock:
            self._running = False


def _coerce(v: str):
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v
