"""Scope control operations. They use the relay connection path."""
import datetime as dt
import math
import os
import threading
import uuid

from .client import Client
from .files import Album
from .spec import CAPABILITY_OPERATIONS, SPEC_VERSION
from .scope import ALBUM_ROOT, EXPOSURE_INDEX, FILTERS, MOTOR_PITCH, MOTOR_ROTATION, Scope

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

    def capabilities(self) -> dict:
        return {"spec_version": SPEC_VERSION, "service": {"name": "dwarf3-relay", "vendor": "DWARFLAB", "model": "DWARF 3"},
                "capabilities": sorted(CAPABILITY_OPERATIONS)}

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
