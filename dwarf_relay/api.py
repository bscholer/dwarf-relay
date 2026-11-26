"""HTTP and MCP surface for scope control, declared once with pydantic models."""
import asyncio
import contextlib
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .events import status_events
from .endpoints import AuthMode, StatusHint, ToolAnnotations, http_endpoint, mcp_tool, mount_endpoints
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


class ServiceInfo(BaseModel):
    name: str
    vendor: str
    model: str
    version: str | None = Field(None, description="Version of this service.")


class CaptureOptions(BaseModel):
    filters: list[str] = Field(description="Filter names startCapture accepts on this scope.")
    exposures: list[str] = Field(description="Exposures in seconds that startCapture and startDark accept.")
    wide_darks: bool = Field(description="Whether startDark takes wide=true on this scope.")


class CapabilitiesOut(BaseModel):
    spec_version: str
    service: ServiceInfo
    capabilities: list[str]
    capture: CaptureOptions | None = Field(None, description="Choices that depend on the scope model. Since spec 0.2.")


class CameraIn(BaseModel):
    wide: bool = Field(False, description="true for the wide camera")


class FrameIn(BaseModel):
    camera: str = Field("wide", description="tele or wide")


class Frame(BaseModel):
    camera: str
    data: str = Field(description="Base64 JPEG of one live frame, 1280 pixels wide.")


class MoveIn(BaseModel):
    angle: float = Field(description="Joystick angle in degrees. 0 turns right.")
    length: float = Field(ge=0, le=1, description="Joystick deflection, 0 to 1.")
    seconds: float = Field(gt=0, le=5, description="Duration in seconds, 5 at most.")


class PointIn(BaseModel):
    pitch: float = Field(description="Absolute pitch position, 0 to 175. On a DWARF 3, 172.5 is straight up.")
    rotation: float | None = Field(None, description="Absolute rotation position, 0 to 360.")


class GotoIn(BaseModel):
    name: str
    ra_hours: float
    dec_degrees: float


class CaptureIn(BaseModel):
    frames: int = Field(ge=1, le=500)
    exposure: str = Field("15", description="Seconds per frame, one of capabilities capture.exposures (15, 30 or 60 on a DWARF 3).")
    gain: int = 60
    filter: str = Field("duo", description="One of capabilities capture.filters (vis, astro or duo on a DWARF 3).")


class DarkIn(BaseModel):
    frames: int = Field(ge=1, le=500)
    exposure: str = Field("15", description="Seconds per frame, one of capabilities capture.exposures (15, 30 or 60 on a DWARF 3).")
    wide: bool = Field(False, description="true for the wide camera")


class SessionIn(BaseModel):
    session: str = Field(description="Session folder name, starting with DWARF_RAW_.")


class FilesIn(BaseModel):
    path: str = Field("", description="Folder under the album. Empty means the album root.")


class FileEntry(BaseModel):
    name: str
    size: int
    dir: bool


class FileList(BaseModel):
    entries: list[FileEntry]


class ChunkIn(BaseModel):
    path: str = Field(description="File under the album.")
    offset: int = Field(0, ge=0)
    length: int = Field(1048576, gt=0, le=4194304, description="Bytes to read, 4 MiB at most.")


class Chunk(BaseModel):
    offset: int
    size: int = Field(description="Total file size in bytes.")
    data: str = Field(description="Base64 bytes starting at offset.")
    eof: bool


class Job(BaseModel):
    id: str | None = None
    state: str
    frames: int | None = None
    captured: int | None = Field(None, description="Frames on disk so far.")
    failed: int | None = Field(None, description="Frames the scope tagged failed. They count in captured.")
    session: str | None = None
    error: str | None = None


class StatusOut(BaseModel):
    app_connected: bool
    busy_elsewhere: bool = Field(
        False, description="The scope reports a capture that this service did not start."
    )
    job: Job | None
    rotation: float | None = None
    pitch: float | None = None
    tele_darks: int | None = None
    wide_darks: int | None = None
    sd_free_gb: float | None = Field(None, description="Free space on the scope SD card, when known.")
    sd_total_gb: float | None = Field(None, description="Size of the scope SD card, as last reported.")
    capture_state: int | None = Field(None, description="Scope capture state as last reported. 1 is running, 0 is idle.")
    tracking_state: int | None = Field(None, description="Scope tracking state as last reported. 1 is tracking.")
    goto_state: int | None = Field(None, description="Scope goto state code as last reported. Meaning not confirmed.")
    calibration_state: int | None = Field(
        None, description="Scope calibration state code as last reported. 0 is idle, other codes not confirmed."
    )
    battery_percent: float | None = Field(None, description="Battery charge, as the scope last reported it.")
    device_temperature_c: float | None = Field(None, description="Scope body temperature, as last reported.")
    tele_sensor_temperature_c: float | None = Field(None, description="Tele sensor temperature, as last reported.")
    wide_sensor_temperature_c: float | None = Field(None, description="Wide sensor temperature, as last reported.")
    scope_reachable: bool | None = Field(None, description="The scope answered on its API port at the last probe.")


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


@mcp_tool(name="capabilities", title="List service capabilities",
          description="Return the spec version and the features this service supports.",
          annotations=read_only(), error_map=TOOL_ERRORS, authentication=AuthMode.NONE)
@http_endpoint(path="/api/capabilities", operation_id="getCapabilities", method="GET",
               response_model=CapabilitiesOut,
               description="Return the spec version and the features this service supports.",
               tags=["Scope"], error_map=CONTROL_ERRORS, authentication=AuthMode.NONE)
def capabilities() -> CapabilitiesOut:
    return CapabilitiesOut(**svc().capabilities())


@mcp_tool(name="status", title="Read scope status",
          description="Read motor positions, app connection, dark counts, and the job state.",
          annotations=read_only(), error_map=TOOL_ERRORS, authentication=AuthMode.NONE)
@http_endpoint(path="/api/status", operation_id="getStatus", method="GET", response_model=StatusOut,
               description="Read motor positions, app connection, dark counts, and the job state.",
               tags=["Scope"], error_map=CONTROL_ERRORS, authentication=AuthMode.NONE)
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


@mcp_tool(name="delete_session", title="Delete a capture session",
          description="Delete one capture session folder from the scope SD card. This cannot be undone. "
                      "Refused while a capture runs.",
          annotations=acts(destructive_hint=True, idempotent_hint=True), error_map=TOOL_ERRORS)
@http_endpoint(path="/api/sessions/delete", operation_id="deleteSession", method="POST", payload_model=SessionIn,
               response_model=Message, description="Delete one capture session from the scope SD card.",
               tags=["Files"], error_map=CONTROL_ERRORS)
def delete_session(p: SessionIn) -> Message:
    return Message(message=svc().delete_session(p.session))


@http_endpoint(path="/api/files", operation_id="listFiles", method="GET", payload_model=FilesIn,
               response_model=FileList, description="List one folder of the scope album.", tags=["Files"],
               error_map=CONTROL_ERRORS)
def list_files(p: FilesIn) -> FileList:
    return FileList(entries=svc().list_files(p.path))


@http_endpoint(path="/api/files/chunk", operation_id="readFileChunk", method="GET", payload_model=ChunkIn,
               response_model=Chunk, description="Read a byte range of one album file. Resume at any offset.",
               tags=["Files"], error_map=CONTROL_ERRORS)
def read_file_chunk(p: ChunkIn) -> Chunk:
    return Chunk(**svc().read_chunk(p.path, p.offset, p.length))


@http_endpoint(path="/api/frame", operation_id="getFrame", method="GET", payload_model=FrameIn,
               response_model=Frame, description="Grab one live frame from a camera.", tags=["Preview"],
               error_map=CONTROL_ERRORS)
def get_frame(p: FrameIn) -> Frame:
    return Frame(**svc().frame(p.camera))


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


@http_endpoint(path="/api/point", operation_id="pointScope", method="POST", payload_model=PointIn,
               response_model=Message, description="Move the motors to absolute positions.",
               tags=["Scope"], error_map=CONTROL_ERRORS)
def point(p: PointIn) -> Message:
    return Message(message=svc().point(p.pitch, p.rotation))


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
          description="Start a capture job that stops at the frame count. Exposure and filter are names from "
                      "the capture options in capabilities.",
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


app = FastAPI(title="DWARF relay", version=__version__)
mount_endpoints(app)


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    text = svc().relay.render_metrics()
    return Response(text, headers={"Content-Type": "text/plain; version=0.0.4"})


@app.get("/api/events", include_in_schema=False)
async def events(request: Request) -> StreamingResponse:
    """Server-sent events. A `status` event on connect and on every change, then a comment every 15 s."""
    return StreamingResponse(
        status_events(svc().live_status, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
