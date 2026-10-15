"""ServiceError, the one exception type the API turns into a JSON error."""

from __future__ import annotations

from typing import Any

from poster.config import API


class ServiceError(Exception):
    """A classified failure, rendered to the browser as JSON.

    ``kind`` is one of: validation, config, auth, rate_limit, timeout, network,
    server, client, not_found, conflict, parse. ``uncertain`` marks a failed
    request that may still have taken effect upstream (a post may exist).
    """

    def __init__(self, message: str, *, kind: str = "server", status: int = 0, service: str = "",
                 hint: str = "", details: str = "", uncertain: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status = status
        self.service = service
        self.hint = hint
        self.details = details
        self.uncertain = uncertain
        self.retries: list[dict[str, Any]] = []

    @property
    def retryable(self) -> bool:
        return self.kind in API["retry"]["retryable_kinds"] or self.status in API["retry"]["retryable_status"]

    @property
    def http_status(self) -> int:
        return {"validation": 400, "config": 503, "conflict": 409, "rate_limit": 429, "timeout": 504}.get(self.kind, 502)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message, "kind": self.kind, "status": self.status, "service": self.service,
            "hint": self.hint, "details": self.details, "uncertain": self.uncertain, "retries": self.retries,
        }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def extract_error_detail(data: Any) -> str:
    """Pulls a readable message out of the common error body shapes."""
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict) and (message := _text(error.get("message"))):
        return message
    if message := _text(data.get("message")):
        return message
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict) and (message := _text(first.get("message"))):
            return message
    for key in ("detail", "error_description"):
        if message := _text(data.get(key)):
            return message
    return ""


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
