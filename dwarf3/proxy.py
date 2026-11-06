"""Logging relay between the DWARFLAB app and the scope.

Traffic passes through unchanged, except the device name in API and discovery
replies. Local tools can add commands to the live app connection, because the
scope accepts only one control client.
"""
import asyncio
import json
import os
import re
import socket
import struct
import time
from datetime import datetime
from pathlib import Path

from .wire import field_bytes, field_int, parse

CONTROL_PORT = 9900
API_PORT = 8082
TCP_PORTS = (80, 554, CONTROL_PORT, API_PORT, 8085, 8092)
INJECT_PORT = 9901
RELAY_NAME = b"DWARF3_RELAY1"  # Same length as the scope name, so Content-Length and protobuf sizes stay valid.
SCOPE_NAME = re.compile(rb"DWARF3_[0-9A-F]{6}")
INJECT_WAIT_S = 1.0
MAX_API_CAPTURE = 1 << 20
SECRET = re.compile(rb'("[A-Za-z_]*(?:pwd|password|psd)[A-Za-z_]*"\s*:\s*")[^"]*"', re.IGNORECASE)
OPCODE_BINARY, OPCODE_CLOSE = 2, 8


def rename(data: bytes) -> bytes:
    return SCOPE_NAME.sub(RELAY_NAME, data)


def redact(data: bytes) -> bytes:
    return SECRET.sub(rb'\1<redacted>"', data)


class WsStream:
    """Decode the frames of one direction of a WebSocket connection, in any chunk size."""

    def __init__(self):
        self.buf = bytearray()
        self.handshake: bytes | None = None

    def feed(self, chunk: bytes) -> list[tuple[int, bytes]]:
        self.buf += chunk
        if self.handshake is None:
            end = self.buf.find(b"\r\n\r\n")
            if end < 0:
                return []
            self.handshake = bytes(self.buf[:end])
            del self.buf[: end + 4]
        frames = []
        while (frame := self._next_frame()) is not None:
            frames.append(frame)
        return frames

    def _next_frame(self) -> tuple[int, bytes] | None:
        buf = self.buf
        if len(buf) < 2:
            return None
        opcode, masked, size, pos = buf[0] & 0x0F, buf[1] & 0x80, buf[1] & 0x7F, 2
        if size in (126, 127):
            width = 2 if size == 126 else 8
            if len(buf) < pos + width:
                return None
            size = int.from_bytes(buf[pos : pos + width], "big")
            pos += width
        mask_size = 4 if masked else 0
        if len(buf) < pos + mask_size + size:
            return None
        payload = bytes(buf[pos + mask_size : pos + mask_size + size])
        if masked:
            mask = buf[pos : pos + 4]
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        del buf[: pos + mask_size + size]
        return opcode, payload


def client_frame(payload: bytes) -> bytes:
    """Build one masked binary frame, as a WebSocket client must send it."""
    size = len(payload)
    if size < 126:
        head = bytes([0x82, 0x80 | size])
    elif size < 1 << 16:
        head = bytes([0x82, 0x80 | 126]) + size.to_bytes(2, "big")
    else:
        head = bytes([0x82, 0x80 | 127]) + size.to_bytes(8, "big")
    mask = os.urandom(4)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def _printable(value):
    if isinstance(value, bytes):
        try:
            text = value.decode()
            if text.isprintable():
                return text
        except UnicodeDecodeError:
            pass
        return value.hex()
    return value


def decode_packet(payload: bytes) -> dict:
    """Decode a WsPacket. The body stays as hex, because its schema depends on cmd."""
    packet = dict(parse(payload))
    data = packet.get(7, b"")
    result = {"module": packet.get(4), "cmd": packet.get(5), "type": packet.get(6), "client": _printable(packet.get(8, b"")), "data": data.hex()}
    try:
        result["fields"] = [[field, _printable(value)] for field, value in parse(data)]
    except (ValueError, IndexError, struct.error):
        pass
    return result


class Log:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.file = path.open("a", buffering=1)

    def write(self, **event) -> None:
        self.file.write(json.dumps({"t": round(time.time(), 3), **event}) + "\n")


class AppLink:
    """The live app connection, with the packet header that the app uses."""

    def __init__(self, conn: int, ws: WsStream, writer):
        self.conn, self.ws, self.writer = conn, ws, writer
        self.header: dict[int, object] | None = None

    def packet(self, module: int, cmd: int, data: bytes) -> bytes:
        h = self.header
        return (field_int(1, h.get(1, 0)) + field_int(2, h.get(2, 0)) + field_int(3, h.get(3, 0))
                + field_int(4, module) + field_int(5, cmd) + field_bytes(7, data) + field_bytes(8, h.get(8, b"")))


class Relay:
    def __init__(self, upstream: str, log: Log, ports=TCP_PORTS):
        self.upstream = upstream
        self.log = log
        self.ports = ports
        self.connection_id = 0
        self.app_link: AppLink | None = None

    def _upstream_port(self, port: int) -> int:
        return port

    async def serve(self, host: str):
        servers = []
        for port in self.ports:
            handler = lambda r, w, p=port: self.handle(p, r, w)
            servers.append(await asyncio.start_server(handler, host, port))
        return servers

    async def handle(self, port: int, reader, writer) -> None:
        self.connection_id += 1
        conn = self.connection_id
        peer = writer.get_extra_info("peername")
        self.log.write(event="open", conn=conn, port=port, peer=str(peer))
        try:
            up_reader, up_writer = await asyncio.open_connection(self.upstream, self._upstream_port(port))
        except OSError as exc:
            self.log.write(event="upstream_failed", conn=conn, port=port, error=str(exc))
            writer.close()
            return
        await asyncio.gather(
            self._pump(conn, port, "app", reader, up_writer),
            self._pump(conn, port, "scope", up_reader, writer),
            return_exceptions=True,
        )

    async def _pump(self, conn: int, port: int, source: str, reader, writer) -> None:
        ws = WsStream() if port == CONTROL_PORT else None
        api = bytearray() if port == API_PORT else None
        link = AppLink(conn, ws, writer) if ws is not None and source == "app" else None
        total = 0
        if link:
            self.app_link = link
        try:
            while chunk := await reader.read(65536):
                # Decode before the write, so ws.buf shows a partial frame at each await.
                if ws is not None:
                    self._log_ws(conn, source, ws, chunk, link)
                if api is not None and len(api) < MAX_API_CAPTURE:
                    api += chunk
                writer.write(rename(chunk) if api is not None and source == "scope" else chunk)
                await writer.drain()
                total += len(chunk)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if link and self.app_link is link:
                self.app_link = None
            if api:
                self.log.write(event="http", conn=conn, source=source, text=redact(bytes(api)).decode("utf-8", "replace"))
            self.log.write(event="close", conn=conn, port=port, source=source, bytes=total)
            writer.close()

    def _log_ws(self, conn: int, source: str, ws: WsStream, chunk: bytes, link: AppLink | None = None) -> None:
        had_handshake = ws.handshake is not None
        try:
            frames = ws.feed(chunk)
        except (ValueError, IndexError) as exc:
            self.log.write(event="ws_decode_error", conn=conn, source=source, error=str(exc))
            return
        if ws.handshake is not None and not had_handshake:
            self.log.write(event="ws_handshake", conn=conn, source=source, text=ws.handshake.decode("latin-1"))
        for opcode, payload in frames:
            if opcode == OPCODE_CLOSE:
                code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
                self.log.write(event="ws_close", conn=conn, source=source, code=code, reason=payload[2:].decode("latin-1"))
            elif opcode == OPCODE_BINARY:
                try:
                    if link:
                        link.header = {k: v for k, v in parse(payload) if k in (1, 2, 3, 8)}
                    self.log.write(event="ws", conn=conn, source=source, **decode_packet(payload))
                except (ValueError, IndexError, struct.error):
                    self.log.write(event="ws_raw", conn=conn, source=source, hex=payload.hex())


    async def inject(self, module: int, cmd: int, data: bytes) -> None:
        link = self.app_link
        if link is None or link.header is None:
            raise RuntimeError("no app connection to carry the command")
        end = time.monotonic() + INJECT_WAIT_S
        while link.ws.buf:
            if time.monotonic() > end:
                raise RuntimeError("app frame still in progress")
            await asyncio.sleep(0.005)
        link.writer.write(client_frame(link.packet(module, cmd, data)))
        await link.writer.drain()
        self.log.write(event="inject", conn=link.conn, module=module, cmd=cmd, data=data.hex())

    async def handle_inject(self, reader, writer) -> None:
        while line := await reader.readline():
            try:
                request = json.loads(line)
                await self.inject(request["module"], request["cmd"], bytes.fromhex(request.get("data", "")))
                reply = {"ok": True}
            except (RuntimeError, ValueError, KeyError) as exc:
                reply = {"ok": False, "error": str(exc)}
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        writer.close()


class InjectClient:
    """Send commands through a relay that runs on this host."""

    def __init__(self):
        self.sock = socket.create_connection(("127.0.0.1", INJECT_PORT), timeout=5)
        self.reader = self.sock.makefile("rb")

    def send(self, module: int, cmd: int, data: bytes = b"") -> None:
        self.sock.sendall(json.dumps({"module": module, "cmd": cmd, "data": data.hex()}).encode() + b"\n")
        reply = json.loads(self.reader.readline())
        if not reply["ok"]:
            raise RuntimeError(reply["error"])

    def close(self) -> None:
        self.sock.close()


class UdpRelay(asyncio.DatagramProtocol):
    """Forward discovery datagrams to the scope, and return replies to each sender."""

    def __init__(self, upstream: str, port: int, log: Log):
        self.upstream, self.port, self.log = upstream, port, log
        self.clients: dict[tuple, asyncio.DatagramTransport] = {}

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.log.write(event="udp", source="app", peer=str(addr), hex=data.hex())
        asyncio.get_running_loop().create_task(self._forward(data, addr))

    async def _forward(self, data: bytes, addr) -> None:
        if addr not in self.clients:
            relay = self

            class Reply(asyncio.DatagramProtocol):
                def datagram_received(self, reply: bytes, _):
                    relay.log.write(event="udp", source="scope", peer=str(addr), hex=reply.hex())
                    relay.transport.sendto(rename(reply), addr)

            self.clients[addr], _ = await asyncio.get_running_loop().create_datagram_endpoint(
                Reply, remote_addr=(self.upstream, self.port)
            )
        self.clients[addr].sendto(data)


def local_ip(upstream: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((upstream, 9))
        return s.getsockname()[0]


async def run(upstream: str, listen: str, log_dir: Path) -> None:
    log = Log(log_dir / f"proxy-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
    relay = Relay(upstream, log)
    await relay.serve(listen)
    await asyncio.start_server(relay.handle_inject, "127.0.0.1", INJECT_PORT)
    await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: UdpRelay(upstream, CONTROL_PORT, log), local_addr=(listen, CONTROL_PORT)
    )
    print(f"relay ready. Enter {local_ip(upstream)} in the app. Log: {log.path}", flush=True)
    await asyncio.Event().wait()
