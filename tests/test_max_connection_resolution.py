from types import SimpleNamespace

import pytest

from app.integrations.max_connection import (
    BOT_CONNECTION,
    PRIMARY,
    MaxConnectionUnavailable,
    resolve_channel_connection,
)


def channel(**kwargs):
    values = {"id": "channel-1", "bot_connection_id": None,
              "max_connection_kind": PRIMARY}
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_primary_channel_resolves_to_configured_primary_client():
    client = object()
    resolved = resolve_channel_connection(channel(), client, {})
    assert resolved.kind == PRIMARY
    assert resolved.client is client


def test_custom_channel_resolves_to_active_custom_client():
    client = object()
    resolved = resolve_channel_connection(
        channel(bot_connection_id="bot-1", max_connection_kind=BOT_CONNECTION),
        object(), {"bot-1": client},
    )
    assert resolved.kind == BOT_CONNECTION
    assert resolved.client is client


@pytest.mark.parametrize("row", [
    channel(max_connection_kind=PRIMARY, bot_connection_id="bot-1"),
    channel(max_connection_kind=BOT_CONNECTION, bot_connection_id=None),
    channel(max_connection_kind="UNKNOWN"),
])
def test_invalid_or_missing_connection_is_explicit(row):
    with pytest.raises(MaxConnectionUnavailable):
        resolve_channel_connection(row, object(), {})


def test_legacy_rows_are_resolved_without_new_column():
    legacy = SimpleNamespace(id="legacy", bot_connection_id=None)
    resolved = resolve_channel_connection(legacy, "primary", {})
    assert resolved.kind == PRIMARY


def test_duplicate_resolution_is_deterministic():
    row = channel()
    first = resolve_channel_connection(row, "primary", {})
    second = resolve_channel_connection(row, "primary", {})
    assert (first.kind, first.channel_id) == (second.kind, second.channel_id)
