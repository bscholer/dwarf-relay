import datetime as dt

import pytest
from fastapi.testclient import TestClient

from dwarf3 import api
from dwarf3.endpoints import tool_manifest
from dwarf3.mcp_server import Service, require_safe_target, sun_position
from scripts.export_spec import DOCS, generated


def test_sun_position_at_june_solstice():
    ra, dec = sun_position(dt.datetime(2026, 6, 21, 12, tzinfo=dt.timezone.utc))
    assert ra == pytest.approx(6.0, abs=0.1)
    assert dec == pytest.approx(23.44, abs=0.1)


def test_goto_guard_handles_ra_wrap_near_the_sun():
    march = dt.datetime(2026, 3, 20, 15, tzinfo=dt.timezone.utc)  # Sun near RA 0.
    with pytest.raises(ValueError, match="from the Sun"):
        require_safe_target(23.9, 0.0, march)
    require_safe_target(12.0, 0.0, march)


class FakeRelay:
    app_link = None

    def render_metrics(self):
        return "dwarf3_scope_up 1\n"


@pytest.fixture
def svc(monkeypatch):
    service = Service(relay=FakeRelay(), scope_host="scope", loop=None)
    monkeypatch.setattr(api, "_service", service)
    return service


@pytest.fixture
def client(svc):
    return TestClient(api.app)


def call(client, name, arguments=None):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}
    return client.post("/mcp", json=request).json()["result"]


def test_every_tool_has_a_service_method():
    for tool in tool_manifest():
        assert callable(getattr(Service, tool["name"]))


def test_invalid_move_is_refused_before_scope_access(client):
    assert call(client, "move", {"angle": 0, "length": 0.5, "seconds": 60})["isError"] is True
    r = client.post("/api/move", json={"angle": 0, "length": 0.5, "seconds": 60})
    assert r.status_code == 422


def test_invalid_body_uses_the_error_shape(client):
    r = client.post("/api/move", json={"angle": 0, "length": 0.5, "seconds": 60})
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_request"
    assert "seconds" in r.json()["detail"]


def test_goto_near_sun_returns_sun_too_close_before_scope_access(client):
    ra, dec = sun_position(dt.datetime.now(dt.timezone.utc))
    body = {"name": "Sun", "ra_hours": ra, "dec_degrees": dec}
    r = client.post("/api/goto", json=body)
    assert (r.status_code, r.json()["code"]) == (422, "sun_too_close")
    result = call(client, "goto", body)
    assert result["isError"] is True
    assert "from the Sun" in result["content"][0]["text"]


def test_motion_refused_while_capture_job_runs(client, svc):
    svc.job = {"state": "running"}
    r = client.post("/api/close_barrel")
    assert (r.status_code, r.json()["code"]) == (409, "busy")
    assert "stop_job" in call(client, "close_barrel")["content"][0]["text"]


def test_status_while_job_runs_skips_scope_access(client, svc):
    svc.job = {"state": "running", "id": "ab", "frames": 5}
    body = client.get("/api/status").json()
    assert body["job"]["state"] == "running" and body["rotation"] is None


def test_metrics_endpoint_uses_the_prometheus_content_type(client):
    r = client.get("/metrics")
    assert r.headers["content-type"] == "text/plain; version=0.0.4"
    assert r.text == "dwarf3_scope_up 1\n"


def test_committed_spec_matches_code():
    for name, text in generated().items():
        assert (DOCS / name).read_text() == text, f"{name} is stale: python -m scripts.export_spec"
