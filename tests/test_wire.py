from dwarf3.wire import field_bytes, field_double, field_int, parse


def test_negative_codes_survive_round_trip():
    # Firmware error codes are negative int32 values on the 10-byte varint path.
    assert parse(field_int(1, -11513)) == [(1, -11513)]


def test_parse_mixed_fields_after_a_multibyte_param_id():
    data = field_int(1, 0x0202000000000010) + field_double(2, -100.0) + field_bytes(3, b"NGC 2174")
    assert parse(data) == [(1, 0x0202000000000010), (2, -100.0), (3, b"NGC 2174")]
