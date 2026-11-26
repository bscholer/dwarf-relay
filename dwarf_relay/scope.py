"""Scope operations, with the firmware behavior found by live tests."""
import time

from .client import Client, ConnectionLost
from . import models
from .files import Album, count_failed, count_frames, raw_sessions
from .protocol import CMD, astro, focus, motor, param, system, task_center
from .wire import parse

MODULE_ASTRO, MODULE_SYSTEM, MODULE_MOTOR = CMD.MODULE_ASTRO, CMD.MODULE_SYSTEM, CMD.MODULE_MOTOR
MODULE_FOCUS, MODULE_DEVICE_CONFIG, MODULE_PARAMS = CMD.MODULE_FOCUS, CMD.MODULE_DEVICE_CONFIG, CMD.MODULE_CAMERA_PARAMS

MOTOR_ROTATION, MOTOR_PITCH = 1, 2
CALIBRATE, GOTO, CAPTURE_START = CMD.CMD_ASTRO_START_CALIBRATION, CMD.CMD_ASTRO_START_GOTO_DSO, CMD.CMD_ASTRO_START_CAPTURE_RAW_LIVE_STACKING
AUTOFOCUS = CMD.CMD_FOCUS_START_ASTRO_AUTO_FOCUS
JOYSTICK, JOYSTICK_STOP = CMD.CMD_STEP_MOTOR_SERVICE_JOYSTICK, CMD.CMD_STEP_MOTOR_SERVICE_JOYSTICK_STOP
SHOOTING_MODE_ASTRO, SHOOTING_TECH_DEEP_SKY, ENCODE_TYPE = 2, 2, 1
PITCH_CLOSED = models.DWARF3.pitch_closed
PITCH_ZENITH = models.DWARF3.pitch_zenith
PITCH_TOLERANCE = 8.0  # A pitch further from zenith than this after point_up means the scope did not get there.
ROTATION_CENTER = models.DWARF3.rotation_center
ROTATION_LOCKED = -14511  # The firmware refuses a rotation reset in EQ mode.
MODE_REFUSED = -16602  # The firmware refuses a shooting mode change in some states.

PARAM_EXPOSURE = 0x0201000000000001
PARAM_GAIN = 0x0201000000000002
PARAM_IR_FILTER = 0x020100000000000D
PARAM_STACK_COUNT = 0x0202000000000010

FILTERS = models.DWARF3.filters  # The DWARF 3; other models: Scope.model.filters
DARK_GAIN_INDEX = {"60": 18}

STATE_RUNNING = 1
NOTIFY_CALIBRATION = CMD.CMD_NOTIFY_STATE_ASTRO_CALIBRATION
NOTIFY_CAPTURE = CMD.CMD_NOTIFY_STATE_CAPTURE_RAW_LIVE_STACKING
NOTIFY_TRACKING = CMD.CMD_NOTIFY_STATE_ASTRO_TRACKING
ALBUM_ROOT = "/Astronomy"
END_GRACE_S = 10  # A stop notice this soon after the start belongs to the start, not the end.
START_GRACE_S = 30  # A start the scope refuses must show a session folder within this time.
RECONNECTS = 3


def encode(message) -> bytes:
    return message.SerializeToString()


class ScopeError(RuntimeError):
    pass


def reply_code(fields, code_field: int = 1) -> int | None:
    return None if fields is None else dict(fields).get(code_field, 0)


class Scope:
    model: models.Model = models.DEFAULT
    album_root: str = ALBUM_ROOT

    def __init__(self, host: str, log=print, client=None, reconnect=None, model: models.Model = models.DEFAULT,
                 album_root: str | None = None):
        self.host = host
        self.model = model
        self.album_root = album_root or model.album_roots[0]
        self.log = log
        self.client = client or Client(host)
        self.reconnect = reconnect
        self.rotation_locked = False

    def _recv(self):
        """Read one packet. Open a new connection when the scope drops this one."""
        for attempt in range(RECONNECTS + 1):
            try:
                return self.client.recv()
            except ConnectionLost as exc:
                if self.reconnect is None or attempt == RECONNECTS:
                    raise
                self.log(f"connection lost ({exc}), reconnecting")
                time.sleep(1 + attempt)
                self.client = self.reconnect()

    def _retry(self, fn):
        """Run one command. When the connection went away (the app left, say), reconnect and run it again."""
        for attempt in range(RECONNECTS + 1):
            try:
                return fn(self.client)
            except ConnectionLost as exc:
                if self.reconnect is None or attempt == RECONNECTS:
                    raise
                self.log(f"connection lost ({exc}), reconnecting")
                time.sleep(attempt)
                self.client = self.reconnect()

    def _send(self, module: int, cmd: int, data: bytes = b"") -> None:
        self._retry(lambda client: client.send(module, cmd, data))

    def _call(self, module: int, cmd: int, data: bytes = b"", timeout: float = 20.0):
        return self._retry(lambda client: client.call(module, cmd, data, timeout=timeout))

    def _exposure(self, seconds: str) -> int:
        try:
            return models.exposure_index(self.model, seconds)
        except ValueError as exc:
            raise ScopeError(str(exc)) from exc

    def _filter(self, name: str) -> int:
        try:
            return models.filter_index(self.model, name)
        except ValueError as exc:
            raise ScopeError(str(exc)) from exc

    def _ok(self, name: str, fields, code_field: int = 1) -> None:
        code = reply_code(fields, code_field)
        if code != 0:
            raise ScopeError(f"{name} failed, code {code}")
        self.log(f"{name}: ok")

    def _wait(self, cmd: int, timeout: float, until) -> list | None:
        end = time.time() + timeout
        while time.time() < end:
            packet = self._recv()
            if packet and packet[0] == cmd and until(dict(packet[2])):
                return packet[2]
        return None

    def prepare(self, lat: float, lon: float, utc_offset: float, timezone: str) -> None:
        c = self.client
        self._ok("set time", c.call(MODULE_SYSTEM, CMD.CMD_SYSTEM_SET_TIME, encode(system.ReqSetTime(timestamp=int(time.time()), timezone_offset=utc_offset))))
        self._ok("set timezone", c.call(MODULE_SYSTEM, CMD.CMD_SYSTEM_SET_TIME_ZONE, encode(system.ReqSetTimezone(timezone=timezone))))
        self._ok("set location", c.call(MODULE_SYSTEM, CMD.CMD_V3_SYSTEM_SET_GPS_LOCATION, encode(system.ReqSetLocation(latitude=lat, longitude=lon))))
        c.call(MODULE_ASTRO, CMD.CMD_ASTRO_GO_LIVE)
        # dwarf_python_api names 16402 MODE_QUERY, but with a mode in the body it switches to that mode.
        mode = c.call(MODULE_DEVICE_CONFIG, CMD.CMD_V3_DEVICE_CONFIG_MODE_QUERY,
                      encode(task_center.ReqSwitchShootingMode(mode=SHOOTING_MODE_ASTRO)))
        if reply_code(mode) == MODE_REFUSED:
            self.log("astro mode: refused, keeping the current mode")
        else:
            self._ok("astro mode", mode)
        self._ok("enter camera", c.call(MODULE_DEVICE_CONFIG, CMD.CMD_V3_DEVICE_CONFIG_MODE_SWITCH, encode(
            task_center.ReqEnterCamera(client_param=task_center.ClientParams(encode_type=ENCODE_TYPE)))))
        self._ok("deep sky tech", c.call(MODULE_DEVICE_CONFIG, CMD.CMD_V3_DEVICE_CONFIG_SHOOTING_MODE,
                                           encode(task_center.ReqSwitchShootingTech(tech=SHOOTING_TECH_DEEP_SKY))))

    def position(self, motor_id: int) -> float | None:
        fields = self._call(MODULE_MOTOR, CMD.CMD_STEP_MOTOR_GET_POSITION, encode(motor.ReqMotorGetPosition(id=motor_id)), timeout=8)
        return None if fields is None else dict(fields).get(3)

    def move(self, motor_id: int, position: float, speed: float = 10.0, resolution: int = 3) -> None:
        data = encode(motor.ReqMotorRunTo(id=motor_id, end_position=position, speed=speed,
                                          speed_ramping=100, resolution_level=resolution))
        reply = self._call(MODULE_MOTOR, CMD.CMD_STEP_MOTOR_RUN_TO, data, timeout=60)
        self._ok(f"move motor {motor_id} to {position}", reply, 2)

    def home(self) -> None:
        fields = self._call(MODULE_MOTOR, CMD.CMD_STEP_MOTOR_RESET, encode(motor.ReqMotorReset(id=MOTOR_ROTATION)), timeout=120)
        self.rotation_locked = reply_code(fields, 2) == ROTATION_LOCKED
        if not self.rotation_locked:
            self._ok("reset rotation", fields, 2)
        pitch = encode(motor.ReqMotorReset(id=MOTOR_PITCH, direction=True))
        self._ok("reset pitch", self._call(MODULE_MOTOR, CMD.CMD_STEP_MOTOR_RESET, pitch, timeout=120), 2)

    def point_up(self) -> None:
        zenith = self.model.pitch_zenith
        if zenith is None:
            # Calibration slews on its own, so a scope that stays put here can still go on.
            self.log(f"point up: the motor angle for straight up is not known for the {self.model.name}, skipped")
            return
        if not self.rotation_locked and self.model.rotation_center is not None:
            self.move(MOTOR_ROTATION, self.model.rotation_center)
        self.move(MOTOR_PITCH, zenith)
        pitch = self.position(MOTOR_PITCH)
        if pitch is None or abs(pitch - zenith) > PITCH_TOLERANCE:
            raise ScopeError(f"scope is not pointing at the sky: pitch {pitch}, expected {zenith}")

    def close_barrel(self) -> None:
        # The firmware returns pitch to about 12.5 after it reaches 0. The official app does the same.
        if self.model.pitch_closed is None:
            self.log(f"close barrel: the closed motor angle is not known for the {self.model.name}, skipped")
            return
        self.move(MOTOR_PITCH, self.model.pitch_closed, resolution=2)

    def focus_infinity(self) -> None:
        self._ok("infinity focus", self._call(MODULE_FOCUS, AUTOFOCUS, encode(focus.ReqAstroAutoFocus(mode=1)), timeout=90))

    def autofocus(self) -> None:
        self._ok("autofocus", self._call(MODULE_FOCUS, AUTOFOCUS, timeout=120))

    def calibrate(self, timeout: float = 420) -> None:
        self._send(MODULE_ASTRO, CALIBRATE)
        end = time.time() + timeout
        while time.time() < end:
            packet = self.client.recv()
            if packet and packet[0] == CALIBRATE and packet[1] != 0:
                code = reply_code(packet[2])
                if code != 0:
                    raise ScopeError(f"calibration failed, code {code}, pitch {self.position(MOTOR_PITCH)}")
                self.log("calibration: ok")
                return
        raise ScopeError(f"calibration timed out, pitch {self.position(MOTOR_PITCH)}")

    def goto(self, name: str, ra_hours: float, dec_deg: float, timeout: float = 240) -> None:
        self._send(MODULE_ASTRO, GOTO, encode(astro.ReqGotoDSO(ra=ra_hours, dec=dec_deg, target_name=name)))
        end = time.time() + timeout
        while time.time() < end:
            packet = self.client.recv()
            if not packet:
                continue
            if packet[0] == GOTO and packet[1] != 0 and reply_code(packet[2]):
                raise ScopeError(f"goto refused: code {reply_code(packet[2])}")
            if packet[0] == NOTIFY_TRACKING and dict(packet[2]).get(1) == STATE_RUNNING:
                self.log("goto: tracking")
                return
        raise ScopeError("goto did not reach tracking state")

    def set_capture(self, exposure: str, gain: int, filter_name: str) -> None:
        c = self.client
        exposure_index, filter_index = self._exposure(exposure), self._filter(filter_name)  # check both before sending
        exposure_req = param.ReqSetExposure(param_id=PARAM_EXPOSURE, mode=1, value=exposure_index)
        self._ok("exposure", c.call(MODULE_PARAMS, CMD.CMD_V3_CAMERA_PARAMS_SET_PARAM, encode(exposure_req)))
        gain_req = param.ReqSetGain(param_id=PARAM_GAIN, mode=1, value=gain)
        self._ok("gain", c.call(MODULE_PARAMS, CMD.CMD_V3_CAMERA_PARAMS_SET_EXP_GAIN, encode(gain_req)))
        filter_req = param.ReqSetGeneralIntParam(param_id=PARAM_IR_FILTER, value=filter_index)
        self._ok("filter", c.call(MODULE_PARAMS, CMD.CMD_V3_CAMERA_PARAMS_ADJUST, encode(filter_req)))

    def capture(self, frames: int, filter_name: str, exposure_s: float, cancel=None, progress=None) -> str:
        """Capture frames and stop at the count. Return the session folder.

        Capture start resets stackCount to 999, so the frame count comes from the
        FTP listing. A start error can arrive while the capture still runs, so this
        follows the folder listing and never exits on the start reply alone. A refused
        start that makes no session folder within START_GRACE_S raises with the code.
        Any exit after the start sends a stop, so a failed job never leaves the scope shooting.
        """
        filter_index = self._filter(filter_name)
        album = Album(self.host)
        started = False
        try:
            before = raw_sessions(album.names(self.album_root))
            self._send(MODULE_ASTRO, CAPTURE_START, encode(astro.ReqCaptureRawLiveStacking(ir_index=filter_index, force_start=True)))
            started = True
            begun = time.time()
            end = begun + 120 + frames * (exposure_s + 15)
            session = None
            count = 0
            start_code = 0
            saw_running = False
            ended = False
            next_poll = time.time() + 5
            while time.time() < end and not (cancel and cancel.is_set()):
                packet = self._recv()
                if packet and packet[0] == CAPTURE_START and packet[1] != 0:
                    start_code = reply_code(packet[2]) or 0
                    self.log(f"capture start reply: code {start_code}")
                if packet and packet[0] == NOTIFY_CAPTURE:
                    running = dict(packet[2]).get(1, 0) == STATE_RUNNING
                    saw_running = saw_running or running
                    ended = saw_running and not running and time.time() - begun > END_GRACE_S
                if time.time() < next_poll:
                    continue
                next_poll = time.time() + 5
                new = raw_sessions(album.names(self.album_root)) - before
                if new:
                    session = max(new, key=lambda n: n[-23:])
                    names = album.names(f"{self.album_root}/{session}")
                    count = count_frames(names)
                    self.log(f"frames: {count}/{frames}")
                    if progress:
                        progress(count, count_failed(names))
                elif start_code and time.time() - begun > START_GRACE_S:
                    raise ScopeError(f"capture start refused, code {start_code}")
                if count >= frames or ended:
                    if ended and count < frames:
                        self.log(f"the scope ended the capture at {count}/{frames} frames")
                    break
            started = False
            self.stop_capture()
            if session is None:
                raise ScopeError("capture produced no session folder")
            return session
        finally:
            if started:
                self._stop_quietly()
            album.close()

    def _stop_quietly(self) -> None:
        try:
            self.stop_capture()
        except Exception as exc:
            self.log(f"stop after a failed capture did not finish: {exc}")

    def stop_capture(self) -> None:
        self._send(MODULE_ASTRO, CMD.CMD_ASTRO_STOP_CAPTURE_RAW_LIVE_STACKING)
        idle = self._wait(NOTIFY_CAPTURE, 60, lambda f: f.get(1, 0) != STATE_RUNNING)
        self.log("capture stop sent" if idle is None else "capture stopped")

    def dark_list(self, wide: bool = False) -> list[dict]:
        cmd = CMD.CMD_ASTRO_GET_WIDE_DARK_FRAME_LIST if wide else CMD.CMD_ASTRO_GET_DARK_FRAME_LIST
        fields = self._call(MODULE_ASTRO, cmd) or []
        rows = []
        for field, value in fields:
            if field == 2:
                row = dict(parse(value))
                rows.append({"exp": row[4].decode(), "gain": row[5].decode(), "bin": row[6].decode(), "temp_c": row.get(7)})
        return rows

    def start_dark(self, exposure: str, gain: str, frames: int, wide: bool = False) -> None:
        """Start a dark capture. The scope sends no reply, so watch the dark folder for completion."""
        data = encode(astro.ReqCaptureDarkFrameWithParam(
            exp_index=self._exposure(exposure), gain_index=DARK_GAIN_INDEX[gain], cap_size=frames))
        wide_cmd, tele_cmd = CMD.CMD_ASTRO_START_CAPTURE_WIDE_RAW_DARK_WITH_PARAM, CMD.CMD_ASTRO_START_CAPTURE_RAW_DARK_WITH_PARAM
        self._send(MODULE_ASTRO, wide_cmd if wide else tele_cmd, data)

    def joystick(self, angle: float, length: float, seconds: float) -> None:
        vector = encode(motor.ReqMotorServiceJoystick(vector_angle=angle, vector_length=length))
        end = time.time() + seconds
        try:
            while time.time() < end:
                self._send(MODULE_MOTOR, JOYSTICK, vector)
                time.sleep(0.05)
        finally:
            self._send(MODULE_MOTOR, JOYSTICK_STOP)

    def close(self) -> None:
        self.client.close()
