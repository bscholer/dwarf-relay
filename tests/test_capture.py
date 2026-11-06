from dwarf3 import scope as scope_module
from dwarf3.scope import Scope


class FakeClient:
    """Reports a start error, as firmware 1.5.4 does, while frames still arrive."""

    def __init__(self):
        self.sent = []

    def send(self, module, cmd, data=b""):
        self.sent.append(cmd)

    def recv(self):
        if self.sent == [11005]:
            self.sent.append("start reply")
            return 11005, 3, [(1, -11513)]
        return (15208, 2, [(1, 3)]) if 11006 in self.sent else None


class FakeAlbum:
    def __init__(self, host):
        self.polls = 0

    def names(self, path):
        if path == "/Astronomy":
            self.polls += 1
            return ["DWARF_DARK"] if self.polls == 1 else ["DWARF_DARK", "DWARF_RAW_TELE_2026-11-05-22-57-32-526"]
        return [f"frame_{i}.fits" for i in range(self.polls)]

    def close(self):
        pass


def test_capture_continues_after_start_error_and_stops_at_frame_count(monkeypatch):
    monkeypatch.setattr(scope_module, "Album", FakeAlbum)
    clock = iter(range(0, 100000, 3))
    monkeypatch.setattr(scope_module.time, "time", lambda: next(clock))

    scope = Scope.__new__(Scope)
    scope.host, scope.log, scope.client = "scope", lambda *_: None, FakeClient()

    assert scope.capture(2, "duo", 15.0) == "DWARF_RAW_TELE_2026-11-05-22-57-32-526"
    assert 11006 in scope.client.sent
