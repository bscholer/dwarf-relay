import pytest

from dwarf3.scope import NOTIFY_TRACKING, Scope, ScopeError


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def send(self, module, cmd, data=b""):
        pass

    def recv(self):
        return self.replies.pop(0) if self.replies else None


def test_goto_fails_at_once_when_the_scope_refuses():
    scope = Scope("x", client=FakeClient([(11002, 3, [(1, -11505)])]))
    with pytest.raises(ScopeError, match="-11505"):
        scope.goto("NGC 6543", 17.97, 66.6, timeout=5)


def test_goto_returns_when_tracking_starts():
    scope = Scope("x", client=FakeClient([(11002, 3, [(1, 0)]), (NOTIFY_TRACKING, 2, [(1, 1)])]))
    scope.goto("M33", 1.56, 30.6, timeout=5)


def test_prepare_skips_rotation_motor_when_the_firmware_locks_it():
    from dwarf3.scope import MOTOR_ROTATION

    class Client:
        def __init__(self):
            self.moved = []

        def send(self, module, cmd, data=b""):
            pass

        def call(self, module, cmd, data=b"", timeout=None):
            if cmd == 14003 and data.startswith(b"\x08\x01"):
                return [(2, -14511)]
            if cmd == 14001:
                self.moved.append(data[1])
            return [(1, 0), (2, 0)]

    client = Client()
    scope = Scope("x", client=client)
    scope.home()
    scope.point_up()
    assert MOTOR_ROTATION not in client.moved


def test_prepare_continues_when_the_firmware_refuses_the_shooting_mode():
    class Client:
        def __init__(self):
            self.cmds = []

        def send(self, module, cmd, data=b""):
            pass

        def call(self, module, cmd, data=b"", timeout=None):
            self.cmds.append(cmd)
            return [(1, -16602)] if cmd == 16402 else [(1, 0)]

    client = Client()
    Scope("x", log=lambda *_: None, client=client).prepare(37.0, -122.0, -8.0, "America/Los_Angeles")
    assert 16403 in client.cmds
