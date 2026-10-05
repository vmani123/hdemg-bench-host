"""Discovery must be bounded. The first real sweep hung on its first board: discovery
sent one BLOCKING datagram to each of a home /24's 254 addresses, the kernel queued them
behind ~240 unanswered ARP lookups, and the send never returned."""
import sys, pathlib, socket, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bench import control, drivers, rfmeta
from bench.control import discover
from bench.drivers import HardwareDriver
from bench.fakeesp import FakeESP


def test_a_chatty_peer_cannot_keep_discovery_collecting_forever():
    """The old loop ended only after 1.5 s of silence, so anything that kept answering
    kept it alive. The call is now bounded by its own deadline."""
    port = 17110
    stop = threading.Event()
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", port))
    srv.settimeout(0.2)

    def chatter():
        peer = None
        while not stop.is_set():
            try:
                _, peer = srv.recvfrom(100)
            except socket.timeout:
                pass
            if peer:
                for _ in range(20):
                    srv.sendto(b"not json at all", peer)
                time.sleep(0.05)
    t = threading.Thread(target=chatter, daemon=True)
    t.start()
    try:
        t0 = time.monotonic()
        found = discover(["127.0.0.1"], port=port, timeout=0.5)
        took = time.monotonic() - t0
    finally:
        stop.set()
        t.join(timeout=2)
        srv.close()
    assert found == {}
    assert took < 2.0, f"discovery ran for {took:.1f} s"


def test_sends_are_non_blocking_and_a_refused_send_is_skipped(monkeypatch):
    seen = {}
    real_socket = socket.socket

    class Sock:
        def __init__(self, *a, **k):
            self._s = real_socket(*a, **k)
            self.sent = 0

        def setblocking(self, flag):
            seen["blocking"] = flag
            self._s.setblocking(flag)

        def sendto(self, data, addr):
            self.sent += 1
            if self.sent % 2 == 0:
                raise OSError(55, "No buffer space available")
            return self._s.sendto(data, addr)

        def __getattr__(self, name):
            return getattr(self._s, name)

    monkeypatch.setattr(control.socket, "socket", Sock)
    esp = FakeESP("esp32s3", control_port=17120, band="2.4")
    esp.start()
    try:
        # The device's address is tried first, so the refused second send costs nothing.
        found = discover(["127.0.0.1", "127.0.0.1"], port=17120, timeout=0.5)
    finally:
        esp.shutdown()
    assert seen["blocking"] is False
    assert found["esp32s3"]["ip"] == "127.0.0.1"


def test_a_whole_slash_24_of_absent_hosts_returns_promptly():
    t0 = time.monotonic()
    found = discover([f"127.0.{i}.1" for i in range(1, 255)], port=17130, timeout=0.3)
    assert found == {}
    assert time.monotonic() - t0 < 3.0


def test_known_neighbours_are_asked_first_and_the_blind_scan_is_skipped(monkeypatch):
    """Once the wanted board answers from an address already known to exist, the rest of
    the /24 — hundreds of ARP broadcasts on a home network — is not probed at all."""
    calls = []

    def fake_discover(addresses, **kw):
        addresses = list(addresses)
        calls.append(addresses)
        if "192.168.1.165" in addresses:
            return {"esp32c5": {"chip": "esp32c5", "ip": "192.168.1.165", "band": "2.4"}}
        return {}
    monkeypatch.setattr(drivers, "discover", fake_discover)
    monkeypatch.setattr(rfmeta, "lan_neighbours", lambda prefix: ["192.168.1.1", "192.168.1.165"])
    drv = HardwareDriver(ips={"esp32s3": None, "esp32c5": None}, firmware_root=".",
                         ports={}, host_ip="192.168.1.160", ap="verizon-router")
    found = drv.rediscover("esp32c5")
    assert found["esp32c5"]["ip"] == "192.168.1.165"
    assert drv.ips["esp32c5"] == "192.168.1.165"
    assert calls == [["192.168.1.1", "192.168.1.165"]]

    # A board that is NOT among the known neighbours does trigger the blind scan, once;
    # an immediate retry is rationed rather than flooding the LAN again.
    calls.clear()
    drv.rediscover("esp32s3")
    assert len(calls) == 2 and len(calls[1]) == 252
    assert "192.168.1.165" not in calls[1]
    calls.clear()
    drv.rediscover("esp32s3")
    assert len(calls) == 1


def test_neighbour_table_parsing_skips_incomplete_entries(monkeypatch):
    table = ("? (192.168.1.1) at 0:11:22:33:44:55 on en0 ifscope [ethernet]\n"
             "? (192.168.1.165) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
             "? (192.168.1.77) at (incomplete) on en0 ifscope [ethernet]\n"
             "? (192.168.1.255) at ff:ff:ff:ff:ff:ff on en0 ifscope [ethernet]\n"
             "? (10.0.0.9) at 1:2:3:4:5:6 on en8 ifscope [ethernet]\n")
    monkeypatch.setattr(rfmeta.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(rfmeta, "_run", lambda cmd, timeout=15.0: table)
    assert rfmeta.lan_neighbours("192.168.1") == ["192.168.1.1", "192.168.1.165"]
