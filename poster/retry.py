"""Retries with back-off, bounded by a per-request time budget."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

from poster.config import API
from poster.errors import ServiceError

log = logging.getLogger("poster")

T = TypeVar("T")
RetryCallback = Callable[[int, int, ServiceError], None]

# Seconds an attempt needs to be worth starting; with less budget left, the last error stands.
MIN_ATTEMPT_SECONDS = 5

_sleep: Callable[[float], None] = time.sleep  # replaced in tests
_deadline: ContextVar[float | None] = ContextVar("poster_deadline", default=None)


@contextmanager
def time_budget(seconds: int) -> Iterator[None]:
    """Upstream calls made inside stop retrying, and shorten their timeouts, so that
    together they end within ``seconds`` (0: no limit)."""
    token = _deadline.set(time.monotonic() + seconds if seconds > 0 else None)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_budget() -> float | None:
    """Seconds left in the current request's budget, or None without one."""
    deadline = _deadline.get()
    return None if deadline is None else deadline - time.monotonic()


def worst_case_seconds(timeout: int) -> int:
    """Longest one retried call can take: every attempt times out, plus the waits between them."""
    retry = API["retry"]
    return int(timeout * (retry["max_retries"] + 1) + sum(retry["delays"]))


def browser_timeout(seconds: int, budget: int) -> int:
    """How long the browser should wait for a request the server may work on for ``seconds``."""
    return min(seconds, budget + 15) if budget else seconds


def retry_request(operation: Callable[[], T], *, should_retry: Callable[[ServiceError], bool] | None = None,
                  max_retries: int = API["retry"]["max_retries"], delays: tuple[int, ...] = API["retry"]["delays"],
                  on_retry: RetryCallback | None = None, sleep: Callable[[float], None] | None = None) -> T:
    """Runs ``operation`` and retries transient failures.

    Retries timeouts, temporary network failures and HTTP 429/500/502/503/504
    up to 3 times, waiting 1 s, 2 s and 4 s, while the request's time budget
    allows another attempt. Never retries 400/401/403 or validation errors.
    """
    check = should_retry or (lambda error: error.retryable)
    attempt = 0
    while True:
        try:
            return operation()
        except ServiceError as error:
            if attempt >= max_retries or not check(error):
                raise
            delay = delays[min(attempt, len(delays) - 1)]
            left = remaining_budget()
            if left is not None and left < delay + MIN_ATTEMPT_SECONDS:
                log.warning("Not retrying %s: %.0fs left of this request's time budget", error.service or "the call", left)
                raise
            attempt += 1
            if on_retry is not None:  # RetryLog is a list, so an empty one is falsy
                on_retry(attempt, delay, error)
            (sleep or _sleep)(delay)


class RetryLog(list[dict[str, Any]]):
    """Collects retry events so the browser can show them in its activity log."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def __call__(self, attempt: int, delay: int, error: ServiceError) -> None:
        max_retries = API["retry"]["max_retries"]
        self.append({"attempt": attempt, "maxRetries": max_retries, "delayMs": delay * 1000, "message": error.message})
        log.warning("%s: retry %d/%d in %ds (%s)", self.label, attempt, max_retries, delay, error.message)
