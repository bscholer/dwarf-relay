"""The event stream: one event per change, a keepalive when quiet, and no scope traffic."""

import asyncio
import json
from types import SimpleNamespace

from dwarf3 import proxy
from dwarf3.events import status_events
from dwarf3.mcp_server import Service
from dwarf3.proxy import Relay


def _collect(read, steps, tick=0.0, keepalive=1.0):
    """Run the stream until `steps` pieces came out, or the reader says stop."""
    out: list[str] = []

    async def go():
        stop = False

        async def disconnected():
            return stop

        async for piece in status_events(read, disconnected, tick=tick, keepalive=keepalive):
            out.append(piece)
            if len(out) >= steps:
                stop = True

    asyncio.run(go())
    return out


def _data(piece):
    return json.loads(piece.split("data: ", 1)[1])


def test_sends_the_status_at_once_then_only_changes():
    values = iter([{"battery_percent": 80}, {"battery_percent": 80}, {"battery_percent": 79}])
    state = {"now": {"battery_percent": 80}}

    def read():
        try:
            state["now"] = next(values)
        except StopIteration:
            pass
        return state["now"]

    pieces = _collect(read, steps=2)
    assert [_data(p)["battery_percent"] for p in pieces] == [80, 79]  # the repeat of 80 sent nothing
    assert pieces[0].startswith("event: status\n")


def test_a_quiet_scope_still_gets_a_keepalive():
    pieces = _collect(lambda: {"a": 1}, steps=2, tick=0.01, keepalive=0.03)
    assert pieces[0].startswith("event: status")
    assert pieces[1] == ": keepalive\n\n"


def test_a_failed_read_does_not_end_the_stream():
    calls = {"n": 0}

    def read():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("dictionary changed size during iteration")
        return {"n": calls["n"] // 3}

    pieces = _collect(read, steps=2, tick=0.0)
    assert len(pieces) == 2


def _relay():
    return Relay("127.0.0.1", log=SimpleNamespace(write=lambda **k: None))


def test_live_status_comes_from_memory_and_shows_what_the_scope_pushed():
    relay = _relay()
    relay.metrics.observe("scope", 15201, 2, [[1, 64]])
    relay.metrics.observe("scope", 15208, 2, [[1, 1]])
    relay.metrics.set("scope_up", 1)
    svc = Service(relay, "192.0.2.10", loop=None)

    def refuse(fn):
        raise AssertionError("must not open a connection to the scope")

    svc._with_scope = refuse
    live = svc.live_status()
    assert live["battery_percent"] == 64
    assert live["capture_state"] == 1
    assert live["scope_reachable"] is True
    assert live["app_connected"] is False
    json.dumps(live)  # sendable


def test_probe_logs_only_when_the_answer_changes(monkeypatch):
    events, answers = [], iter([True, True, False, False, True])
    relay = SimpleNamespace(
        metrics=proxy.Metrics(), log=SimpleNamespace(write=lambda **k: events.append(k["event"]))
    )

    class Done(Exception):
        pass

    async def fake_open(host, port):
        try:
            ok = next(answers)
        except StopIteration:
            raise Done from None
        if not ok:
            raise OSError("down")
        return None, SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(proxy.asyncio, "open_connection", fake_open)
    try:
        asyncio.run(proxy.probe_scope(relay, "192.0.2.10", down_interval=0, up_interval=0))
    except Done:
        pass
    assert events == ["scope_up", "scope_down", "scope_up"]
    assert relay.metrics.gauges[("scope_up", ())] == 1
