"""Client for the ESP control plane (spec §1.2).

Line-oriented ASCII commands over UDP, JSON replies. UDP because the serial port is
shared with idf.py monitor and stops working the moment a board is not on the desk.
"""
from __future__ import annotations
import json
import select
import socket
import time

CONTROL_PORT = 3334
PROTOCOL_VERSION = 1


class ControlError(RuntimeError):
    pass


class ControlClient:
    def __init__(self, host: str, port: int = CONTROL_PORT, timeout: float = 2.0,
                 retries: int = 3):
        self.addr = (host, port)
        self.timeout = timeout
        self.retries = retries

    def _rpc(self, line: str) -> dict:
        last = None
        for _ in range(self.retries):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(self.timeout)
            try:
                s.sendto(line.encode(), self.addr)
                data, _ = s.recvfrom(65535)
                reply = json.loads(data.decode())
                if reply.get("ok") is False:
                    raise ControlError(reply.get("err", "device refused command"))
                return reply
            except socket.timeout as e:
                last = e
            finally:
                s.close()
        raise ControlError(f"no reply from {self.addr} after {self.retries} tries: {last}")

    def hello(self) -> dict:
        r = self._rpc("hello")
        if r.get("proto") != PROTOCOL_VERSION:
            raise ControlError(
                f"control protocol mismatch: host speaks v{PROTOCOL_VERSION}, "
                f"device speaks v{r.get('proto')}")
        return r

    def cfg(self, **kw) -> dict:
        parts = " ".join(f"{k}={v}" for k, v in sorted(kw.items()))
        return self._rpc(f"cfg {parts}")

    def start(self) -> dict:
        return self._rpc("start")

    def stop(self) -> dict:
        return self._rpc("stop")

    def stat(self) -> dict:
        return self._rpc("stat")

    def reset(self) -> dict:
        return self._rpc("reset")


def discover(addresses, port: int = CONTROL_PORT, timeout: float = 1.5,
             batch: int = 32, gap_s: float = 0.04) -> dict:
    """Find bench devices by asking every address for `hello`. Returns {chip: reply}
    with the replying address under reply["ip"].

    The access point hands out addresses by DHCP, so a device's IP is not known in
    advance and can change after a re-association.

    The sends are NON-BLOCKING and paced, and the whole call is bounded (roughly
    len(addresses)/batch * gap_s + timeout seconds). Both matter on a home /24: most of
    the 254 addresses do not exist, the kernel queues a packet behind each unanswered ARP
    lookup, and a BLOCKING send then waits behind that queue indefinitely — which hung
    the first real sweep on its first board. A send the kernel will not take right now
    is simply skipped; callers retry, and should put addresses known to exist first.
    """
    found: dict = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)

    def collect(wait_s: float) -> None:
        deadline = time.monotonic() + wait_s
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            readable, _, _ = select.select([s], [], [], left)
            if not readable:
                return
            try:
                data, (ip, _) = s.recvfrom(65535)
            except OSError:
                continue
            try:
                reply = json.loads(data.decode())
            except ValueError:
                continue
            chip = reply.get("chip") if isinstance(reply, dict) else None
            if chip and reply.get("ok", True):
                found[chip] = {**reply, "ip": ip}

    try:
        todo = [str(a) for a in addresses]
        for i in range(0, len(todo), max(1, batch)):
            for a in todo[i:i + max(1, batch)]:
                try:
                    s.sendto(b"hello", (a, port))
                except OSError:
                    continue            # no buffer / host down / would block: skip it
            if i + batch < len(todo):
                collect(gap_s)
        collect(timeout)
    finally:
        s.close()
    return found
