"""Schema-free protobuf decoder for DWARF packets.

The relay logs and watches every packet, including ones it has no message type for, so it
decodes by field number here. Requests are built from the typed messages in protocol.py.
"""
import struct


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
