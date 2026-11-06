"""Scope operations, with the firmware behavior found by live tests."""
import time

from .client import Client
from .files import Album, count_frames, raw_sessions
from .wire import field_bytes, field_double, field_int, parse

MODULE_ASTRO, MODULE_SYSTEM, MODULE_MOTOR, MODULE_FOCUS = 3, 4, 6, 8
MODULE_DEVICE_CONFIG, MODULE_PARAMS = 14, 15

MOTOR_ROTATION, MOTOR_PITCH = 1, 2
JOYSTICK, JOYSTICK_STOP = 14006, 14008
PITCH_CLOSED = 0.0
PITCH_ZENITH = 172.5  # Set by eye on a tripod. The polar value 169 is about 3 degrees short.
ROTATION_CENTER = 158.0

PARAM_EXPOSURE = 0x0201000000000001
PARAM_GAIN = 0x0201000000000002
PARAM_IR_FILTER = 0x020100000000000D
PARAM_STACK_COUNT = 0x0202000000000010

FILTERS = {"vis": 0, "astro": 1, "duo": 2}
EXPOSURE_INDEX = {"15": 156, "30": 159, "60": 162}
DARK_GAIN_INDEX = {"60": 18}

STATE_RUNNING = 1
NOTIFY_CALIBRATION = 15210
NOTIFY_CAPTURE = 15208
NOTIFY_TRACKING = 15212
ALBUM_ROOT = "/Astronomy"


class ScopeError(RuntimeError):
    pass


def reply_code(fields, code_field: int = 1) -> int | None:
    return None if fields is None else dict(fields).get(code_field, 0)


class Scope:
    def __init__(self, host: str, log=print, client=None):
        self.host = host
        self.log = log
        self.client = client or Client(host)

    def _ok(self, name: str, fields, code_field: int = 1) -> None:
        code = reply_code(fields, code_field)
        if code != 0:
            raise ScopeError(f"{name} failed, code {code}")
        self.log(f"{name}: ok")

    def _wait(self, cmd: int, timeout: float, until) -> list | None:
        end = time.time() + timeout
        while time.time() < end:
            packet = self.client.recv()
            if packet and packet[0] == cmd and until(dict(packet[2])):
                return packet[2]
        return None

    def prepare(self, lat: float, lon: float, utc_offset: float, timezone: str) -> None:
        c = self.client
        self._ok("set time", c.call(MODULE_SYSTEM, 13000, field_int(1, int(time.time())) + field_double(2, utc_offset)))
        self._ok("set timezone", c.call(MODULE_SYSTEM, 13001, field_bytes(1, timezone.encode())))
        self._ok("set location", c.call(MODULE_SYSTEM, 13010, field_double(1, lat) + field_double(2, lon)))
        c.call(MODULE_ASTRO, 11010)
        self._ok("astro mode", c.call(MODULE_DEVICE_CONFIG, 16402, field_int(1, 2)))
        self._ok("enter camera", c.call(MODULE_DEVICE_CONFIG, 16404, field_bytes(3, field_int(1, 1))))
        self._ok("deep sky tech", c.call(MODULE_DEVICE_CONFIG, 16403, field_int(1, 2)))

    def position(self, motor: int) -> float | None:
        fields = self.client.call(MODULE_MOTOR, 14011, field_int(1, motor), timeout=8)
        return None if fields is None else dict(fields).get(3)

    def move(self, motor: int, position: float, speed: float = 10.0, resolution: int = 3) -> None:
        data = (field_int(1, motor) + field_double(2, position) + field_double(3, speed)
                + field_int(4, 100) + field_int(5, resolution))
        self._ok(f"move motor {motor} to {position}", self.client.call(MODULE_MOTOR, 14001, data, timeout=60), 2)

    def home(self) -> None:
        self._ok("reset rotation", self.client.call(MODULE_MOTOR, 14003, field_int(1, MOTOR_ROTATION), timeout=120), 2)
        self._ok("reset pitch", self.client.call(MODULE_MOTOR, 14003, field_int(1, MOTOR_PITCH) + field_int(2, 1), timeout=120), 2)

    def point_up(self) -> None:
        self.move(MOTOR_ROTATION, ROTATION_CENTER)
        self.move(MOTOR_PITCH, PITCH_ZENITH)

    def close_barrel(self) -> None:
        # The firmware returns pitch to about 12.5 after it reaches 0. The official app does the same.
        self.move(MOTOR_PITCH, PITCH_CLOSED, resolution=2)

    def focus_infinity(self) -> None:
        self._ok("infinity focus", self.client.call(MODULE_FOCUS, 15004, field_int(1, 1), timeout=90))

    def autofocus(self) -> None:
        self._ok("autofocus", self.client.call(MODULE_FOCUS, 15004, timeout=120))

    def calibrate(self, timeout: float = 300) -> None:
        self.client.send(MODULE_ASTRO, 11000)
        end = time.time() + timeout
        while time.time() < end:
            packet = self.client.recv()
            if packet and packet[0] == 11000 and packet[1] != 0:
                self._ok("calibration", packet[2])
                return
        raise ScopeError("calibration timed out")

    def goto(self, name: str, ra_hours: float, dec_deg: float, timeout: float = 240) -> None:
        self.client.send(MODULE_ASTRO, 11002, field_double(1, ra_hours) + field_double(2, dec_deg) + field_bytes(3, name.encode()))
        if self._wait(NOTIFY_TRACKING, timeout, lambda f: f.get(1) == STATE_RUNNING) is None:
            raise ScopeError("goto did not reach tracking state")
        self.log("goto: tracking")

    def set_capture(self, exposure: str, gain: int, filter_name: str) -> None:
        c = self.client
        self._ok("exposure", c.call(MODULE_PARAMS, 16700, field_int(1, PARAM_EXPOSURE) + field_int(2, 1) + field_int(3, EXPOSURE_INDEX[exposure])))
        self._ok("gain", c.call(MODULE_PARAMS, 16701, field_int(1, PARAM_GAIN) + field_int(2, 1) + field_int(3, gain)))
        self._ok("filter", c.call(MODULE_PARAMS, 16703, field_int(1, PARAM_IR_FILTER) + field_int(2, FILTERS[filter_name])))

    def capture(self, frames: int, filter_name: str, exposure_s: float, cancel=None) -> str:
        """Capture frames and stop at the count. Return the session folder.

        Capture start resets stackCount to 999, so the frame count comes from the
        FTP listing. A start error can arrive while the capture still runs, so this
        follows the capture state and never exits on the start reply.
        """
        album = Album(self.host)
        try:
            before = raw_sessions(album.names(ALBUM_ROOT))
            self.client.send(MODULE_ASTRO, 11005, field_int(1, FILTERS[filter_name]) + field_int(2, 1))
            end = time.time() + 120 + frames * (exposure_s + 15)
            session = None
            count = 0
            next_poll = time.time() + 5
            while time.time() < end and not (cancel and cancel.is_set()):
                packet = self.client.recv()
                if packet and packet[0] == 11005 and packet[1] != 0:
                    self.log(f"capture start reply: code {reply_code(packet[2])}")
                if time.time() < next_poll:
                    continue
                next_poll = time.time() + 5
                new = raw_sessions(album.names(ALBUM_ROOT)) - before
                if new:
                    session = max(new, key=lambda n: n[-23:])
                    count = count_frames(album.names(f"{ALBUM_ROOT}/{session}"))
                    self.log(f"frames: {count}/{frames}")
                if count >= frames:
                    break
            self.stop_capture()
            if session is None:
                raise ScopeError("capture produced no session folder")
            return session
        finally:
            album.close()

    def stop_capture(self) -> None:
        self.client.send(MODULE_ASTRO, 11006)
        idle = self._wait(NOTIFY_CAPTURE, 60, lambda f: f.get(1, 0) != STATE_RUNNING)
        self.log("capture stop sent" if idle is None else "capture stopped")

    def dark_list(self, wide: bool = False) -> list[dict]:
        fields = self.client.call(MODULE_ASTRO, 11027 if wide else 11023) or []
        rows = []
        for field, value in fields:
            if field == 2:
                row = dict(parse(value))
                rows.append({"exp": row[4].decode(), "gain": row[5].decode(), "bin": row[6].decode(), "temp_c": row.get(7)})
        return rows

    def start_dark(self, exposure: str, gain: str, frames: int, wide: bool = False) -> None:
        """Start a dark capture. The scope sends no reply, so watch the dark folder for completion."""
        data = field_int(1, EXPOSURE_INDEX[exposure]) + field_int(2, DARK_GAIN_INDEX[gain]) + field_int(4, frames)
        self.client.send(MODULE_ASTRO, 11025 if wide else 11021, data)

    def joystick(self, angle: float, length: float, seconds: float) -> None:
        vector = field_double(1, angle) + field_double(2, length)
        end = time.time() + seconds
        try:
            while time.time() < end:
                self.client.send(MODULE_MOTOR, JOYSTICK, vector)
                time.sleep(0.05)
        finally:
            self.client.send(MODULE_MOTOR, JOYSTICK_STOP)

    def close(self) -> None:
        self.client.close()
