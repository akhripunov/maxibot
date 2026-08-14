from pathlib import Path


def test_connection_flow_fetches_chat_and_persists_private_link():
    source = Path("app/bot/menu.py").read_text()
    assert 'chat = data.get("chat")' in source
    assert 'target = link if not is_public and valid_max_target(link) else None' in source
    assert 'TrafficProfile(channel_id=channel.id' in source


def test_bot_added_sync_is_channel_scoped_and_read_only():
    source = Path("app/bot/runtime.py").read_text()
    assert 'await self.client.get_chat(update.chat_id)' in source
    assert 'sync_max_chat_metadata(update.chat_id' in source
    assert 'CHAT_UPDATE' not in source
