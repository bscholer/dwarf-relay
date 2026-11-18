import datetime as dt

import pytest
from fastapi.testclient import TestClient

from dwarf3 import api
from dwarf3.endpoints import ensure_token, tool_manifest
from dwarf3.mcp_server import Service, require_safe_target, sun_position
from scripts.conformance import check
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
    capture_running = False

    def render_metrics(self):
        return "dwarf3_scope_up 1\n"


@pytest.fixture
def svc(monkeypatch):
    service = Service(relay=FakeRelay(), scope_host="scope", loop=None)
    monkeypatch.setattr(api, "_service", service)
    return service


@pytest.fixture
def client(svc, monkeypatch):
    monkeypatch.delenv("DWARF3_AUTH", raising=False)
    monkeypatch.setenv("DWARF3_API_TOKEN", "test-token")
    return TestClient(api.app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture
def anon(client):
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


def test_service_conforms_to_the_spec(client):
    assert check(lambda path: client.get(path).json()) == []


def test_conformance_reports_a_missing_operation(client):
    def get_json(path):
        data = client.get(path).json()
        if path == "/openapi.json":
            del data["paths"]["/api/goto"]
        return data

    assert check(get_json) == ["missing operation gotoTarget"]


def test_control_endpoints_need_the_token_by_default(anon):
    for path, body in (("/api/close_barrel", None), ("/api/goto", {"name": "x", "ra_hours": 1, "dec_degrees": 1})):
        r = anon.post(path, json=body)
        assert (r.status_code, r.json()["code"]) == (401, "unauthorized")
    r = anon.post("/api/close_barrel", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "close_barrel", "arguments": {}}}
    assert anon.post("/mcp", json=request).json()["result"]["isError"] is True


def test_status_and_capabilities_stay_open(anon, svc, monkeypatch):
    monkeypatch.setattr(Service, "_with_scope", lambda self, read: {"rotation": 1.0, "pitch": 2.0,
                                                                    "tele_darks": 0, "wide_darks": 0})
    monkeypatch.setattr(Service, "_sd_free_gb", lambda self: None)
    assert anon.get("/api/capabilities").status_code == 200
    assert anon.get("/api/status").status_code == 200


def test_auth_off_lets_control_through(anon, monkeypatch):
    monkeypatch.setenv("DWARF3_AUTH", "off")
    r = anon.post("/api/move", json={"angle": 0, "length": 0.5, "seconds": 60})
    assert r.status_code == 422, "past auth, stopped by validation"


def test_status_reports_battery_and_temperatures(client, svc, monkeypatch):
    from dwarf3.metrics import Metrics

    m = Metrics()
    m.observe("scope", 15201, 2, [(1, 87)])
    m.observe("scope", 15243, 2, [(2, 31)])
    m.observe("scope", 15292, 2, [(1, 41)])
    m.observe("scope", 15292, 2, [(1, 39), (2, 1)])
    m.observe("scope", 15203, 2, [(1, 31), (2, 64)])
    m.observe("scope", 15212, 2, [(1, 1)])
    m.observe("scope", 15208, 2, [])
    m.set("scope_up", 1)
    svc.relay.metrics = m
    monkeypatch.setattr(Service, "_with_scope", lambda self, read: {"rotation": 1.0, "pitch": 2.0,
                                                                    "tele_darks": 0, "wide_darks": 0})
    monkeypatch.setattr(Service, "_sd_free_gb", lambda self: None)
    body = client.get("/api/status").json()
    assert body["battery_percent"] == 87
    assert body["device_temperature_c"] == 31
    assert (body["tele_sensor_temperature_c"], body["wide_sensor_temperature_c"]) == (41, 39)
    assert body["scope_reachable"] is True
    assert body["sd_total_gb"] == 64
    assert (body["tracking_state"], body["capture_state"]) == (1, 0)
    assert body["goto_state"] is None, "no notification yet means unknown, not zero"


def test_ensure_token_makes_one_and_keeps_it(tmp_path, monkeypatch):
    monkeypatch.delenv("DWARF3_API_TOKEN", raising=False)
    monkeypatch.delenv("DWARF3_AUTH", raising=False)
    path = ensure_token(tmp_path)
    first = path.read_text()
    assert len(first) >= 32 and (path.stat().st_mode & 0o777) == 0o600
    monkeypatch.delenv("DWARF3_API_TOKEN")
    ensure_token(tmp_path)
    assert path.read_text() == first
    monkeypatch.setenv("DWARF3_API_TOKEN", "from-env")
    assert ensure_token(tmp_path / "other") is None


def test_capabilities_report_the_service_version(anon):
    from dwarf3 import __version__

    assert anon.get("/api/capabilities").json()["service"]["version"] == __version__
