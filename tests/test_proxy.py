import asyncio
import json
import struct

import pytest

from dwarf3.proxy import AppLink, Log, Relay, WsStream, decode_packet, http_requests, redact, rename
from dwarf3.wire import field_bytes, field_int, parse

HANDSHAKE = b"GET /?client_id=x HTTP/1.1\r\nUpgrade: websocket\r\n\r\n"


def frame(payload: bytes, opcode: int = 2, mask: bool = False) -> bytes:
    size = len(payload)
    if size < 126:
        head = bytes([0x80 | opcode, size | (0x80 if mask else 0)])
    else:
        head = bytes([0x80 | opcode, 126 | (0x80 if mask else 0)]) + struct.pack(">H", size)
    if not mask:
        return head + payload
    key = b"\x01\x02\x03\x04"
    return head + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))


def test_ws_stream_decodes_masked_extended_frames_split_at_every_byte():
    payload = bytes(range(256)) * 2
    data = HANDSHAKE + frame(payload, mask=True) + frame(b"\x03\xe8bye", opcode=8)
    stream = WsStream()
    frames = []
    for i in range(len(data)):
        frames += stream.feed(data[i : i + 1])
    assert frames == [(2, payload), (8, b"\x03\xe8bye")]


def test_decode_packet_keeps_undecodable_body_as_hex():
    packet = field_int(4, 3) + field_int(5, 11005) + field_int(6, 3) + field_bytes(7, b"\xff") + field_bytes(8, b"id")
    decoded = decode_packet(packet)
    assert (decoded["module"], decoded["cmd"], decoded["data"]) == (3, 11005, "ff")
    assert "fields" not in decoded


def test_redact_removes_scope_credentials_and_keeps_other_values():
    body = b'{"staWifiPwd": "secret1", "devicePwd":"secret2", "ble_psd":"secret3", "staWifiName": "net"}'
    result = redact(body)
    assert b"secret" not in result
    assert b'"staWifiName": "net"' in result


def test_relay_passes_bytes_unchanged_and_logs_control_traffic(tmp_path):
    packet = field_int(4, 6) + field_int(5, 14011) + field_int(6, 0)
    request = HANDSHAKE + frame(packet, mask=True)
    reply = b"HTTP/1.1 101 Switching\r\n\r\n" + frame(field_int(5, 14011) + field_int(6, 3))

    async def scenario():
        seen = []

        async def scope(reader, writer):
            seen.append(await reader.readexactly(len(request)))
            writer.write(reply)
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(scope, "127.0.0.1", 0)
        upstream_port = upstream.sockets[0].getsockname()[1]
        log = Log(tmp_path / "proxy.jsonl")
        relay = Relay("127.0.0.1", log, ports=(0,))
        relay._upstream_port = lambda _: upstream_port
        relay_server = await asyncio.start_server(lambda r, w: relay.handle(9900, r, w), "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection("127.0.0.1", relay_server.sockets[0].getsockname()[1])
        writer.write(request)
        await writer.drain()
        received = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        await asyncio.sleep(0.05)
        relay_server.close()
        upstream.close()
        return seen[0], received

    sent, received = asyncio.run(scenario())
    assert sent == request
    assert received == reply
    events = [json.loads(line) for line in (tmp_path / "proxy.jsonl").read_text().splitlines()]
    cmds = {(e["source"], e["cmd"], e["type"]) for e in events if e["event"] == "ws"}
    assert cmds == {("mcp", 14011, 0), ("scope", 14011, 3)}, "localhost clients are local tools"


class FakeWriter:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data += data

    async def drain(self):
        pass


def linked_relay(tmp_path):
    stream = WsStream()
    stream.feed(HANDSHAKE)
    writer = FakeWriter()
    relay = Relay("scope", Log(tmp_path / "proxy.jsonl"))
    relay.app_link = AppLink(5, stream, writer)
    relay.app_link.header = {1: 1, 2: 7, 3: 2, 8: b"app-id"}
    return relay, stream, writer


def test_inject_uses_app_identity_in_a_valid_masked_frame(tmp_path):
    relay, _, writer = linked_relay(tmp_path)
    asyncio.run(relay.inject(6, 14008, b""))

    assert writer.data[1] & 0x80, "scope requires masked client frames"
    reader = WsStream()
    reader.handshake = b"done"
    [(opcode, payload)] = reader.feed(bytes(writer.data))
    decoded = decode_packet(payload)
    assert (opcode, decoded["module"], decoded["cmd"], decoded["client"]) == (2, 6, 14008, "app-id")


def test_inject_waits_for_app_frame_to_finish(tmp_path):
    relay, stream, writer = linked_relay(tmp_path)
    partial = frame(b"payload", mask=True)
    stream.feed(partial[:3])

    async def scenario():
        task = asyncio.create_task(relay.inject(6, 14008, b""))
        await asyncio.sleep(0.02)
        assert not writer.data
        stream.feed(partial[3:])
        await task

    asyncio.run(scenario())
    assert writer.data


def test_inject_fails_with_no_app_connection(tmp_path):
    relay = Relay("scope", Log(tmp_path / "proxy.jsonl"))
    with pytest.raises(RuntimeError, match="no app connection"):
        asyncio.run(relay.inject(6, 14008, b""))


def test_rename_keeps_protobuf_length_prefix_valid():
    reply = field_bytes(8, b"DWARF3_57D0EC") + field_int(9, 1)
    assert parse(rename(reply)) == [(8, b"DWARF3_RELAY1"), (9, 1)]


def test_http_requests_finds_pipelined_requests_and_keeps_a_body_only_for_writes():
    chunk = (
        b"GET /thumb/a.jpg HTTP/1.1\r\nHost: x\r\n\r\n"
        b'POST /album/delete HTTP/1.1\r\nHost: x\r\nContent-Length: 20\r\n\r\n{"path":"/Astronomy/s"}'
    )
    assert http_requests(chunk) == [
        {"method": "GET", "path": "/thumb/a.jpg"},
        {"method": "POST", "path": "/album/delete", "body": '{"path":"/Astronomy/s"}'},
    ]
    assert http_requests(b"\x81\x05binary") == []
