"""DWARF II and mini support, checked against what the library and dwarfAlp know. No hardware run yet."""
import io
import json

import pytest

from dwarf_relay import models
from dwarf_relay.proxy import rename
from dwarf_relay.scope import Scope, ScopeError
from dwarf_relay.wire import parse


@pytest.mark.parametrize("device_id, expected", [(1, models.DWARF2), (2, models.DWARF3), (4, models.DWARF_MINI)])
def test_model_from_device_id(device_id, expected):
    assert models.from_device_info({"deviceId": device_id}) is expected


@pytest.mark.parametrize("name, expected", [
    ("DWARF3_3AD246", models.DWARF3), ("DWARF_mini_1A2B3C", models.DWARF_MINI), ("DWARF_1A2B3C", models.DWARF2),
])
def test_model_from_device_name_when_the_id_is_missing(name, expected):
    assert models.from_device_info({"deviceName": name}) is expected


def test_unknown_device_is_none():
    assert models.from_device_info({"deviceId": 3, "deviceName": "DWARF3PRO_000000"}) is None


def test_detect_asks_the_scope_and_honours_the_override():
    def urlopen(request, timeout):
        assert request.full_url == "http://scope:8082/deviceInfo"
        return io.BytesIO(json.dumps({"code": 0, "data": {"deviceId": 4}}).encode())

    assert models.detect("scope", env={}, urlopen=urlopen) is models.DWARF_MINI
    assert models.detect("scope", env={"DWARF_MODEL": "dwarf2"}, urlopen=urlopen) is models.DWARF2

    def offline(request, timeout):
        raise OSError("no route")

    assert models.detect("scope", env={}, urlopen=offline) is None


def test_exposure_tables_follow_the_library():
    assert models.exposure_index(models.DWARF3, "30") == 159
    assert models.exposure_index(models.DWARF_MINI, "180") == 168
    assert models.exposure_index(models.DWARF2, "15") == 156
    with pytest.raises(ValueError, match="DWARF II has no 30 s exposure"):
        models.exposure_index(models.DWARF2, "30")
    with pytest.raises(ValueError, match="DWARF 3 has no 180 s"):
        models.exposure_index(models.DWARF3, "180")
    assert models.exposures(models.DWARF2)[-1] == "15"
    assert models.exposures(models.DWARF_MINI)[-1] == "180"


def test_filters_differ_by_model():
    assert models.filter_index(models.DWARF_MINI, "duo") == 2
    with pytest.raises(ValueError, match="DWARF mini has no 'vis' filter"):
        models.filter_index(models.DWARF_MINI, "vis")
    with pytest.raises(ValueError, match="DWARF II has no 'duo' filter"):
        models.filter_index(models.DWARF2, "duo")


@pytest.mark.parametrize("name, relay", [
    (b"DWARF3_3AD246", b"DWARF3_RELAY1"), (b"DWARF_mini_1A2B3C", b"DWARF_mini_RELAY1"), (b"DWARF_1A2B3C", b"DWARF_RELAY1"),
])
def test_the_relay_name_keeps_the_length_of_each_model(name, relay):
    body = b'{"deviceName":"' + name + b'","path":"/Astronomy/DWARF_RAW_TELE_M 33_EXP_15"}'
    renamed = rename(body)
    assert len(renamed) == len(body)
    assert relay in renamed and b"DWARF_RAW_TELE_M 33" in renamed


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, module, cmd, data=b""):
        self.sent.append((cmd, parse(data)))

    def call(self, module, cmd, data=b"", timeout=None):
        self.send(module, cmd, data)
        return [(1, 0), (2, 0), (3, 90.0)]

    def recv(self):
        return None


def mini_scope():
    return Scope("x", client=Recorder(), log=lambda *a: None, model=models.DWARF_MINI)


def test_mini_capture_settings():
    scope = mini_scope()
    scope.set_capture("180", 80, "duo")
    assert scope.client.sent == [
        (16700, [(1, 0x0201000000000001), (2, 1), (3, 168)]),
        (16701, [(1, 0x0201000000000002), (2, 1), (3, 80)]),
        (16703, [(1, 0x020100000000000D), (2, 2)]),
    ]


def test_an_unsupported_setting_fails_before_anything_is_sent():
    scope = mini_scope()
    with pytest.raises(ScopeError, match="no 'vis' filter"):
        scope.set_capture("15", 80, "vis")
    assert scope.client.sent == []


def test_unknown_motor_angles_leave_the_motors_alone():
    scope = mini_scope()
    scope.point_up()
    scope.close_barrel()
    assert scope.client.sent == []


def test_the_own_connection_uses_the_model_header():
    from dwarf_relay.client import Client

    client = Client.__new__(Client)
    client.header, client.client_id, client.ws = models.DWARF_MINI.header, "id", type("WS", (), {})()
    sent = []
    client.ws.send_binary = sent.append
    client.send(3, 11002)
    assert parse(sent[0])[:3] == [(1, 1), (2, 20), (3, 4)]


def test_capabilities_report_the_detected_model_and_its_choices():
    from types import SimpleNamespace

    from dwarf_relay.mcp_server import Service

    service = Service(SimpleNamespace(app_link=None, app_connected=False, capture_running=False), "127.0.0.1", loop=None)
    assert service.capabilities()["service"]["model"] == "DWARF 3", "before the scope answers"
    service._note_device_info({"deviceId": 4, "deviceName": "DWARF_mini_1A2B3C"})
    caps = service.capabilities()
    assert caps["service"]["model"] == "DWARF mini"
    assert caps["capture"]["filters"] == ["astro", "duo"] and caps["capture"]["exposures"][-1] == "180"
