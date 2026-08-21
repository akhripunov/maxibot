from app.bot.runtime import MaxBotRuntime
from app.integrations.max_client import MaxMessage, MaxUpdate


def test_bot_started_payload_is_read_only_from_payload():
    update = MaxUpdate(update_type="bot_started", user_id="u1", raw={"payload": "peterburg-24"})
    assert MaxBotRuntime._start_payload(update) == "peterburg-24"


def test_bot_started_payload_accepts_production_wrapper_objects():
    update = MaxUpdate(
        update_type="bot_started",
        user_id="u1",
        raw={"payload": {"data": {"start": "opaque-visitor-token"}}},
    )
    assert MaxBotRuntime._start_payload(update) == "opaque-visitor-token"


def test_missing_payload_is_none():
    update = MaxUpdate(update_type="bot_started", user_id="u1", raw={})
    assert MaxBotRuntime._start_payload(update) is None


def test_local_start_query_is_not_interpreted_as_real_payload():
    update = MaxUpdate(update_type="bot_started", user_id="u1", raw={"start": "https://max.ru/bot?channel=peterburg-24"})
    assert MaxBotRuntime._start_payload(update) is None


def test_forwarded_message_is_read_from_message_link():
    from app.integrations.max_client import MaxMessage
    message = MaxMessage("m1", "u1", "User", "post", raw={"link": {"type": "forward", "chat_id": 12345}})
    assert MaxBotRuntime._forwarded_channel_id(message) == "12345"


def test_unknown_payload_remains_distinguishable_from_missing_payload():
    known = MaxUpdate(update_type="bot_started", user_id="u1", raw={"payload": "removed-slug"})
    missing = MaxUpdate(update_type="bot_started", user_id="u1", raw={})
    assert MaxBotRuntime._start_payload(known) == "removed-slug"
    assert MaxBotRuntime._start_payload(missing) is None


def test_startapp_alias_is_supported_for_traffic_poc():
    update = MaxUpdate(update_type="bot_started", user_id="u1", raw={"startapp": "opaque-visitor-token"})
    assert MaxBotRuntime._start_payload(update) == "opaque-visitor-token"


def test_ios_universal_link_payload_extracts_start_query():
    update = MaxUpdate(
        update_type="bot_started",
        user_id="u1",
        raw={"payload": "https://max.ru/maxibot?start=opaque-ios-token"},
    )
    assert MaxBotRuntime._start_payload(update) == "opaque-ios-token"


def test_ios_query_shaped_payload_extracts_startapp_query():
    update = MaxUpdate(update_type="bot_started", user_id="u1", raw={"payload": "startapp=opaque-ios-token"})
    assert MaxBotRuntime._start_payload(update) == "opaque-ios-token"


def test_start_payload_candidates_keep_current_message_start_after_wrapper_value():
    update = MaxUpdate(
        update_type="bot_started",
        user_id="u1",
        raw={"payload": "legacy-channel-slug"},
        message=MaxMessage("m1", "u1", "User", "/start current-opaque-token"),
    )
    assert MaxBotRuntime._start_payload_candidates(update) == [
        "legacy-channel-slug",
        "current-opaque-token",
    ]
