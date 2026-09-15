"""Strict RU↔EU Telegram executor protocol (version 1).

This module intentionally has no database imports.  The wire contract is
small, typed and read-only; arbitrary URLs and provider write operations are
not representable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROTOCOL_VERSION = 1
READ_OPERATIONS = frozenset({"ACCOUNT_INVENTORY", "CAMPAIGN_PERFORMANCE"})


class ExecutionOperation(str, Enum):
    ACCOUNT_INVENTORY = "ACCOUNT_INVENTORY"
    CAMPAIGN_PERFORMANCE = "CAMPAIGN_PERFORMANCE"


class TelegramExecutionRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: int = Field(default=PROTOCOL_VERSION)
    request_id: UUID
    sent_at: datetime
    account_id: UUID
    identity_ref: UUID
    owner_identifier: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    operation: ExecutionOperation
    slot_key: str = Field(min_length=1, max_length=255)
    campaign_external_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[0-9]+$")
    date_from: str | None = Field(default=None, max_length=32)
    date_to: str | None = Field(default=None, max_length=32)

    @field_validator("sent_at")
    @classmethod
    def timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("sent_at must be timezone-aware")
        return value

    def assert_supported(self, *, now: datetime | None = None, max_age_seconds: int = 300) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("PROTOCOL_VERSION_MISMATCH")
        if self.operation not in READ_OPERATIONS:
            raise ValueError("INVALID_REQUEST")
        current = now or datetime.now(timezone.utc)
        age = (current - self.sent_at.astimezone(timezone.utc)).total_seconds()
        if age < -30 or age > max_age_seconds:
            raise ValueError("REPLAY_REJECTED")
        if self.operation == ExecutionOperation.CAMPAIGN_PERFORMANCE and not self.campaign_external_id:
            raise ValueError("INVALID_REQUEST")


class StructuredExecutionError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    retryable: bool = False
    provider_status_if_safe: str | None = None
    message_sanitized: str


class TelegramExecutionResponseV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: int = PROTOCOL_VERSION
    request_id: UUID
    account_id: UUID
    operation: ExecutionOperation
    executor_region: str
    executor_version_sha: str
    started_at: datetime
    finished_at: datetime
    status: str
    owner_assertion: str
    normalized_result: dict[str, Any] | None = None
    error: StructuredExecutionError | None = None

