"""ActivityLog: what the dashboard and the publisher did, kept in the database next to the post queue.

The dashboard records its entries in the browser and saves them here in batches. The publisher, which runs
with no browser open, records what it published, what it will try again and what it gave up on.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from poster.config import API
from poster.errors import ServiceError, truncate
from poster.store import PostStore, Row

log = logging.getLogger("poster")


def stamp(value: datetime) -> str:
    """UTC to the millisecond, with a Z suffix (as JavaScript's toISOString writes it): every entry's time."""
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _rules() -> dict[str, Any]:
    rules: dict[str, Any] = API["activity"]
    return rules


class ActivityLog:
    """The activity log's entries in the database, newest first."""

    def __init__(self, store: PostStore) -> None:
        self.store = store

    def save(self, entries: list[Row]) -> dict[str, Any]:
        """Stores entries the dashboard recorded (see validation.parse_activity_entries)."""
        return {"saved": self.store.add_activity(entries, _rules()["keep"])}

    def record(self, level: str, category: str, message: str, details: str | None = None) -> None:
        """Adds an entry from the server itself. A database failure is only logged: recording must never stop
        the work it records."""
        rules = _rules()
        entry = {"id": f"srv-{uuid.uuid4().hex}", "ts": stamp(datetime.now(UTC)), "level": level, "category": category,
                 "message": truncate(message, rules["max_message_chars"]),
                 "details": truncate(details, rules["max_details_chars"]) if details else None}
        try:
            self.store.add_activity([entry], rules["keep"])
        except ServiceError as error:
            log.warning("Could not record activity (%s): %s", error.message, message)

    def page(self, name: str, after: int | None) -> dict[str, Any]:
        """One page of the entries ``name`` (one of API["activity"]["filters"]) selects. ``after`` is the
        previous page's ``next``."""
        rules = _rules()
        size = rules["page_size"]
        offset = after or 0
        chosen = rules["filters"][name] or {}
        rows = self.store.activity_page(chosen.get("levels", []), chosen.get("categories", []), offset, size + 1)
        return {"entries": [self.summary(row) for row in rows[:size]], "next": str(offset + size) if len(rows) > size else None}

    def clear(self) -> dict[str, Any]:
        return {"deleted": self.store.clear_activity()}

    @staticmethod
    def summary(row: Row) -> dict[str, Any]:
        entry = {"id": str(row["id"]), "ts": str(row["ts"]), "level": str(row["level"]), "category": str(row["category"]),
                 "message": str(row["message"])}
        if row.get("details"):
            entry["details"] = str(row["details"])
        return entry
