"""Conservative parser for the currently known MAX WebSocket envelope.

This module contains no captures or credentials.  It decodes the structural
parts proven by local fixtures and can encode the observed payload shape.  The
envelope correlation field is intentionally exposed, not inferred: existing
captures do not prove that the response header echoes the request field.
Callers must inject a live-validated correlation strategy before enabling this
codec for destructive traffic.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Any


class MaxBinaryCodecError(ValueError):
    pass


@dataclass(frozen=True)
class MaxBinaryMessage:
    version: int
    direction: int
    envelope_value: int
    opcode: int
    phase: int
    request_value: int
    payload: dict[str, Any]


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, size: int) -> bytes:
        end = self.pos + size
        if end > len(self.data):
            raise MaxBinaryCodecError("truncated MAX payload")
        value = self.data[self.pos:end]
        self.pos = end
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def integer(self, size: int, signed: bool = False) -> int:
        return int.from_bytes(self.take(size), "big", signed=signed)

    def value(self) -> Any:
        marker = self.u8()
        if marker <= 0x7f:
            return marker
        if 0xa0 <= marker <= 0xbf:
            return self.take(marker & 0x1f).decode("utf-8")
        if 0x90 <= marker <= 0x9f:
            return [self.value() for _ in range(marker & 0x0f)]
        if 0x80 <= marker <= 0x8f:
            return {self.value(): self.value() for _ in range(marker & 0x0f)}
        if marker == 0xc0:
            return None
        if marker == 0xc2:
            return False
        if marker == 0xc3:
            return True
        if marker == 0xcc:
            return self.integer(1)
        if marker == 0xcd:
            return self.integer(2)
        if marker == 0xce:
            return self.integer(4)
        if marker == 0xcf:
            return self.integer(8)
        if marker == 0xd0:
            return self.integer(1, True)
        if marker == 0xd1:
            return self.integer(2, True)
        if marker == 0xd2:
            return self.integer(4, True)
        if marker == 0xd3:
            return self.integer(8, True)
        if marker in (0xd9, 0xda, 0xdb):
            length = self.integer({0xd9: 1, 0xda: 2, 0xdb: 4}[marker])
            return self.take(length).decode("utf-8")
        if marker in (0xdc, 0xdd):
            length = self.integer(2 if marker == 0xdc else 4)
            return [self.value() for _ in range(length)]
        if marker in (0xde, 0xdf):
            length = self.integer(2 if marker == 0xde else 4)
            return {self.value(): self.value() for _ in range(length)}
        if marker == 0xc7:
            length = self.integer(1)
            ext_type = self.u8()
            body = self.take(length)
            # Known captures use ext type 1, containing a numeric marker and
            # an 8-byte integer.  Preserve unknown ext values as bytes.
            if ext_type == 1 and len(body) in (5, 9) and body[0] in (0xd2, 0xcf, 0xd3):
                return int.from_bytes(body[1:], "big", signed=body[0] in (0xd2, 0xd3))
            return {"ext_type": ext_type, "data": body}
        raise MaxBinaryCodecError(f"unsupported MAX MessagePack marker 0x{marker:02x}")


def _pack_string(value: str) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) < 32:
        return bytes([0xa0 | len(raw)]) + raw
    if len(raw) <= 255:
        return b"\xd9" + bytes([len(raw)]) + raw
    raise MaxBinaryCodecError("string is too long for proven fixture encoder")


def _pack_value(value: Any) -> bytes:
    if isinstance(value, str):
        return _pack_string(value)
    if value is True:
        return b"\xc3"
    if value is False:
        return b"\xc2"
    if value is None:
        return b"\xc0"
    if isinstance(value, int):
        if 0 <= value <= 0x7f:
            return bytes([value])
        if 0 <= value <= 0xff:
            return b"\xcc" + struct.pack(">B", value)
        if value >= 0:
            return b"\xcf" + struct.pack(">Q", value)
        return b"\xd3" + struct.pack(">q", value)
    if isinstance(value, list):
        if len(value) > 15:
            raise MaxBinaryCodecError("array is outside proven fixture shape")
        return bytes([0x90 | len(value)]) + b"".join(_pack_value(v) for v in value)
    if isinstance(value, dict):
        if len(value) > 15:
            raise MaxBinaryCodecError("map is outside proven fixture shape")
        return bytes([0x80 | len(value)]) + b"".join(_pack_value(k) + _pack_value(v) for k, v in value.items())
    raise MaxBinaryCodecError(f"unsupported value type: {type(value).__name__}")


def _pack_max_id(value: int, *, signed: bool) -> bytes:
    marker = b"\xd3" if signed else b"\xcf"
    number = struct.pack(">q" if signed else ">Q", value)
    return b"\xc7\x09\x01" + marker + number


class MaxBinaryCodec:
    """Decode known fixtures and construct the observed request payload shape.

    `request_value` is numeric because that is what the wire capture exposes;
    callers must supply a freshly generated value.  The method-specific
    envelope constants are fixture-derived and remain behind this module.
    """

    _ENVELOPE_VALUE = {59: 0x1e, 77: 0x24}

    def decode_message(self, data: bytes) -> MaxBinaryMessage:
        if len(data) < 13 or data[0] != 0x0a:
            raise MaxBinaryCodecError("invalid MAX envelope")
        payload_reader = _Reader(data[12:])
        try:
            payload = payload_reader.value()
        except (MaxBinaryCodecError, UnicodeDecodeError):
            # Some contact profile fields use an undocumented MAX custom
            # wrapper (observed as c7 be 41).  Do not guess its whole schema;
            # recover only the identity fields proven by the pending fixture.
            payload = self._decode_pending_identity_subset(data[12:])
        if not isinstance(payload, dict):
            raise MaxBinaryCodecError("MAX payload is not a map")
        return MaxBinaryMessage(data[0], data[1], int.from_bytes(data[2:4], "big"),
                                int.from_bytes(data[4:6], "big"), data[6],
                                int.from_bytes(data[9:12], "big"), payload)

    @staticmethod
    def _decode_pending_identity_subset(data: bytes) -> dict[str, Any]:
        id_marker = b"id\xc7\x05\x01\xd2"
        id_pos = data.find(id_marker)
        if id_pos < 0 or id_pos + len(id_marker) + 4 > len(data):
            raise MaxBinaryCodecError("unsupported MAX response custom field")
        user_id = int.from_bytes(data[id_pos + len(id_marker):id_pos + len(id_marker) + 4], "big", signed=True)
        names_pos = data.find(b"names", id_pos)
        if names_pos < 0:
            raise MaxBinaryCodecError("pending response names are missing")
        name_marker = data.find(b"\xbf", names_pos)
        if name_marker < 0 or name_marker + 1 + 31 > len(data):
            raise MaxBinaryCodecError("pending response name field is unsupported")
        name = data[name_marker + 1:name_marker + 32].decode("utf-8")
        return {"members": [{"contact": {"id": user_id, "names": [{"name": name}]}}]}

    def encode_request(self, *, request_value: int, opcode: int,
                       payload: dict[str, Any], response_phase: int = 1) -> bytes:
        if opcode not in self._ENVELOPE_VALUE:
            raise MaxBinaryCodecError(f"unsupported MAX opcode: {opcode}")
        if not 0 <= request_value <= 0xffffff:
            raise MaxBinaryCodecError("request_value must fit uint24")
        body = _pack_value(payload)
        return (b"\x0a\x00\x00" + self._ENVELOPE_VALUE[opcode].to_bytes(1, "big")
                + opcode.to_bytes(2, "big") + bytes([response_phase, 0, 0])
                + request_value.to_bytes(3, "big") + body)

    def encode_pending_request(self, *, request_value: int, chat_id: int) -> bytes:
        return self.encode_request(request_value=request_value, opcode=59,
                                   payload={"query": "", "type": "JOIN_REQUEST",
                                            "chatId": _MaxId(chat_id, True), "count": 2})

    def encode_approve_request(self, *, request_value: int, chat_id: int,
                               user_id: int) -> bytes:
        return self.encode_request(request_value=request_value, opcode=77,
                                   payload={"chatId": _MaxId(chat_id, True),
                                            "userIds": [_MaxId(user_id, False)],
                                            "showHistory": True, "operation": "add",
                                            "type": "JOIN_REQUEST"})


class _MaxId:
    """Internal marker selecting MAX's observed bigint ext representation."""
    def __init__(self, value: int, signed: bool):
        self.value, self.signed = value, signed


# Keep marker handling local to the encoder without exposing a second payload
# type to callers.
_pack_value_original = _pack_value
def _pack_value(value: Any) -> bytes:  # type: ignore[no-redef]
    if isinstance(value, _MaxId):
        return _pack_max_id(value.value, signed=value.signed)
    return _pack_value_original(value)
