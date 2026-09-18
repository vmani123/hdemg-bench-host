"""Client for the ESP control plane (spec §1.2).

Line-oriented ASCII commands over UDP, JSON replies. UDP because the serial port is
shared with idf.py monitor and stops working the moment a board is not on the desk.
"""
from __future__ import annotations
import json
import socket

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
