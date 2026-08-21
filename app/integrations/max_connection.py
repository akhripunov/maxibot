"""Resolve the MAX transport used by a channel.

The primary MAXIBOT client and owner-configured custom bots are different
transports.  Keeping the distinction here prevents polling code from
silently treating a primary channel as a missing BotConnection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRIMARY = "PRIMARY"
BOT_CONNECTION = "BOT_CONNECTION"


@dataclass(frozen=True)
class ResolvedMaxConnection:
    kind: str
    client: Any
    channel_id: str


class MaxConnectionUnavailable(RuntimeError):
    pass


def resolve_channel_connection(channel: Any, primary_client: Any,
                                custom_clients: dict[str, Any]) -> ResolvedMaxConnection:
    """Resolve a channel to its configured MAX client.

    Existing rows are migrated to an explicit kind.  The fallback keeps old
    rows readable during rolling deploys: a non-null BotConnection is custom,
    otherwise the configured primary client is used.
    """
    kind = getattr(channel, "max_connection_kind", None)
    bot_id = getattr(channel, "bot_connection_id", None)
    if not kind:
        kind = BOT_CONNECTION if bot_id else PRIMARY
    if kind == PRIMARY:
        if bot_id:
            raise MaxConnectionUnavailable("PRIMARY channel cannot have bot_connection_id")
        if primary_client is None:
            raise MaxConnectionUnavailable("primary MAX client is unavailable")
        return ResolvedMaxConnection(PRIMARY, primary_client, str(channel.id))
    if kind == BOT_CONNECTION:
        if not bot_id:
            raise MaxConnectionUnavailable("BOT_CONNECTION channel has no bot_connection_id")
        client = custom_clients.get(str(bot_id))
        if client is None:
            raise MaxConnectionUnavailable("custom MAX client is unavailable")
        return ResolvedMaxConnection(BOT_CONNECTION, client, str(channel.id))
    raise MaxConnectionUnavailable(f"unsupported MAX connection kind: {kind}")
