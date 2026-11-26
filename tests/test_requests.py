"""Requests keep the field layout found by live tests, whatever dwarf_python_api version is installed."""
from dwarf3.scope import Scope
from dwarf3.wire import parse


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, module, cmd, data=b""):
        self.sent.append((module, cmd, parse(data)))

    def call(self, module, cmd, data=b"", timeout=None):
        self.send(module, cmd, data)
        return [(1, 0), (2, 0), (3, 172.5)]

    def recv(self):
        return None


def sent_by(action):
    client = Recorder()
    try:
        action(Scope("x", client=client, log=lambda *a: None))
    except Exception:
        pass
    return client.sent


def test_goto_request():
    sent = sent_by(lambda s: s.goto("M33", 1.56, 30.6, timeout=0))
    assert sent == [(3, 11002, [(1, 1.56), (2, 30.6), (3, b"M33")])]


def test_capture_settings_requests():
    sent = sent_by(lambda s: s.set_capture("30", 80, "duo"))
    assert sent == [
        (15, 16700, [(1, 0x0201000000000001), (2, 1), (3, 159)]),
        (15, 16701, [(1, 0x0201000000000002), (2, 1), (3, 80)]),
        (15, 16703, [(1, 0x020100000000000D), (2, 2)]),
    ]


def test_motor_requests():
    sent = sent_by(lambda s: (s.home(), s.move(2, 90.0)))
    assert sent == [
        (6, 14003, [(1, 1)]),
        (6, 14003, [(1, 2), (2, 1)]),
        (6, 14001, [(1, 2), (2, 90.0), (3, 10.0), (4, 100), (5, 3)]),
    ]


def test_prepare_mode_requests():
    sent = sent_by(lambda s: s.prepare(40.0, -75.0, -5.0, "America/New_York"))
    assert sent[1:] == [
        (4, 13001, [(1, b"America/New_York")]),
        (4, 13010, [(1, 40.0), (2, -75.0)]),
        (3, 11010, []),
        (14, 16402, [(1, 2)]),
        (14, 16404, [(3, b"\x08\x01")]),
        (14, 16403, [(1, 2)]),
    ]
