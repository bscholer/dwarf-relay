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
