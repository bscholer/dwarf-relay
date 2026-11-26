"""Server-sent events: tell a listener the moment the status changes.

The stream only reads values the relay already holds, so it never opens a connection to the scope.
A listener gets the full status when it connects, then one event per change, and a comment line
every KEEPALIVE_S so it can tell a quiet scope from a dead relay.
"""
import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

TICK_S = 0.25
KEEPALIVE_S = 15.0


def event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def status_events(
    read: Callable[[], dict],
    disconnected: Callable[[], Awaitable[bool]],
    tick: float = TICK_S,
    keepalive: float = KEEPALIVE_S,
) -> AsyncIterator[str]:
    last: dict | None = None
    quiet = 0.0
    while not await disconnected():
        try:
            now = json.loads(json.dumps(read()))  # a copy, so a worker thread cannot change it mid-send
        except (TypeError, ValueError, RuntimeError):
            now = last
        if now is not None and now != last:
            last, quiet = now, 0.0
            yield event("status", now)
        elif quiet >= keepalive:
            quiet = 0.0
            yield ": keepalive\n\n"
        await asyncio.sleep(tick)
        quiet += tick
