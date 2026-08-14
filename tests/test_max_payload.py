from app.integrations.max_client import RealMaxClient


def test_real_client_converts_internal_callback_data_to_max_payload():
    attachments = RealMaxClient._attachments([
        [{"text": "📋 Мои каналы", "callback_data": "channels"}],
    ])
    assert attachments == [{
        "type": "inline_keyboard",
        "payload": {"buttons": [[{
            "type": "callback",
            "text": "📋 Мои каналы",
            "payload": "channels",
        }]]},
    }]


def test_real_client_keeps_official_callback_button_format():
    attachments = RealMaxClient._attachments([
        [{"type": "callback", "text": "Назад", "payload": "home"}],
    ])
    assert attachments[0]["payload"]["buttons"][0][0]["payload"] == "home"
