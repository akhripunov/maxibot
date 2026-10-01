"""Bounded recovery for idempotent release-network reads."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")
NETWORK_RETRY_BUDGET_SECONDS = 30 * 60
NETWORK_BACKOFF_CAP_SECONDS = 60


class NetworkUnavailable(RuntimeError):
    """The safe read remained unavailable through its bounded retry window."""


class RetryBudget:
    def __init__(self, seconds: float = NETWORK_RETRY_BUDGET_SECONDS,
                 clock: Callable[[], float] | None = None) -> None:
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.deadline = self.started + seconds
        self.seconds = seconds


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
    retries = 0

    def unavailable() -> NetworkUnavailable:
        elapsed = clock() - budget.started
        emit(f"NETWORK_UNAVAILABLE stage={stage} elapsed_s={elapsed:.1f} budget_s={budget.seconds}")
        return NetworkUnavailable(stage)

    while True:
        if clock() >= budget.deadline:
            raise unavailable()
        try:
            result = operation()
            elapsed = clock() - budget.started
            if clock() >= budget.deadline:
                raise unavailable()
            if retries:
                emit(f"NETWORK_RESTORED stage={stage} elapsed_s={elapsed:.1f}")
            emit(f"RELEASE_CONTINUING stage={stage}")
            return result
        except Exception as error:
            if not is_retryable(error):
                raise
            remaining = budget.deadline - clock()
            if remaining <= 0:
                raise unavailable() from None
            cap = min(NETWORK_BACKOFF_CAP_SECONDS, 2 ** min(retries, 6))
            delay = min(max(0.0, jitter(0.0, cap)), remaining)
            emit(f"NETWORK_RETRYING stage={stage} retry_in_s={delay:.1f} elapsed_s={clock()-budget.started:.1f}")
            retries += 1
            sleep(delay)
