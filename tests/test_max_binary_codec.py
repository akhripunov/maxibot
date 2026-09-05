import pytest

from app.integrations.max_binary_codec import MaxBinaryCodec, MaxBinaryCodecError




def _synthetic_pending_response() -> bytes:
    """Build a sanitized response shape without retaining a private capture."""
    name = b"QA synthetic member".ljust(31, b" ")
    body = (
        b"\x81\xa7members\x91\x81\xa7contact\x83"
        b"\xa2id\xc7\x05\x01\xd2" + (987).to_bytes(4, "big", signed=True)
        + b"\xa5names\xbf" + name
    )
    return b"\x0a\x00\x00\x1e\x00;\x01\x00\x00\x00\x00\x01" + body


def test_decode_known_pending_and_approve_fixtures():
    codec = MaxBinaryCodec()
    pending = codec.decode_message(codec.encode_pending_request(request_value=7, chat_id=-123))
    approve = codec.decode_message(codec.encode_approve_request(request_value=8, chat_id=-123, user_id=987))
    assert pending.opcode == 59
    assert pending.payload["chatId"] == approve.payload["chatId"]
    assert approve.opcode == 77
    assert len(approve.payload["userIds"]) == 1


def test_decode_known_pending_response_identity():
    message = MaxBinaryCodec().decode_message(_synthetic_pending_response())
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
    assert frame != codec.encode_approve_request(request_value=0x123455, chat_id=-123, user_id=987)


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
