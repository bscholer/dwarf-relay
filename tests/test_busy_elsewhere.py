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
    return svc


def test_status_flags_a_foreign_capture_but_not_our_own():
    relay = SimpleNamespace(app_link=None, capture_running=True)
    assert _service(relay).status()["busy_elsewhere"] is True
    own = {"state": "running"}
    assert _service(relay, own).status()["busy_elsewhere"] is False
    relay.capture_running = False
    assert _service(relay).status()["busy_elsewhere"] is False


def test_point_refuses_while_another_capture_runs_and_checks_ranges():
    import pytest

    relay = SimpleNamespace(app_link=None, capture_running=True)
    svc = _service(relay)
    with pytest.raises(RuntimeError, match="busy"):
        svc.point(90.0, 100.0)
    relay.capture_running = False
    with pytest.raises(ValueError):
        svc.point(200.0, None)
    with pytest.raises(ValueError):
        svc.point(90.0, 400.0)
    assert svc.point(90.0, 100.0) == "pointed"


def test_a_call_waits_for_a_short_status_read_instead_of_failing():
    import threading
    import time

    service = Service(_relay(), "127.0.0.1", loop=None)
    service._scope = lambda: SimpleNamespace(close=lambda: None)
    service.lock.acquire()
    threading.Timer(0.2, service.lock.release).start()
    start = time.monotonic()
    assert service._with_scope(lambda _scope: "ok") == "ok"
    assert time.monotonic() - start >= 0.2


def test_capture_progress_marks_a_capture_the_relay_never_saw_start_and_it_expires():
    relay = _relay()
    now = {"t": 1000.0}
    relay.clock = lambda: now["t"]
    assert not relay.capture_running
    relay.note_scope_packet(15209, [[1, 999], [3, 14]])
    assert relay.capture_running
    now["t"] += 31
    assert not relay.capture_running
    relay.note_scope_packet(15288, [[1, 15.0]])
    relay.note_scope_packet(15208, [[1, 2]])
    assert not relay.capture_running


def test_status_during_our_capture_opens_no_second_connection():
    relay = SimpleNamespace(app_link=None, capture_running=True)
    svc = Service(relay, "127.0.0.1", loop=None)
    svc.positions = {"rotation": 158.0, "pitch": 150.0}
    svc.job = {"state": "running"}

    def forbidden(_fn):
        raise AssertionError("opened a second scope connection")

    svc._with_scope = forbidden
    status = svc.status()
    assert (status["rotation"], status["pitch"]) == (158.0, 150.0)
    assert status["busy_elsewhere"] is False


def test_stop_job_stops_a_capture_that_no_job_owns():
    relay = SimpleNamespace(app_link=None, capture_running=True)
    svc = Service(relay, "127.0.0.1", loop=None)
    stopped = []
    svc._with_scope = lambda fn: fn(SimpleNamespace(stop_capture=lambda: stopped.append(1)))
    assert "no job owned" in svc.stop_job()
    assert stopped and relay.capture_running is False
    assert svc.stop_job() == "nothing to stop"


def test_start_capture_waits_for_a_short_status_read_instead_of_failing():
    import threading

    service = Service(_relay(), "127.0.0.1", loop=None)
    service._capture = lambda job, *args: service.lock.release()
    service.lock.acquire()
    threading.Timer(0.2, service.lock.release).start()
    job = service.start_capture(2)
    assert job["state"] == "running"


def test_delete_session_is_refused_while_any_capture_runs_and_reports_a_missing_session():
    import pytest

    relay = SimpleNamespace(app_link=None, capture_running=True)
    svc = Service(relay, "127.0.0.1", loop=None)
    with pytest.raises(RuntimeError, match="capture is running"):
        svc.delete_session("DWARF_RAW_x")
    relay.capture_running = False
    svc.job = {"state": "running"}
    with pytest.raises(RuntimeError, match="capture is running"):
        svc.delete_session("DWARF_RAW_x")
