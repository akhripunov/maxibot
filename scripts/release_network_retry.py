"""Bounded recovery for idempotent release-network reads."""

from __future__ import annotations

import random
import math
import os
from pathlib import Path
import errno
import socket
import ssl
import time
import urllib.error
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")
NETWORK_RETRY_BUDGET_SECONDS = 30 * 60
NETWORK_BACKOFF_CAP_SECONDS = 60
RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
RETRYABLE_ERRNOS = {
    errno.ECONNABORTED, errno.ECONNRESET, errno.EPIPE, errno.ETIMEDOUT,
    errno.EHOSTUNREACH, errno.ENETUNREACH,
}


def is_retryable_network_error(error: BaseException) -> bool:
    """Classify transient failures for idempotent network reads only."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in RETRYABLE_HTTP_STATUS
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return False
    if isinstance(reason, ssl.SSLError):
        message = str(reason).lower()
        if any(marker in message for marker in (
            "certificate_verify_failed", "certificate verify failed",
            "wrong_version_number", "protocol_version", "unsupported protocol",
        )):
            return False
        if isinstance(reason, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
            return True
        syscall_error = getattr(ssl, "SSLSyscallError", ())
        return bool(syscall_error and isinstance(reason, syscall_error)) or any(
            marker in message for marker in (
                "ssl_error_syscall", "unexpected_eof", "eof occurred", "timed out", "timeout",
                "connection reset", "tlsv1_alert_internal_error",
            ))
    if isinstance(reason, socket.gaierror):
        return reason.errno == getattr(socket, "EAI_AGAIN", None)
    if isinstance(reason, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    return isinstance(reason, OSError) and reason.errno in RETRYABLE_ERRNOS


class NetworkUnavailable(RuntimeError):
    """The safe read remained unavailable through its bounded retry window."""


class RetryBudget:
    def __init__(self, seconds: float = NETWORK_RETRY_BUDGET_SECONDS,
                 clock: Callable[[], float] | None = None,
                 state_path: str | None = None) -> None:
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.seconds = seconds
        configured_path = state_path or os.environ.get("NETWORK_RETRY_BUDGET_STATE")
        self.state_path = Path(configured_path) if configured_path else None
        self.spent = 0.0
        if self.state_path and self.state_path.exists():
            try:
                self.spent = max(0.0, float(self.state_path.read_text(encoding="utf-8").strip()))
            except (OSError, ValueError):
                raise RuntimeError("Invalid shared network retry budget state") from None
        self.deadline = self.started + max(0.0, seconds - self.spent)

    def persist(self, elapsed: float) -> None:
        if self.state_path:
            self.spent += max(0.0, elapsed)
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(f"{math.ceil(self.spent)}\n", encoding="utf-8")
            temporary.replace(self.state_path)


def retry_network_read(operation: Callable[[], T], stage: str,
                       is_retryable: Callable[[BaseException], bool], *,
                       budget: RetryBudget | None = None,
                       clock: Callable[[], float] | None = None,
                       sleep: Callable[[float], None] | None = None,
                       jitter: Callable[[float, float], float] | None = None,
                       emit: Callable[[str], None] = print) -> T:
    """Retry only a caller-supplied idempotent read, bounded by one deadline."""
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    jitter = jitter or random.uniform
    budget = budget or RetryBudget(clock=clock)
    call_started = clock()
    retries = 0
    attempts = 0

    def unavailable() -> NetworkUnavailable:
        elapsed = budget.spent + clock() - call_started
        emit(f"NETWORK_UNAVAILABLE stage={stage} elapsed_s={elapsed:.1f} budget_s={budget.seconds}")
        return NetworkUnavailable(stage)

    retries = 0
    deadline = clock() + max(0.0, budget.seconds - budget.spent)
    try:
      while True:
        if clock() >= deadline:
            raise unavailable()
        try:
            attempts += 1
            result = operation()
            elapsed = budget.spent + clock() - call_started
            if clock() >= deadline:
                raise unavailable()
            if retries:
                emit(f"NETWORK_RESTORED stage={stage} elapsed_s={elapsed:.1f}")
            emit(f"RELEASE_CONTINUING stage={stage}")
            return result
        except Exception as error:
            if not is_retryable(error):
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                raise unavailable() from None
            cap = min(NETWORK_BACKOFF_CAP_SECONDS, 2 ** min(retries, 6))
            delay = min(max(0.0, jitter(0.0, cap)), remaining)
            emit(f"NETWORK_RETRYING stage={stage} retry_in_s={delay:.1f} elapsed_s={budget.spent + clock()-call_started:.1f}")
            retries += 1
            sleep(delay)
    finally:
        if attempts:
            budget.persist(clock() - call_started)
