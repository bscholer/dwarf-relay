"""Prometheus metrics from the relay traffic. Scope values update only while a client is connected."""
import time

COMMAND_NAMES = {
    11000: "calibrate", 11002: "goto", 11005: "capture_start", 11006: "capture_stop", 11010: "go_live",
    11021: "dark_start_tele", 11025: "dark_start_wide", 14001: "motor_run_to", 14003: "motor_reset",
    14006: "joystick", 14008: "joystick_stop", 15004: "autofocus", 16402: "shooting_mode",
}
STATE_GAUGES = {15208: "capture_state", 15210: "calibration_state", 15211: "goto_state", 15212: "tracking_state"}
NOTIFICATION_MIN, REQUEST = 15200, 0
ERROR_CODE_MIN = 10000
QUIET_COMMANDS = {14006}


def command_name(cmd: int) -> str:
    return COMMAND_NAMES.get(cmd, str(cmd))


def error_code(fields: list) -> int | None:
    """Return a firmware error code. Replies carry it in field 1 or, for motors, field 2."""
    for field, value in fields:
        if field in (1, 2) and isinstance(value, int) and value <= -ERROR_CODE_MIN:
            return value
    return None


class Metrics:
    def __init__(self):
        self.gauges: dict[tuple, float] = {}
        self.counters: dict[tuple, float] = {}

    def set(self, name: str, value: float, **labels) -> None:
        self.gauges[(name, tuple(sorted(labels.items())))] = value

    def add(self, name: str, value: float = 1, **labels) -> None:
        key = (name, tuple(sorted(labels.items())))
        self.counters[key] = self.counters.get(key, 0) + value

    def observe(self, source: str, cmd: int | None, packet_type: int | None, fields: list) -> None:
        self.add("packets_total", source=source)
        values = dict(fields)
        if source != "scope":
            if not packet_type:
                self.add("commands_total", source=source, command=command_name(cmd))
            return
        code = error_code(fields)
        if code is not None:
            self.add("errors_total", command=command_name(cmd), code=str(code))
        if cmd is None or cmd < NOTIFICATION_MIN or packet_type != 2:
            return
        self.set("last_notification_timestamp_seconds", time.time())
        if cmd == 15201:
            self.set("battery_percent", values.get(1, 0))
        elif cmd == 15203:
            self.set("sd_free_gigabytes", values.get(1, 0))
            self.set("sd_total_gigabytes", values.get(2, 0))
        elif cmd == 15243:
            self.set("device_temperature_celsius", values.get(2, 0))
        elif cmd == 15292:
            self.set("sensor_temperature_celsius", values.get(1, 0), camera="wide" if values.get(2) == 1 else "tele")
        elif cmd == 15209:
            self.set("capture_frames_target", values.get(1, 0))
            self.set("capture_frames_stacked", values.get(4, 0))
        elif cmd in STATE_GAUGES:
            self.set(STATE_GAUGES[cmd], values.get(1, 0))

    def render(self) -> str:
        lines = []
        for table, kind in ((self.gauges, "gauge"), (self.counters, "counter")):
            seen = set()
            for (name, labels), value in sorted(table.items()):
                if name not in seen:
                    lines.append(f"# TYPE dwarf3_{name} {kind}")
                    seen.add(name)
                text = ",".join(f'{k}="{v}"' for k, v in labels)
                lines.append(f"dwarf3_{name}{{{text}}} {value}" if text else f"dwarf3_{name} {value}")
        return "\n".join(lines) + "\n"


def summary(event: dict) -> dict | None:
    """Pick the event fields for stdout. Payloads stay out, because they hold the site location."""
    kind = event.get("event")
    if kind in ("upstream_failed", "ws_close", "ws_decode_error"):
        return {k: v for k, v in event.items() if k in ("event", "port", "code", "reason", "error")}
    if kind == "open" and event.get("port") == 9900:
        return {"event": "control_open", "peer": event.get("peer")}
    is_command = kind == "inject" or (kind == "ws" and event.get("source") == "app" and not event.get("type"))
    if is_command and event.get("cmd") not in QUIET_COMMANDS:
        return {"event": "command", "source": "inject" if kind == "inject" else "app", "command": command_name(event.get("cmd"))}
    if kind == "ws" and event.get("source") == "scope":
        code = error_code(event.get("fields") or [])
        if code is not None:
            return {"event": "scope_error", "command": command_name(event.get("cmd")), "code": code}
    return None
