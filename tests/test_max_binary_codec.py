from pathlib import Path

import pytest

from app.integrations.max_binary_codec import MaxBinaryCodec, MaxBinaryCodecError


PRIVATE = Path("research/max_ws_reproduction/private")


def test_decode_known_pending_and_approve_fixtures():
    codec = MaxBinaryCodec()
    pending = codec.decode_message((PRIVATE / "pending_request.bin").read_bytes())
    approve = codec.decode_message((PRIVATE / "approve_frame_template.bin").read_bytes())
    assert pending.opcode == 59
    assert pending.payload["chatId"] == approve.payload["chatId"]
    assert approve.opcode == 77
    assert len(approve.payload["userIds"]) == 1


def test_decode_known_pending_response_identity():
    message = MaxBinaryCodec().decode_message((PRIVATE / "pending_response_latest.bin").read_bytes())
    member = message.payload["members"][0]
    assert message.opcode == 59
    assert member["contact"]["id"] > 0
    assert member["contact"]["names"][0]["name"]


def test_dynamic_encode_replaces_runtime_ids_and_request_value():
    codec = MaxBinaryCodec()
    frame = codec.encode_approve_request(request_value=0x123456, chat_id=-123, user_id=987)
    decoded = codec.decode_message(frame)
    assert decoded.request_value == 0x123456
    assert decoded.payload["chatId"] == -123
    assert decoded.payload["userIds"] == [987]
    assert frame != (PRIVATE / "approve_frame_template.bin").read_bytes()


def test_dynamic_pending_encode_and_single_user_guard_shape():
    message = MaxBinaryCodec().decode_message(
        MaxBinaryCodec().encode_pending_request(request_value=7, chat_id=-123)
    )
    assert message.opcode == 59
    assert message.payload["chatId"] == -123
    assert message.payload["count"] == 2


def test_unknown_value_and_truncated_frame_fail_closed():
    codec = MaxBinaryCodec()
    with pytest.raises(MaxBinaryCodecError):
        codec.encode_request(request_value=1, opcode=77, payload={"x": object()})
    with pytest.raises(MaxBinaryCodecError):
        codec.decode_message(b"\x0a\x00")
