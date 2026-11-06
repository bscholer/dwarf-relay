"""WebSocket client for the DWARF 3 control port."""
import struct
import time
import uuid

import websocket

from .wire import field_bytes, field_int, parse

PORT = 9900
TYPE_REQUEST = 0
CLOSE_DEVICE_OCCUPIED = 4409


class DeviceOccupied(RuntimeError):
    """Another client holds the single control connection."""


class ConnectionLost(RuntimeError):
    pass


class Client:
    def __init__(self, host: str, recv_timeout: float = 2.0):
        self.client_id = str(uuid.uuid4())
        self.ws = websocket.create_connection(f"ws://{host}:{PORT}/", timeout=recv_timeout)
        self.last: dict[int, list] = {}
        self._last_ping = 0.0

    def send(self, module: int, cmd: int, data: bytes = b"") -> None:
        self.ws.send_binary(
            field_int(1, 1) + field_int(2, 7) + field_int(3, 2) + field_int(4, module)
            + field_int(5, cmd) + field_int(6, TYPE_REQUEST) + field_bytes(7, data)
            + field_bytes(8, self.client_id.encode())
        )

    def recv(self):
        """Return (cmd, type, fields) for one packet, or None on idle."""
        if time.time() - self._last_ping > 5:
            self.ws.ping()
            self._last_ping = time.time()
        try:
            opcode, payload = self.ws.recv_data(control_frame=True)
        except websocket.WebSocketTimeoutException:
            return None
        except websocket.WebSocketConnectionClosedException as exc:
            raise ConnectionLost("scope closed the connection") from exc
        if opcode == websocket.ABNF.OPCODE_CLOSE:
            code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else None
            if code == CLOSE_DEVICE_OCCUPIED:
                raise DeviceOccupied("another client holds the scope, close the DWARFLAB app")
            raise ConnectionLost(f"scope closed the connection, code {code}")
        if opcode != websocket.ABNF.OPCODE_BINARY:
            return None
        packet = dict(parse(payload))
        fields = parse(packet.get(7, b""))
        cmd = packet.get(5)
        self.last[cmd] = fields
        return cmd, packet.get(6), fields

    def call(self, module: int, cmd: int, data: bytes = b"", timeout: float = 20.0):
        """Send a request and return the fields of its reply, or None on timeout."""
        self.send(module, cmd, data)
        end = time.time() + timeout
        while time.time() < end:
            packet = self.recv()
            if packet and packet[0] == cmd and packet[1] != TYPE_REQUEST:
                return packet[2]
        return None

    def close(self) -> None:
        self.ws.close()
