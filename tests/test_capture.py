from dwarf_relay import scope as scope_module
from dwarf_relay.scope import Scope


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


def test_capture_reports_frame_count_while_running(monkeypatch):
    class Client(FakeClient):
        def recv(self):
            return None

    class Album(FakeAlbum):
        calls = 0

        def names(self, path):
            Album.calls += 1
            if path == scope_module.ALBUM_ROOT:
                return ["DWARF_RAW_TELE_x_2026-11-06-20-00-00-000"] if Album.calls > 1 else []
            return ["a.fits", "failed_b.fits", "stacked-1.fits", "shotsInfo.json"]

    monkeypatch.setattr(scope_module, "Album", Album)
    monkeypatch.setattr(scope_module.time, "time", iter(range(0, 10_000, 6)).__next__)
    seen = []
    scope = Scope("x", client=Client())
    scope.stop_capture = lambda: None
    scope.capture(2, "duo", 15, progress=lambda n, bad: seen.append((n, bad)))
    assert seen == [(2, 1)]


def _scope_with(client, monkeypatch, album):
    monkeypatch.setattr(scope_module, "Album", album)
    clock = iter(range(0, 100000, 3))
    monkeypatch.setattr(scope_module.time, "time", lambda: next(clock))
    monkeypatch.setattr(scope_module.time, "sleep", lambda _s: None)
    return Scope("scope", log=lambda *_: None, client=client)


def test_refused_start_with_no_session_fails_with_the_code_and_stops_the_scope(monkeypatch):
    import pytest

    class Client:
        sent = []

        def send(self, module, cmd, data=b""):
            self.sent.append(cmd)

        def recv(self):
            if self.sent == [11005]:
                self.sent.append("reply")
                return 11005, 3, [(1, -6)]
            return (15208, 2, [(1, 3)]) if 11006 in self.sent else None

    class EmptyAlbum(FakeAlbum):
        def names(self, path):
            return []

    scope = _scope_with(Client(), monkeypatch, EmptyAlbum)
    with pytest.raises(scope_module.ScopeError, match="refused, code -6"):
        scope.capture(4, "duo", 15.0)
    assert 11006 in scope.client.sent


def test_capture_reconnects_after_the_scope_drops_the_link(monkeypatch):
    from dwarf_relay.client import ConnectionLost

    class Dropping:
        sent = []

        def send(self, module, cmd, data=b""):
            self.sent.append(cmd)

        def recv(self):
            raise ConnectionLost("scope closed the connection")

    class Fresh(FakeClient):
        def recv(self):
            return (15208, 2, [(1, 3)]) if 11006 in self.sent else None

    fresh = Fresh()
    scope = _scope_with(Dropping(), monkeypatch, FakeAlbum)
    scope.reconnect = lambda: fresh
    assert scope.capture(2, "duo", 15.0) == "DWARF_RAW_TELE_2026-11-05-22-57-32-526"
    assert 11006 in fresh.sent


def test_capture_that_loses_the_link_for_good_still_tries_to_stop_the_scope(monkeypatch):
    import pytest

    from dwarf_relay.client import ConnectionLost

    class Dead:
        sent = []

        def send(self, module, cmd, data=b""):
            self.sent.append(cmd)

        def recv(self):
            raise ConnectionLost("scope closed the connection")

    scope = _scope_with(Dead(), monkeypatch, FakeAlbum)
    with pytest.raises(ConnectionLost):
        scope.capture(2, "duo", 15.0)
    assert scope.client.sent.count(11006) >= 1


def test_capture_returns_when_the_scope_ends_it_before_the_frame_count(monkeypatch):
    class Client(FakeClient):
        reads = 0

        def recv(self):
            self.reads += 1
            if self.sent == [11005]:
                self.sent.append("running")
                return 15208, 2, [(1, 1)]
            if self.sent == [11005, "running"]:
                self.sent.append("ended")
                return 15208, 2, [(1, 3)]
            return None

    class Album(FakeAlbum):
        def names(self, path):
            if path != scope_module.ALBUM_ROOT:
                return ["a.tif", "stacked-1.fits"]
            self.polls += 1
            return ["DWARF_RAW_TELE_x_2026-11-11-01-03-07-760"] if self.polls > 1 else []

    scope = _scope_with(Client(), monkeypatch, Album)
    scope.stop_capture = lambda: None
    progress = []
    scope.capture(200, "duo", 15.0, progress=lambda n, bad: progress.append(n))
    assert progress and progress[-1] == 1
    assert scope.client.reads < 100
