"""The mid-run watchdog (19 Oct): a board that neither delivers nor answers is sent
through the recovery ladder and its stream started again; a board that answers but
sends nothing (a stalled stream, the thing being measured) is left alone."""
import time

from bench import orchestrator as orc


class _Rx:
    def __init__(self, silent_s):
        self.silent_s, self.events = silent_s, []

    def silence_s(self):
        return self.silent_s

    def note_event(self, kind, **kw):
        self.events.append((kind, kw))


class _Sampler:
    def __init__(self, unanswered_s):
        self.unanswered, self.client = unanswered_s, None

    def unanswered_s(self):
        return self.unanswered


class _Drv:
    def __init__(self):
        self.recovered = []

    def recover(self, chip):
        self.recovered.append(chip)
        return True


class _Client:
    def __init__(self):
        self.calls = []

    def cfg(self, **kw):
        self.calls.append(("cfg", kw))

    def start(self):
        self.calls.append(("start", {}))


def _dog(rx, sampler, drv, client):
    orc._Watchdog.POLL_S = 0.05
    return orc._Watchdog(rx, sampler, drv, "esp32s3", client, {"rate_bps": 1}, 600, 90,
                         lambda *a, **k: None).start()


def test_dead_board_is_recovered_and_restarted():
    rx, s, d, c = _Rx(120), _Sampler(120), _Drv(), _Client()
    dog = _dog(rx, s, d, c)
    time.sleep(0.3)
    rx.silent_s = s.unanswered = 0          # it is back
    time.sleep(0.2)
    dog.stop()
    assert d.recovered == ["esp32s3"]
    assert [k for k, _ in c.calls] == ["cfg", "start"]
    assert c.calls[0][1]["dur_s"] > 500
    assert \
        rx.events[-1][1]["action"] == "restarted"


def test_answering_but_silent_board_is_left_alone():
    rx, s, d, c = _Rx(300), _Sampler(0), _Drv(), _Client()
    dog = _dog(rx, s, d, c)
    time.sleep(0.3)
    dog.stop()
    assert d.recovered == [] and c.calls == []
