"""Configuration mapping for system conversion goals."""
from __future__ import annotations

from app.core.config import settings


def system_conversion_goal(internal_event: str) -> str:
    mapping: dict[str, str] = {}
    for item in (settings.system_conversion_goal_map or "").split(","):
        key, separator, value = item.partition("=")
        if separator and key.strip() and value.strip():
            mapping[key.strip()] = value.strip()
    return mapping.get(internal_event, internal_event)
