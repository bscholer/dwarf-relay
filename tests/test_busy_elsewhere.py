"""The relay reports a capture it did not start, so callers keep away from the motors."""

from types import SimpleNamespace

from dwarf3.mcp_server import Service
from dwarf3.proxy import Relay


def _relay():
    return Relay("127.0.0.1", log=SimpleNamespace(write=lambda **k: None))


def test_capture_notification_sets_and_clears_the_flag():
    relay = _relay()
    relay.note_scope_packet(15208, [[1, 1]])
    assert relay.capture_running
    relay.note_scope_packet(15212, [[1, 1]])
    assert relay.capture_running
    relay.note_scope_packet(15208, [[1, 3]])
    assert not relay.capture_running


def test_empty_capture_notification_counts_as_stopped():
    relay = _relay()
    relay.note_scope_packet(15208, [[1, 1]])
    relay.note_scope_packet(15208, [])
    assert not relay.capture_running


def _service(relay, job=None):
    svc = Service(relay, "127.0.0.1", loop=None)
    svc.job = job
    svc._with_scope = lambda fn: {}
    svc._positions_while_capturing = lambda: {}
    return svc


def test_status_flags_a_foreign_capture_but_not_our_own():
    relay = SimpleNamespace(app_link=None, capture_running=True)
    assert _service(relay).status()["busy_elsewhere"] is True
    own = {"state": "running"}
    assert _service(relay, own).status()["busy_elsewhere"] is False
    relay.capture_running = False
    assert _service(relay).status()["busy_elsewhere"] is False
