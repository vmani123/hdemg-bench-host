"""A host-side stand-in for an ESP running bench_fw (spec §8).

Speaks the real control protocol and emits real frames over loopback, so the entire
harness — sweep logic, knee detection, receiver, gates, ledger, resume, report — is
exercised with zero hardware. This is the thing that keeps the first bench session from
being spent debugging Python.

It models three behaviours that matter for testing the harness:
  * a link ceiling, above which offered load is shed (producing a knee and real loss);
  * a soft shoulder below the ceiling, so the knee is a curve rather than a cliff;
  * a CPU-idle curve, so the plan §2.3 discriminator has something to discriminate.

Protocol v2 (docs/CAMPAIGN_PROTOCOL.md §1) adds, and this fake follows:
  * cfg keys nodelay / wr_max / dgram. Frames are batched the way the firmware batches
    them — everything due at one instant goes out together, up to a pool buffer — then
    split into datagrams of whole frames up to `dgram` (default 1,472 -> 1,350 B), or
    TCP writes of whole frames up to `wr_max`, with TCP_NODELAY as `nodelay` says;
  * the board-health fields of `stat` and the `stop` summary, with plausible values;
  * `linkdrop <ms>`: the reply goes out, then for ms the control plane is deaf and the
    stream stalls — UDP sends fail (one send error per batch, frames lost), and TCP
    blocks, so the source starves (frames counted as dropped), as on the board;
  * `pins ...`, against an optional `FakeWiring` (host/bench/wiring.py) so the wiring
    check can be run end to end, faults included. Refused unless the build is synth,
    exactly as the firmware refuses it.
"""
from __future__ import annotations
import json
import os
import random
import socket
import threading
import time

from .control import CONTROL_PORT, PROTOCOL_VERSION
from .frame import HDR_BYTES, RAW16_128CH_FRAME, pack
from .wiring import WIRES, FakeWiring

U32 = 1 << 32

# Firmware constants the fake mirrors.
TICK_S = 0.001                   # CONFIG_FREERTOS_HZ=1000: the source's sleep step
POOL_BUF_BYTES = 8192            # CONFIG_BENCH_PIPE_BUF_BYTES default
UDP_DGRAM_MAX = 1472             # CONFIG_BENCH_UDP_DGRAM_MAX default: 5 x 270 = 1,350 B
LINKDROP_MIN_MS, LINKDROP_MAX_MS = 100, 60000
WIFI_REASON_ASSOC_LEAVE = 8      # what esp_wifi_stop() reports for the drop it causes

DEFAULT_CFG: dict = {"rate_bps": 0, "transport": "udp", "dst": "127.0.0.1:3333",
                     "payload": "rand", "dur_s": 10, "frame_bytes": 270,
                     "label": "unset", "nodelay": 1, "wr_max": 0, "dgram": 0, "batch": 0}
_V2_INT_KEYS = ("nodelay", "wr_max", "dgram", "batch")


def link_pins(chip: str) -> dict[int, list[str]]:
    """A board's Stage 2 link GPIOs and their roles, in wire order, as `pins list`
    reports them ("SDIO CMD / QSPI CS" -> ["qspi_cs", "sdio_cmd"]). Derived from
    wiring.WIRES so the fake cannot disagree with the canonical map."""
    out: dict[int, list[str]] = {}
    for w in WIRES:
        if w.chip == chip:
            out[w.gpio] = sorted(r.strip().lower().replace(" ", "_")
                                 for r in w.role.split("/"))
    return out


def whole_frames(cap: int, frame_bytes: int) -> int:
    """`cap` rounded down to whole frames, never below one frame (firmware rule)."""
    return max(1, cap // frame_bytes) * frame_bytes


class FakeESP:
    def __init__(self, chip: str = "esp32c5", *, control_port: int = CONTROL_PORT,
                 ceiling_mbps: float = 45.0, cpu_bound_mbps: float | None = None,
                 idf: str = "v6.0.2", fw_sha: str = "fake0000",
                 rung: str = "r0-baseline", band: str = "5", seed: int = 0,
                 ingress: str = "synth", wiring: FakeWiring | None = None):
        self.chip = chip
        self.control_port = control_port
        self.ceiling_bps = ceiling_mbps * 1e6
        # Load at which CPU idle reaches zero. None => never CPU-bound (RF-limited part).
        self.cpu_bound_bps = cpu_bound_mbps * 1e6 if cpu_bound_mbps else None
        self.idf = idf
        self.fw_sha = fw_sha
        self.rung = rung
        self.band = band
        self.ingress = ingress
        self.wiring = wiring
        self.rng = random.Random(seed)

        self.cfg: dict = dict(DEFAULT_CFG)
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
        self._boot()

    def _boot(self, reason: str = "POWERON") -> None:
        """Everything a reboot clears: the since-boot counters and the link state."""
        self._t_boot = time.monotonic()
        self.reset_reason = reason
        self.udp_enomem = self.send_errs = 0
        self.tcp_opens = self.tcp_open_fails = 0
        self.wifi_disc = self.wifi_last_reason = 0
        self.linkdrops = 0
        self._down_until = 0.0
        self._driven: dict[int, int] = {}
        if self.wiring is not None:
            self.wiring.esp_release_all(self.chip)

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

    # -- link state ----------------------------------------------------------
    def link_down(self) -> bool:
        """True while a `linkdrop` has the radio off."""
        return time.monotonic() < self._down_until

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
            if self.link_down():
                continue                  # the radio is off: the command never arrives
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
                    "idf": self.idf, "fw_sha": self.fw_sha, "ingress": self.ingress,
                    "rung": self.rung, "band": self.band, "simulated": True}
        if verb == "cfg":
            for tok in rest.split():
                k, _, v = tok.partition("=")
                if k not in self.cfg:
                    return {"ok": False, "err": f"unknown cfg key '{k}'"}
                val = _coerce(v)
                if k in _V2_INT_KEYS:
                    if not isinstance(val, int) or val < 0:
                        return {"ok": False, "err": f"cfg {k} must be a whole number"}
                    if k == "nodelay":
                        val = 1 if val else 0
                self.cfg[k] = val
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
            self.cfg = dict(DEFAULT_CFG)          # a reboot forgets every cfg key
            self._boot("SW")
            return {"ok": True}
        if verb == "linkdrop":
            try:
                ms = int(rest.split()[0])
            except (IndexError, ValueError):
                return {"ok": False, "err": "usage: linkdrop <ms>"}
            ms = min(max(ms, LINKDROP_MIN_MS), LINKDROP_MAX_MS)
            # The reply to this command still goes out: _serve only checks the link
            # before handling the NEXT one.
            self.linkdrops += 1
            self.wifi_disc += 1
            self.wifi_last_reason = WIFI_REASON_ASSOC_LEAVE
            self._down_until = time.monotonic() + ms / 1000.0
            return {"ok": True, "ms": ms}
        if verb == "pins":
            return self._pins(rest.split())
        return {"ok": False, "err": f"unknown command '{verb}'"}

    # -- pins (the wiring check) --------------------------------------------
    def _level(self, gpio: int) -> int:
        if self.wiring is not None:
            return self.wiring.esp_level(self.chip, gpio)
        return self._driven.get(gpio, 0)      # nothing attached: only our own drive

    def _levels(self) -> dict:
        return {"ok": True, "levels": {str(g): self._level(g) for g in link_pins(self.chip)}}

    def _release(self) -> None:
        self._driven.clear()
        if self.wiring is not None:
            self.wiring.esp_release_all(self.chip)

    def _pins(self, args: list[str]) -> dict:
        if self.ingress != "synth":
            return {"ok": False,
                    "err": f"pins are owned by the {self.ingress} link; flash a synth build"}
        pins = link_pins(self.chip)
        verb = args[0] if args else ""
        if verb == "list":
            return {"ok": True, "pins": {str(g): roles for g, roles in pins.items()}}
        if verb == "in":
            self._release()                   # every pin an input, pulled down
            return self._levels()
        if verb == "read":
            return self._levels()
        if verb == "drive":
            try:
                gpio, level = int(args[1]), int(args[2])
                if level not in (0, 1):
                    raise ValueError(level)
            except (IndexError, ValueError):
                return {"ok": False, "err": "usage: pins drive <gpio> <0|1>"}
            if gpio not in pins:
                return {"ok": False, "err": f"gpio {gpio} is not a link pin"}
            self._release()
            self._driven[gpio] = level
            if self.wiring is not None:
                self.wiring.esp_drive(self.chip, gpio, level)
            return self._levels()
        if verb == "release":
            self._release()
            return {"ok": True}
        return {"ok": False, "err": "usage: pins list|in|read|drive <gpio> <0|1>|release"}

    # -- stat ------------------------------------------------------------------
    def _stat(self) -> dict:
        offered = float(self.cfg["rate_bps"]) or 1.0
        if not self._running:
            # Faithful to the firmware: once the stream stops there is no achieved rate
            # to report (the source zeroes it at once) and the CPU is simply idle. A
            # harness that reads these from the `stop` summary gets nonsense.
            idle = 99.0
        elif self.cpu_bound_bps:
            idle = max(0.0, 100.0 * (1.0 - offered / self.cpu_bound_bps))
        else:
            idle = max(25.0, 100.0 - 45.0 * offered / max(self.ceiling_bps, 1.0))
        cores = [round(idle, 1)] if self.chip == "esp32c5" else [round(idle, 1), 85.0]
        return {"bytes": self.sent_bytes, "frames": self.sent_frames,
                "dropped": self.dropped, "idle_pct": cores,
                "retries": int(self.sent_frames * 0.01),
                "rssi": -42, "phy_rate": "HE20 MCS9" if self.band == "5" else "HT20 MCS7",
                "heap": 180000, "heap_min": 61000, "achieved_bps": self._achieved_bps(),
                "label": self.cfg["label"],
                # protocol v2: board health
                "heap_largest": 110592,
                "heap_win_min": 152000 if self._running else 176000,
                "psram_free": -1, "psram_min": -1,
                "uptime_s": int(time.monotonic() - self._t_boot),
                "reset_reason": self.reset_reason,
                "udp_enomem": self.udp_enomem, "send_errs": self.send_errs,
                "tcp_opens": self.tcp_opens, "tcp_open_fails": self.tcp_open_fails,
                "wifi_disc": self.wifi_disc, "wifi_last_reason": self.wifi_last_reason,
                "linkdrops": self.linkdrops,
                "stack_min": {"src": 2212, "sink": 1868, "ctl": 2604, "instr": 1540,
                              "tiT": 1764, "wifi": 3120},
                "nodelay": self.cfg["nodelay"], "wr_max": self.cfg["wr_max"],
                "dgram": self.cfg["dgram"], "batch": self.cfg["batch"]}

    def _achieved_bps(self) -> float:
        if not self._running:
            return 0.0
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
        if frame_bytes < HDR_BYTES + 2:              # the firmware's source does the same
            frame_bytes = RAW16_128CH_FRAME
        payload_bytes = frame_bytes - HDR_BYTES
        payload = os.urandom(payload_bytes)
        offered = float(self.cfg["rate_bps"])
        if offered <= 0:
            return

        tcp = self.cfg["transport"] == "tcp"
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
        try:
            if tcp:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY,
                             1 if self.cfg["nodelay"] else 0)
                s.connect((host, port))
                self.tcp_opens += 1
        except OSError:
            self.tcp_open_fails += 1
            s.close()
            with self._lock:
                self._running = False
            return

        # One batch is at most one pool buffer of whole frames; it is cut into TCP writes
        # of at most wr_max, or datagrams of at most dgram, both in whole frames.
        batch_cap = whole_frames(POOL_BUF_BYTES, frame_bytes)
        if tcp:
            wr_max = int(self.cfg["wr_max"])
            step = whole_frames(wr_max, frame_bytes) if wr_max else batch_cap
        else:
            step = whole_frames(int(self.cfg["dgram"]) or UDP_DGRAM_MAX, frame_bytes)
        pend = bytearray()

        def flush() -> bool:
            """Hand the pending frames to the socket. False = the socket failed."""
            nonlocal pend
            if not pend:
                return True
            data, pend = bytes(pend), bytearray()
            if self.link_down():
                if not tcp:
                    self.send_errs += 1      # no route: the sink's send fails outright
                return True
            try:
                for i in range(0, len(data), step):
                    if tcp:
                        s.sendall(data[i:i + step])
                    else:
                        s.sendto(data[i:i + step], (host, port))
            except OSError:
                self.send_errs += 1
                return False
            self.sent_frames += len(data) // frame_bytes
            self.sent_bytes += len(data)
            return True

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
        ok = True
        while ok:
            with self._lock:
                if not self._running:
                    break
            now = time.perf_counter()
            if now >= deadline:
                break
            if now < next_at:
                # Nothing due: what accumulated goes out now, and the loop sleeps at
                # least one 1 ms tick, as the firmware's source does (source_synth.c).
                # Above ~1 frame per ms that is what batches frames, as on the board.
                ok = flush()
                time.sleep(max(TICK_S, (next_at - now) * 0.9))
                continue
            n += 1
            next_at = t0 + n * interval
            self.seq += 1
            if tcp and self.link_down():
                # TCP blocks while the link is down: the pool fills and the source
                # starves, which the firmware counts as dropped.
                self.dropped += 1
                continue
            if self.rng.random() > deliver_frac:
                self.dropped += 1
                if not tcp:
                    self.udp_enomem += 1     # the sink waited on full TX buffers
                continue
            t_src = int(time.perf_counter() * 1e6) % U32
            pend += pack(self.seq, t_src, payload)
            if len(pend) + frame_bytes > batch_cap:
                ok = flush()
        if ok:
            flush()
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
