from app.integrations.max_client import RealMaxClient


def test_message_sender_is_used_when_top_level_ids_are_zero():
    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    update = client._parse_update({
        "update_type": "message_created",
        "chat_id": 0,
        "message": {
            "sender": {"user_id": 123, "name": "Анна"},
            "recipient": {"user_id": 999},
            "body": {"text": "/start"},
        },
    })
    assert update.user_id == "123"
    assert update.chat_id is None
    assert update.message.text == "/start"


def test_message_text_alias_is_supported():
    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    update = client._parse_update({
        "update_type": "message_created",
        "message": {
            "sender": {"id": 456, "name": "Иван"},
            "body": "/start",
        },
    })
    assert update.user_id == "456"
    assert update.message.text == "/start"


def test_message_sender_id_alias_is_supported():
    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    update = client._parse_update({
        "update_type": "message_created",
        "message": {
            "sender": {"id": 456, "name": "Иван"},
            "body": {"text": "/start"},
        },
    })
    assert update.user_id == "456"


def test_callback_sender_is_used_when_callback_chat_id_is_zero():
    client = RealMaxClient(token="token", base_url="https://platform-api2.max.ru")
    update = client._parse_update({
        "update_type": "message_callback",
        "chat_id": 0,
        "callback": {"user": {"user_id": 456, "name": "Иван"}, "payload": "channels"},
    })
    assert update.user_id == "456"
    assert update.chat_id is None
    assert update.button_payload == "channels"
