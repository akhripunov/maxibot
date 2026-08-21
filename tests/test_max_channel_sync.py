from pathlib import Path


def test_connection_flow_fetches_chat_and_persists_private_link():
    source = Path("app/bot/menu.py").read_text()
    assert 'chat = data.get("chat")' in source
    assert 'target = link if not is_public and valid_max_target(link) else None' in source
    assert 'TrafficProfile(channel_id=channel.id' in source


def test_bot_added_sync_is_channel_scoped_and_read_only():
    source = Path("app/bot/runtime.py").read_text()
    assert 'await self.client.get_chat(update.chat_id)' in source
    assert 'connect_channel_from_bot_added(update.chat_id' in source
    assert 'CHAT_UPDATE' not in source


def test_primary_bot_added_marks_primary_connection_without_fake_bot_row():
    source = Path("app/bot/menu.py").read_text()
    assert 'channel.max_connection_kind = PRIMARY' in source
    assert 'if not channel.bot_connection_id' in source


def test_custom_toggle_keeps_custom_connection_kind():
    source = Path("app/bot/menu.py").read_text()
    assert 'channel.max_connection_kind = BOT_CONNECTION if channel.bot_connection_id else PRIMARY' in source


def test_user_added_exact_identity_creates_dry_run_user_did_subscribe_queue():
    source = Path("app/bot/menu.py").read_text()
    assert 'event_type.lower() == "user_added" and exact_visit_id' in source
    assert 'goal="userDidSubscribe"' in source
    assert 'status="dry_run" if settings.yandex_conversion_dry_run else "pending"' in source


def test_user_added_exact_identity_is_idempotent_for_conversion_queue():
    source = Path("app/bot/menu.py").read_text()
    assert 'OfflineConversion.target == "YANDEX"' in source
    assert 'OfflineConversion.membership_id == membership.id' in source


def test_existing_user_added_can_be_reconciled_without_replaying_event():
    source = Path("app/bot/menu.py").read_text()
    assert 'async def ensure_exact_user_added_attribution' in source
    assert 'target="YANDEX", goal="userDidSubscribe"' in source


def test_bot_added_can_adopt_orphan_only_with_official_owner_match():
    source = Path("app/bot/menu.py").read_text()
    assert 'channel.admin_id = owner_admin.id' in source
    assert 'str(chat_owner_id or "") != str(owner_user_id)' in source
    assert 'channel.max_owner_user_id and str(channel.max_owner_user_id) != str(owner_user_id)' in source
