"""HTTP and MCP surface for scope control, declared once with pydantic models."""
import asyncio
import contextlib
from typing import Any

import uvicorn
from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from .endpoints import StatusHint, ToolAnnotations, http_endpoint, mcp_tool, mount_endpoints
from .mcp_server import Service, SunTooCloseError

_service: Service | None = None

CONTROL_ERRORS = {
    SunTooCloseError: StatusHint(422, "sun_too_close"),
    ValueError: StatusHint(422, "invalid_request"),
    RuntimeError: StatusHint(409, "busy"),
    Exception: StatusHint(502, "scope_error"),
}
TOOL_ERRORS = {Exception: str}


class Message(BaseModel):
    message: str


class CameraIn(BaseModel):
    wide: bool = Field(False, description="true for the wide camera")


class MoveIn(BaseModel):
    angle: float = Field(description="Joystick angle in degrees. 0 turns right.")
    length: float = Field(ge=0, le=1, description="Joystick deflection, 0 to 1.")
    seconds: float = Field(gt=0, le=5, description="Duration in seconds, 5 at most.")


class GotoIn(BaseModel):
    name: str
    ra_hours: float
    dec_degrees: float


class CaptureIn(BaseModel):
    frames: int = Field(ge=1, le=500)
    exposure: str = Field("15", description="Seconds per frame: 15, 30, or 60.")
    gain: int = 60
    filter: str = Field("duo", description="vis, astro, or duo.")


class DarkIn(BaseModel):
    frames: int = Field(ge=1, le=500)
    exposure: str = Field("15", description="Seconds per frame: 15, 30, or 60.")
    wide: bool = Field(False, description="true for the wide camera")


class Job(BaseModel):
    id: str | None = None
    state: str
    frames: int | None = None
    session: str | None = None
    error: str | None = None


class StatusOut(BaseModel):
    app_connected: bool
    job: Job | None
    rotation: float | None = None
    pitch: float | None = None
    tele_darks: int | None = None
    wide_darks: int | None = None


class SessionList(BaseModel):
    sessions: list[str]


class DarkList(BaseModel):
    darks: list[dict[str, Any]]


def svc() -> Service:
    if _service is None:
        raise RuntimeError("service is not started")
    return _service


def read_only(**extra: bool) -> ToolAnnotations:
    return ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False, **extra)


def acts(**extra: bool) -> ToolAnnotations:
    return ToolAnnotations(read_only_hint=False, open_world_hint=False, **extra)


@mcp_tool(name="status", title="Read scope status",
          description="Read motor positions, app connection, dark counts, and the job state.",
          annotations=read_only(), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/status", operation_id="getStatus", method="GET", response_model=StatusOut,
               description="Read motor positions, app connection, dark counts, and the job state.",
               tags=["Scope"], error_map=CONTROL_ERRORS)
def status() -> StatusOut:
    return StatusOut(**svc().status())


@mcp_tool(name="list_sessions", title="List capture sessions",
          description="List capture session folders on the scope.", annotations=read_only(),
          error_map=TOOL_ERRORS)
@http_endpoint(path="/api/sessions", operation_id="listSessions", method="GET", response_model=SessionList,
               description="List capture session folders on the scope.", tags=["Files"],
               error_map=CONTROL_ERRORS)
def list_sessions() -> SessionList:
    return SessionList(sessions=svc().list_sessions())


@mcp_tool(name="list_darks", title="List master darks",
          description="List master darks on the scope.", annotations=read_only(), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/darks", operation_id="listDarks", method="GET", payload_model=CameraIn,
               response_model=DarkList, description="List master darks on the scope.", tags=["Darks"],
               error_map=CONTROL_ERRORS)
def list_darks(p: CameraIn) -> DarkList:
    return DarkList(darks=svc().list_darks(wide=p.wide))


@mcp_tool(name="prepare", title="Prepare the scope",
          description="Set time and location, enter astro mode, home the motors, and point straight up.",
          annotations=acts(idempotent_hint=True), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/prepare", operation_id="prepareScope", method="POST", response_model=Message,
               description="Set time and location, enter astro mode, home the motors, and point straight up.",
               tags=["Scope"], error_map=CONTROL_ERRORS)
def prepare() -> Message:
    return Message(message=svc().prepare())


@mcp_tool(name="move", title="Move with the joystick",
          description="Move with the joystick. Angle 0 turns right. Seconds has a limit of 5.",
          annotations=acts(destructive_hint=False), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/move", operation_id="moveScope", method="POST", payload_model=MoveIn,
               response_model=Message, description="Move with the joystick for a short time.",
               tags=["Scope"], error_map=CONTROL_ERRORS)
def move(p: MoveIn) -> Message:
    return Message(message=svc().move(p.angle, p.length, p.seconds))


@mcp_tool(name="close_barrel", title="Close the barrel",
          description="Fold the scope to the closed position.", annotations=acts(idempotent_hint=True),
          error_map=TOOL_ERRORS)
@http_endpoint(path="/api/close_barrel", operation_id="closeBarrel", method="POST", response_model=Message,
               description="Fold the scope to the closed position.", tags=["Scope"],
               error_map=CONTROL_ERRORS)
def close_barrel() -> Message:
    return Message(message=svc().close_barrel())


@mcp_tool(name="calibrate", title="Focus and plate solve",
          description="Focus at infinity and plate solve. Needs a clear view of stars.",
          annotations=acts(idempotent_hint=True), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/calibrate", operation_id="calibrateScope", method="POST", response_model=Message,
               description="Focus at infinity and plate solve. Needs a clear view of stars.",
               tags=["Scope"], error_map=CONTROL_ERRORS)
def calibrate() -> Message:
    return Message(message=svc().calibrate())


@mcp_tool(name="goto", title="Slew to a target",
          description="Slew to a target and track it. Refuses a target near the Sun.",
          annotations=acts(idempotent_hint=True), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/goto", operation_id="gotoTarget", method="POST", payload_model=GotoIn,
               response_model=Message, description="Slew to a target and track it. Refuses a target near the Sun.",
               tags=["Scope"], error_map=CONTROL_ERRORS)
def goto(p: GotoIn) -> Message:
    return Message(message=svc().goto(p.name, p.ra_hours, p.dec_degrees))


@mcp_tool(name="start_capture", title="Start a capture job",
          description="Start a capture job that stops at the frame count. Exposure is 15, 30, or 60. "
                      "Filter is vis, astro, or duo.",
          annotations=acts(), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/capture", operation_id="startCapture", method="POST", payload_model=CaptureIn,
               response_model=Job, status_code=202,
               description="Start a capture job that stops at the frame count.", tags=["Capture"],
               error_map=CONTROL_ERRORS)
def start_capture(p: CaptureIn) -> Job:
    return Job(**svc().start_capture(p.frames, p.exposure, p.gain, p.filter))


@mcp_tool(name="stop_job", title="Stop the capture job",
          description="Stop the running capture job.", annotations=acts(idempotent_hint=True),
          error_map=TOOL_ERRORS)
@http_endpoint(path="/api/capture/stop", operation_id="stopCapture", method="POST", response_model=Message,
               description="Stop the running capture job.", tags=["Capture"], error_map=CONTROL_ERRORS)
def stop_job() -> Message:
    return Message(message=svc().stop_job())


@mcp_tool(name="start_dark", title="Start a dark capture",
          description="Start a dark capture at gain 60. Close the barrel first. The scope sends no end signal.",
          annotations=acts(), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/dark", operation_id="startDark", method="POST", payload_model=DarkIn,
               response_model=Message, status_code=202,
               description="Start a dark capture at gain 60. Close the barrel first.", tags=["Darks"],
               error_map=CONTROL_ERRORS)
def start_dark(p: DarkIn) -> Message:
    return Message(message=svc().start_dark(p.frames, p.exposure, p.wide))


app = FastAPI(title="dwarf3 relay", version="0.1.0")
mount_endpoints(app)


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    text = svc().relay.render_metrics()
    return Response(text, headers={"Content-Type": "text/plain; version=0.0.4"})


class _Server(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve(relay, scope_host: str, listen: str, port: int) -> _Server:
    global _service
    _service = Service(relay, scope_host, asyncio.get_running_loop())
    server = _Server(uvicorn.Config(app, host=listen, port=port, log_level="warning"))
    asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    return server
