import asyncio
import datetime as dt
import json

import pytest

from dwarf3.mcp_server import Service, TOOLS, handle_rpc, require_safe_target, sun_position


def test_sun_position_at_june_solstice():
    ra, dec = sun_position(dt.datetime(2026, 6, 21, 12, tzinfo=dt.timezone.utc))
    assert ra == pytest.approx(6.0, abs=0.1)
    assert dec == pytest.approx(23.44, abs=0.1)


def test_goto_guard_handles_ra_wrap_near_the_sun():
    march = dt.datetime(2026, 3, 20, 15, tzinfo=dt.timezone.utc)  # Sun near RA 0.
    with pytest.raises(ValueError, match="from the Sun"):
        require_safe_target(23.9, 0.0, march)
    require_safe_target(12.0, 0.0, march)


def service():
    return Service(relay=None, scope_host="scope", loop=None)


def call(svc, name, arguments=None):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}
    return asyncio.run(handle_rpc(svc, request))["result"]


def test_every_listed_tool_has_a_service_method():
    for name in TOOLS:
        assert callable(getattr(Service, name))


def test_invalid_move_returns_a_tool_error_with_no_scope_access():
    result = call(service(), "move", {"angle": 0, "length": 0.5, "seconds": 60})
    assert result["isError"] is True


def test_goto_near_sun_fails_before_scope_access():
    ra, dec = sun_position(dt.datetime.now(dt.timezone.utc))
    result = call(service(), "goto", {"name": "Sun", "ra_hours": ra, "dec_degrees": dec})
    assert result["isError"] is True
    assert "from the Sun" in result["content"][0]["text"]


def test_motion_refused_while_capture_job_runs():
    svc = service()
    svc.job = {"state": "running"}
    result = call(svc, "close_barrel")
    assert result["isError"] is True
    assert "stop_job" in result["content"][0]["text"]


def test_notification_gets_no_response():
    assert asyncio.run(handle_rpc(service(), {"jsonrpc": "2.0", "method": "notifications/initialized"})) is None


def test_tools_list_is_valid_json_schema_shape():
    response = asyncio.run(handle_rpc(service(), {"id": 2, "method": "tools/list"}))
    assert json.dumps(response)
    assert all(t["inputSchema"]["type"] == "object" for t in response["result"]["tools"])
