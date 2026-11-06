"""MCP server for scope control, over stateless JSON HTTP. It uses the relay connection path."""
import asyncio
import datetime as dt
import json
import math
import os
import threading
import uuid

from .client import Client
from .files import Album
from .scope import ALBUM_ROOT, EXPOSURE_INDEX, FILTERS, MOTOR_PITCH, MOTOR_ROTATION, Scope

PROTOCOL_VERSION = "2025-06-18"
MIN_SUN_SEPARATION_DEG = 30.0
MAX_MOVE_SECONDS = 5.0
MAX_FRAMES = 500
LOCAL_RELAY = "127.0.0.1"


def sun_position(when: dt.datetime) -> tuple[float, float]:
    """Return the Sun RA in hours and Dec in degrees, within about one degree."""
    days = (when - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)).total_seconds() / 86400
    mean_long = math.radians((280.46 + 0.9856474 * days) % 360)
    anomaly = math.radians((357.528 + 0.9856003 * days) % 360)
    ecliptic = mean_long + math.radians(1.915) * math.sin(anomaly) + math.radians(0.02) * math.sin(2 * anomaly)
    tilt = math.radians(23.439)
    ra = math.atan2(math.cos(tilt) * math.sin(ecliptic), math.cos(ecliptic))
    return (math.degrees(ra) / 15) % 24, math.degrees(math.asin(math.sin(tilt) * math.sin(ecliptic)))


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    d1, d2, dra = math.radians(dec1), math.radians(dec2), math.radians((ra1 - ra2) * 15)
    cosine = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def require_safe_target(ra: float, dec: float, when: dt.datetime | None = None) -> None:
    sun_ra, sun_dec = sun_position(when or dt.datetime.now(dt.timezone.utc))
    distance = separation_deg(ra, dec, sun_ra, sun_dec)
    if distance < MIN_SUN_SEPARATION_DEG:
        raise ValueError(f"target is {distance:.0f} degrees from the Sun, the limit is {MIN_SUN_SEPARATION_DEG:.0f}")


def schema(properties: dict | None = None, required: list | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or []}


NUMBER, STRING, INTEGER = {"type": "number"}, {"type": "string"}, {"type": "integer"}
CAMERA = {"type": "boolean", "description": "true for the wide camera"}
TOOLS = {
    "status": ("Read motor positions, app connection, dark counts, and the job state.", schema()),
    "list_sessions": ("List capture session folders on the scope.", schema()),
    "list_darks": ("List master darks on the scope.", schema({"wide": CAMERA})),
    "prepare": ("Set time and location, enter astro mode, home the motors, and point straight up.", schema()),
    "move": ("Move with the joystick. Angle 0 turns right. Seconds has a limit of 5.",
             schema({"angle": NUMBER, "length": NUMBER, "seconds": NUMBER}, ["angle", "length", "seconds"])),
    "close_barrel": ("Fold the scope to the closed position.", schema()),
    "calibrate": ("Focus at infinity and plate solve. Needs a clear view of stars.", schema()),
    "goto": ("Slew to a target and track it. Refuses a target near the Sun.",
             schema({"name": STRING, "ra_hours": NUMBER, "dec_degrees": NUMBER}, ["name", "ra_hours", "dec_degrees"])),
    "start_capture": ("Start a capture job that stops at the frame count. Exposure is 15, 30, or 60. Filter is vis, astro, or duo.",
                      schema({"frames": INTEGER, "exposure": STRING, "gain": INTEGER, "filter": STRING}, ["frames"])),
    "stop_job": ("Stop the running capture job.", schema()),
    "start_dark": ("Start a dark capture at gain 60. Close the barrel first. The scope sends no end signal.",
                   schema({"exposure": STRING, "frames": INTEGER, "wide": CAMERA}, ["frames"])),
}


class Service:
    """Run tool calls one at a time, because the scope has one control connection."""

    def __init__(self, relay, scope_host: str, loop):
        self.relay, self.scope_host, self.loop = relay, scope_host, loop
        self.lock = threading.Lock()
        self.job: dict | None = None
        self.cancel = threading.Event()

    def _scope(self) -> Scope:
        from .proxy import RelayClient

        link = self.relay.app_link
        client = RelayClient(self.relay, self.loop) if link and link.header else Client(LOCAL_RELAY)
        return Scope(self.scope_host, log=lambda *_: None, client=client)

    def _with_scope(self, fn):
        if self.job and self.job["state"] == "running":
            raise RuntimeError("a capture job is running, call stop_job first")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("another tool call is running")
        scope = None
        try:
            scope = self._scope()
            return fn(scope)
        finally:
            if scope:
                scope.close()
            self.lock.release()

    def status(self) -> dict:
        def read(s):
            return {"rotation": s.position(MOTOR_ROTATION), "pitch": s.position(MOTOR_PITCH),
                    "tele_darks": len(s.dark_list()), "wide_darks": len(s.dark_list(wide=True))}

        state = {"app_connected": self.relay.app_link is not None, "job": self.job}
        if self.job and self.job["state"] == "running":
            return state
        return {**state, **self._with_scope(read)}

    def list_sessions(self) -> list[str]:
        album = Album(self.scope_host)
        try:
            return sorted(n for n in album.names(ALBUM_ROOT) if n.startswith("DWARF_RAW_"))
        finally:
            album.close()

    def list_darks(self, wide: bool = False) -> list[dict]:
        return self._with_scope(lambda s: s.dark_list(wide=wide))

    def prepare(self) -> str:
        env = os.environ

        def run(s):
            s.prepare(float(env["DWARF3_LAT"]), float(env["DWARF3_LON"]), float(env["DWARF3_UTC_OFFSET"]), env["DWARF3_TZ"])
            s.home()
            s.point_up()

        self._with_scope(run)
        return "ready, pointing up"

    def move(self, angle: float, length: float, seconds: float) -> str:
        if not 0 <= length <= 1 or not 0 < seconds <= MAX_MOVE_SECONDS:
            raise ValueError(f"length must be 0 to 1, and seconds must be 0 to {MAX_MOVE_SECONDS:.0f}")
        self._with_scope(lambda s: s.joystick(angle, length, seconds))
        return "moved"

    def close_barrel(self) -> str:
        self._with_scope(lambda s: s.close_barrel())
        return "closed"

    def calibrate(self) -> str:
        def run(s):
            s.focus_infinity()
            s.calibrate()

        self._with_scope(run)
        return "calibrated"

    def goto(self, name: str, ra_hours: float, dec_degrees: float) -> str:
        require_safe_target(ra_hours, dec_degrees)
        self._with_scope(lambda s: s.goto(name, ra_hours, dec_degrees))
        return f"tracking {name}"

    def start_capture(self, frames: int, exposure: str = "15", gain: int = 60, filter: str = "duo") -> dict:
        if not 0 < frames <= MAX_FRAMES or exposure not in EXPOSURE_INDEX or filter not in FILTERS:
            raise ValueError(f"frames must be 1 to {MAX_FRAMES}, exposure one of {sorted(EXPOSURE_INDEX)}, filter one of {sorted(FILTERS)}")
        if self.job and self.job["state"] == "running":
            raise RuntimeError("a capture job is already running")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("another tool call is running")
        self.cancel.clear()
        self.job = {"id": uuid.uuid4().hex[:8], "state": "running", "frames": frames, "session": None, "error": None}
        threading.Thread(target=self._capture, args=(self.job, frames, exposure, gain, filter), daemon=True).start()
        return self.job

    def _capture(self, job: dict, frames: int, exposure: str, gain: int, filter_name: str) -> None:
        scope = None
        try:
            scope = self._scope()
            scope.set_capture(exposure, gain, filter_name)
            job["session"] = scope.capture(frames, filter_name, float(exposure), self.cancel)
            job["state"] = "done"
        except Exception as exc:
            job.update(state="failed", error=str(exc))
        finally:
            if scope:
                scope.close()
            self.lock.release()

    def stop_job(self) -> str:
        self.cancel.set()
        return "stop requested"

    def start_dark(self, frames: int, exposure: str = "15", wide: bool = False) -> str:
        self._with_scope(lambda s: s.start_dark(exposure, "60", frames, wide=wide))
        return "dark capture sent"


def rpc_result(request_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


async def handle_rpc(service: Service, request: dict) -> dict | None:
    method, request_id = request.get("method"), request.get("id")
    if method == "initialize":
        return rpc_result(request_id, {
            "protocolVersion": request.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "dwarf3", "version": "0.1.0"},
        })
    if method == "ping":
        return rpc_result(request_id, {})
    if method == "tools/list":
        tools = [{"name": n, "description": d, "inputSchema": s} for n, (d, s) in TOOLS.items()]
        return rpc_result(request_id, {"tools": tools})
    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            result = await asyncio.to_thread(getattr(service, name), **params.get("arguments", {}))
            return rpc_result(request_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
        except Exception as exc:
            return rpc_result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}}


async def handle_http(service: Service, reader, writer) -> None:
    try:
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1").split("\r\n")
        method, path, *_ = head[0].split(" ")
        headers = {k.strip().lower(): v.strip() for k, v in (h.split(":", 1) for h in head[1:] if ":" in h)}
        body = await reader.readexactly(int(headers.get("content-length", 0)))
        status, payload, content_type = "404 Not Found", b"", "application/json"
        if path == "/mcp" and method == "POST":
            response = await handle_rpc(service, json.loads(body))
            status, payload = ("202 Accepted", b"") if response is None else ("200 OK", json.dumps(response).encode())
        elif path == "/metrics" and method == "GET":
            status, payload, content_type = "200 OK", service.relay.render_metrics().encode(), "text/plain; version=0.0.4"
        elif path == "/mcp":
            status = "405 Method Not Allowed"
        writer.write(f"HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, ValueError):
        pass
    finally:
        writer.close()


async def serve(relay, scope_host: str, listen: str, port: int):
    service = Service(relay, scope_host, asyncio.get_running_loop())
    return await asyncio.start_server(lambda r, w: handle_http(service, r, w), listen, port)
