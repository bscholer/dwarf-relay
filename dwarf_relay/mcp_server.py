"""Scope control operations. They use the relay connection path."""
import base64
import datetime as dt
import logging
import math
import os
import threading
import uuid

from . import __version__, frame, models
from .client import Client
from .files import Album, delete_session, sd_free_from_info
from .spec import CAPABILITY_OPERATIONS, SPEC_VERSION
from .scope import MOTOR_PITCH, MOTOR_ROTATION, Scope

log = logging.getLogger("dwarf_relay.service")

MIN_SUN_SEPARATION_DEG = 30.0
MAX_MOVE_SECONDS = 5.0
LOCK_WAIT_S = 30.0
MAX_FRAMES = 500
LOCAL_RELAY = "127.0.0.1"


MIN_PITCH = 0.0


def sun_position(when: dt.datetime) -> tuple[float, float]:
    """Return the Sun RA in hours and Dec in degrees, within about one degree."""
    days = (when - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)).total_seconds() / 86400
    mean_long = math.radians((280.46 + 0.9856474 * days) % 360)
    anomaly = math.radians((357.528 + 0.9856003 * days) % 360)
    ecliptic = mean_long + math.radians(1.915) * math.sin(anomaly) + math.radians(0.02) * math.sin(2 * anomaly)
    tilt = math.radians(23.439)
    ra = math.atan2(math.cos(tilt) * math.sin(ecliptic), math.cos(ecliptic))
    return (math.degrees(ra) / 15) % 24, math.degrees(math.asin(math.sin(tilt) * math.sin(ecliptic)))


def album_path(path: str, root: str = models.DWARF3.album_roots[0]) -> str:
    """Map a path under the album to its full scope path. Refuse any path outside the album."""
    parts = [p for p in path.split("/") if p]
    if ".." in parts or "." in parts:
        raise ValueError("path must stay inside the album")
    return "/".join([root, *parts])


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    d1, d2, dra = math.radians(dec1), math.radians(dec2), math.radians((ra1 - ra2) * 15)
    cosine = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


class SunTooCloseError(ValueError):
    pass


def require_safe_target(ra: float, dec: float, when: dt.datetime | None = None) -> None:
    sun_ra, sun_dec = sun_position(when or dt.datetime.now(dt.timezone.utc))
    distance = separation_deg(ra, dec, sun_ra, sun_dec)
    if distance < MIN_SUN_SEPARATION_DEG:
        raise SunTooCloseError(f"target is {distance:.0f} degrees from the Sun, the limit is {MIN_SUN_SEPARATION_DEG:.0f}")


class Service:
    """Run tool calls one at a time, because the scope has one control connection."""

    def __init__(self, relay, scope_host: str, loop):
        self.relay, self.scope_host, self.loop = relay, scope_host, loop
        self.lock = threading.Lock()
        self.job: dict | None = None
        self.cancel = threading.Event()
        self.positions: dict = {}
        self._model: models.Model | None = models.by_key(os.environ["DWARF_MODEL"]) if os.environ.get("DWARF_MODEL") else None
        self._album_root: str | None = None

    @property
    def model(self) -> models.Model:
        """The connected model, asked of the scope once. The DWARF 3 until the scope has answered."""
        if self._model is None:
            found = models.detect(self.scope_host)
            if found is None:
                return models.DEFAULT
            self._model = found
            log.info("scope model: %s", found.name)
        return self._model

    def _note_device_info(self, info: dict) -> None:
        if self._model is None:
            self._model = models.from_device_info(info) or models.DEFAULT
            log.info("scope model: %s", self._model.name)

    def album_root(self) -> str:
        """The scope folder that holds astro sessions. Models name it differently, so look once."""
        if self._album_root:
            return self._album_root
        model = self.model
        if len(model.album_roots) == 1:
            return model.album_roots[0]
        album = Album(self.scope_host)
        try:
            self._album_root = next((r for r in model.album_roots if album.has_folder(r)), model.album_roots[0])
        finally:
            album.close()
        return self._album_root

    def _client(self):
        from .proxy import RelayClient

        link = self.relay.app_link
        return RelayClient(self.relay, self.loop) if link and link.header else Client(LOCAL_RELAY, header=self.model.header)

    def _scope(self) -> Scope:
        return Scope(self.scope_host, log=log.info, client=self._client(), reconnect=self._client, model=self.model,
                     album_root=self.album_root())

    def _with_scope(self, fn):
        if self.job and self.job["state"] == "running":
            raise RuntimeError("a capture job is running, call stop_job first")
        if not self.lock.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError("another tool call is running")
        scope = None
        try:
            scope = self._scope()
            return fn(scope)
        finally:
            if scope:
                scope.close()
            self.lock.release()

    def capabilities(self) -> dict:
        model = self._model or models.DEFAULT  # never wait on the scope here
        service = {"name": "dwarf-relay", "vendor": "DWARFLAB", "model": model.name, "version": __version__}
        capture = {"filters": list(model.filters), "exposures": models.exposures(model), "wide_darks": model.wide_astro}
        return {"spec_version": SPEC_VERSION, "service": service,
                "capabilities": sorted(CAPABILITY_OPERATIONS), "capture": capture}

    def status(self) -> dict:
        def read(s):
            return {"rotation": s.position(MOTOR_ROTATION), "pitch": s.position(MOTOR_PITCH),
                    "tele_darks": len(s.dark_list()), "wide_darks": len(s.dark_list(wide=True))}

        own_job = bool(self.job and self.job["state"] == "running")
        if own_job:
            # A second control connection makes the scope drop the capture's link, so reuse the last reading.
            read_values = dict(self.positions)
        else:
            read_values = self._with_scope(read)
            self.positions = {k: v for k, v in read_values.items() if k in ("rotation", "pitch")}
        return {
            "app_connected": self.relay.app_connected,
            "busy_elsewhere": bool(self.relay.capture_running) and not own_job,
            "job": self.job,
            "sd_free_gb": self._sd_free_gb(),
            **self._reported(),
            **read_values,
        }

    def live_status(self) -> dict:
        """What the relay knows right now, without asking the scope. The event stream sends this."""
        own_job = bool(self.job and self.job["state"] == "running")
        gauges = getattr(getattr(self.relay, "metrics", None), "gauges", {})
        return {
            "app_connected": self.relay.app_connected,
            "busy_elsewhere": bool(self.relay.capture_running) and not own_job,
            "job": self.job,
            "sd_free_gb": gauges.get(("sd_free_gigabytes", ())),
            **self._reported(),
            **self.positions,
        }

    def _reported(self) -> dict:
        """Values the scope pushes while a client is connected. They hold the last reading."""
        gauges = getattr(getattr(self.relay, "metrics", None), "gauges", {})
        up = gauges.get(("scope_up", ()))
        def state(name):
            value = gauges.get((name, ()))
            return None if value is None else int(value)

        return {
            "sd_total_gb": gauges.get(("sd_total_gigabytes", ())),
            "capture_state": state("capture_state"),
            "tracking_state": state("tracking_state"),
            "goto_state": state("goto_state"),
            "calibration_state": state("calibration_state"),
            "battery_percent": gauges.get(("battery_percent", ())),
            "device_temperature_c": gauges.get(("device_temperature_celsius", ())),
            "tele_sensor_temperature_c": gauges.get(("sensor_temperature_celsius", (("camera", "tele"),))),
            "wide_sensor_temperature_c": gauges.get(("sensor_temperature_celsius", (("camera", "wide"),))),
            "scope_reachable": None if up is None else bool(up),
        }

    def _sd_free_gb(self) -> float | None:
        info = models.device_info(self.scope_host)
        free = None
        if info is not None:
            self._note_device_info(info)
            free = sd_free_from_info(info)
        if free is not None:
            return free
        gauges = getattr(getattr(self.relay, "metrics", None), "gauges", {})
        return gauges.get(("sd_free_gigabytes", ()))

    def list_sessions(self) -> list[str]:
        album = Album(self.scope_host)
        try:
            return sorted(n for n in album.names(self.album_root()) if n.startswith("DWARF_RAW_"))
        finally:
            album.close()

    def delete_session(self, session: str) -> str:
        """Delete one capture session from the scope. Refuse while any capture runs."""
        if (self.job and self.job["state"] == "running") or self.relay.capture_running:
            raise RuntimeError("a capture is running, stop it before deleting")
        if not delete_session(self.scope_host, session, http_root=self.model.album_http_root):
            raise ValueError(f"no such session: {session}")
        log.warning("deleted session %s", session)
        return f"deleted {session}"

    def list_files(self, path: str = "") -> list[dict]:
        album = Album(self.scope_host)
        try:
            return album.entries(album_path(path, self.album_root()))
        finally:
            album.close()

    def read_chunk(self, path: str, offset: int, length: int) -> dict:
        full = album_path(path, self.album_root())
        album = Album(self.scope_host)
        try:
            size = next((e["size"] for e in album.entries(full.rsplit("/", 1)[0]) if e["name"] == full.rsplit("/", 1)[1]), None)
            if size is None:
                raise ValueError(f"no such file: {path}")
            data = album.read(full, offset, length)
        finally:
            album.close()
        return {"offset": offset, "size": size, "data": base64.b64encode(data).decode(), "eof": offset + len(data) >= size}

    def frame(self, camera: str) -> dict:
        data = frame.grab(self.scope_host, camera)
        return {"camera": camera, "data": base64.b64encode(data).decode()}

    def list_darks(self, wide: bool = False) -> list[dict]:
        return self._with_scope(lambda s: s.dark_list(wide=wide))

    def prepare(self) -> str:
        env = os.environ

        def run(s):
            s.prepare(float(env["DWARF3_LAT"]), float(env["DWARF3_LON"]), float(env["DWARF3_UTC_OFFSET"]), env["DWARF3_TZ"])
            s.home()
            s.point_up()

        self._with_scope(run)
        return "ready, pointing up" if self.model.pitch_zenith is not None else "ready"

    def move(self, angle: float, length: float, seconds: float) -> str:
        if not 0 <= length <= 1 or not 0 < seconds <= MAX_MOVE_SECONDS:
            raise ValueError(f"length must be 0 to 1, and seconds must be 0 to {MAX_MOVE_SECONDS:.0f}")
        self._with_scope(lambda s: s.joystick(angle, length, seconds))
        return "moved"

    def point(self, pitch: float, rotation: float | None) -> str:
        if not MIN_PITCH <= pitch <= self.model.max_pitch:
            raise ValueError(f"pitch must be {MIN_PITCH:.0f} to {self.model.max_pitch:.0f}")
        if rotation is not None and not 0 <= rotation <= 360:
            raise ValueError("rotation must be 0 to 360")
        if self.relay.capture_running and not (self.job and self.job["state"] == "running"):
            raise RuntimeError("the scope is busy with another capture")

        def run(s):
            s.move(MOTOR_PITCH, pitch)
            if rotation is not None:
                s.move(MOTOR_ROTATION, rotation)

        self._with_scope(run)
        return "pointed"

    def close_barrel(self) -> str:
        self._with_scope(lambda s: s.close_barrel())
        return "closed"

    def calibrate(self) -> str:
        def run(s):
            s.focus_infinity()
            s.calibrate()

        self._with_scope(run)
        return "calibrated"

    def autofocus(self) -> str:
        self._with_scope(lambda s: s.autofocus())
        return "focused"

    def goto(self, name: str, ra_hours: float, dec_degrees: float) -> str:
        require_safe_target(ra_hours, dec_degrees)
        self._with_scope(lambda s: s.goto(name, ra_hours, dec_degrees))
        return f"tracking {name}"

    def start_capture(self, frames: int, exposure: str = "15", gain: int = 60, filter: str = "duo") -> dict:
        if not 0 < frames <= MAX_FRAMES:
            raise ValueError(f"frames must be 1 to {MAX_FRAMES}")
        models.exposure_index(self.model, exposure)  # ValueError names the exposures this model has
        models.filter_index(self.model, filter)
        if self.job and self.job["state"] == "running":
            raise RuntimeError("a capture job is already running")
        if not self.lock.acquire(timeout=LOCK_WAIT_S):
            raise RuntimeError("another tool call is running")
        self.cancel.clear()
        self.job = {"id": uuid.uuid4().hex[:8], "state": "running", "frames": frames, "captured": 0, "failed": 0, "session": None, "error": None}
        threading.Thread(target=self._capture, args=(self.job, frames, exposure, gain, filter), daemon=True).start()
        return self.job

    def _capture(self, job: dict, frames: int, exposure: str, gain: int, filter_name: str) -> None:
        scope = None
        try:
            scope = self._scope()
            scope.set_capture(exposure, gain, filter_name)
            job["session"] = scope.capture(
                frames, filter_name, float(exposure), self.cancel, progress=lambda n, bad: job.update(captured=n, failed=bad)
            )
            job["state"] = "done"
        except Exception as exc:
            log.warning("capture job %s failed: %s", job["id"], exc)
            job.update(state="failed", error=str(exc))
        finally:
            if scope:
                scope.close()
            self.lock.release()

    def stop_job(self) -> str:
        """Stop our job. With no job running, stop a capture the scope still runs on its own."""
        self.cancel.set()
        if self.job and self.job["state"] == "running":
            return "stop requested"
        if self.relay.capture_running:
            log.warning("stopping a capture that no job owns")
            self._with_scope(lambda s: s.stop_capture())
            self.relay.capture_running = False
            return "stopped a capture that no job owned"
        return "nothing to stop"

    def start_dark(self, frames: int, exposure: str = "15", wide: bool = False) -> str:
        if wide and not self.model.wide_astro:
            raise ValueError(f"the {self.model.name} takes no wide-camera astro frames, so it has no wide darks")
        self._with_scope(lambda s: s.start_dark(exposure, "60", frames, wide=wide))
        return "dark capture sent"
