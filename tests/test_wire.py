from dwarf_relay.protocol import astro, base, param
from dwarf_relay.wire import parse


def test_negative_codes_survive_round_trip():
    # Firmware error codes are negative int32 values on the 10-byte varint path.
    assert parse(base.ComResponse(code=-11513).SerializeToString()) == [(1, -11513)]


def test_parse_a_multibyte_param_id():
    data = param.ReqSetExposure(param_id=0x0202000000000010, mode=1, value=159).SerializeToString()
    assert parse(data) == [(1, 0x0202000000000010), (2, 1), (3, 159)]


def test_parse_doubles_and_strings():
    data = astro.ReqGotoDSO(ra=5.9, dec=-100.0, target_name="NGC 2174").SerializeToString()
    assert parse(data) == [(1, 5.9), (2, -100.0), (3, b"NGC 2174")]
