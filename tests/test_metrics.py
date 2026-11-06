from dwarf3.metrics import Metrics, summary


def test_sensor_temperature_splits_by_camera_and_missing_field_means_tele():
    m = Metrics()
    m.observe("scope", 15292, 2, [(1, 41)])
    m.observe("scope", 15292, 2, [(1, 39), (2, 1)])
    text = m.render()
    assert 'dwarf3_sensor_temperature_celsius{camera="tele"} 41' in text
    assert 'dwarf3_sensor_temperature_celsius{camera="wide"} 39' in text


def test_capture_state_without_field_reads_as_idle():
    m = Metrics()
    m.observe("scope", 15208, 2, [(1, 1)])
    m.observe("scope", 15208, 2, [])
    assert "dwarf3_capture_state 0" in m.render()


def test_motor_error_in_field_two_counts_and_positions_do_not():
    m = Metrics()
    m.observe("scope", 14011, 3, [(1, 1), (2, -14520)])
    m.observe("scope", 14011, 3, [(1, 2), (3, 158.0)])
    assert 'dwarf3_errors_total{code="-14520",command="14011"} 1' in m.render()
    assert m.render().count("errors_total{") == 1


def test_large_positive_param_value_is_not_an_error():
    m = Metrics()
    m.observe("scope", 16700, 3, [(1, 156)])
    assert "errors_total" not in m.render()


def test_stdout_summary_drops_joystick_and_payloads():
    assert summary({"event": "ws", "source": "app", "cmd": 14006, "type": None}) is None
    line = summary({"event": "ws", "source": "app", "cmd": 13010, "type": None, "fields": [[1, 12.5]], "data": "ff"})
    assert line == {"event": "command", "source": "app", "command": "13010"}


def test_scope_error_reply_reaches_stdout_summary():
    line = summary({"event": "ws", "source": "scope", "cmd": 11005, "type": 3, "fields": [[1, -11513]]})
    assert line == {"event": "scope_error", "command": "capture_start", "code": -11513}


def test_mcp_commands_keep_their_source_in_stdout():
    line = summary({"event": "ws", "source": "mcp", "cmd": 11000, "type": None})
    assert line == {"event": "command", "source": "mcp", "command": "calibrate"}
