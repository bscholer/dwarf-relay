"""Minimal protobuf encoder and decoder for the DWARF WebSocket packets."""
import struct


def varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field_int(num: int, value: int) -> bytes:
    return varint(num << 3) + varint(value)


def field_bytes(num: int, value: bytes) -> bytes:
    return varint(num << 3 | 2) + varint(len(value)) + value


def field_double(num: int, value: float) -> bytes:
    return varint(num << 3 | 1) + struct.pack("<d", value)


def parse(buf: bytes) -> list[tuple[int, object]]:
    """Return (field, value) pairs. Length-delimited fields stay as bytes."""
    i = 0
    out: list[tuple[int, object]] = []

    def read_varint() -> int:
        nonlocal i
        value, shift = 0, 0
        while True:
            c = buf[i]
            i += 1
            value |= (c & 0x7F) << shift
            shift += 7
            if not c & 0x80:
                return value

    while i < len(buf):
        key = read_varint()
        field, wire = key >> 3, key & 7
        if wire == 0:
            value = read_varint()
            out.append((field, value - (1 << 64) if value >= 1 << 63 else value))
        elif wire == 1:
            out.append((field, struct.unpack("<d", buf[i : i + 8])[0]))
            i += 8
        elif wire == 2:
            size = read_varint()
            out.append((field, buf[i : i + size]))
            i += size
        elif wire == 5:
            out.append((field, struct.unpack("<f", buf[i : i + 4])[0]))
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
    return out
