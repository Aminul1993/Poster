"""PostStore: the queue of scheduled posts and the activity log, in Postgres (DATABASE_URL or POSTGRES_URL) or
a local SQLite file.

The browser keeps nothing about scheduled posts: this table is what the posts list shows and what the
publisher works through. Times are stored as UTC ISO 8601 text (``2026-10-08T13:30:00Z``; activity entries
to the millisecond), which sorts and compares the same way in both databases.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from poster.config import API, BASE_DIR, Config
from poster.errors import ServiceError, truncate

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # only the Postgres queue needs it
    psycopg = None  # type: ignore[assignment]

log = logging.getLogger("poster")

Row = dict[str, Any]

SERVICE = "Database"
SQLITE_FILE = BASE_DIR / "poster.db"
# Connection parameters libpq knows. Hosted URLs carry others (Supabase's supa=, Prisma's pgbouncer=) that it rejects.
LIBPQ_PARAMS = frozenset({"sslmode", "sslrootcert", "sslcert", "sslkey", "sslcrl", "connect_timeout", "application_name",
                          "options", "target_session_attrs", "channel_binding", "keepalives", "keepalives_idle"})

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS poster_posts (
        id TEXT PRIMARY KEY,
        request_key TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        service TEXT NOT NULL,
        channel_name TEXT NOT NULL,
        text TEXT NOT NULL,
        media_url TEXT,
        due_at TEXT NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        retry_at TEXT,
        claimed_at TEXT,
        checked_at TEXT,
        container_id TEXT,
        remote_id TEXT,
        permalink TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        UNIQUE (request_key, channel_id)
    )""",
    "CREATE INDEX IF NOT EXISTS poster_posts_status_due ON poster_posts (status, due_at)",
    "CREATE TABLE IF NOT EXISTS poster_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE IF NOT EXISTS poster_activity (
        id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        level TEXT NOT NULL,
        category TEXT NOT NULL,
        message TEXT NOT NULL,
        details TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS poster_activity_ts ON poster_activity (ts)",
)
DATABASE_ERRORS: tuple[type[Exception], ...] = (sqlite3.Error, psycopg.Error) if psycopg is not None else (sqlite3.Error,)
# Supabase serves tables in the public schema over its REST API. Row level security without policies shuts
# that door; Poster's own connection owns the tables and is not affected.
POSTGRES_ONLY = (
    "ALTER TABLE poster_posts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE poster_state ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE poster_activity ENABLE ROW LEVEL SECURITY",
)
# The posts table's columns. Statements name columns only from this set, and pass every value as a parameter.
COLUMNS = frozenset({"id", "request_key", "channel_id", "service", "channel_name", "text", "media_url", "due_at", "status",
                     "attempts", "retry_at", "claimed_at", "checked_at", "container_id", "remote_id", "permalink", "error", "created_at",
                     "sent_at"})
ACTIVITY_COLUMNS = ("id", "ts", "level", "category", "message", "details")


def iso(value: datetime) -> str:
    """UTC, to the second, with a Z suffix: the one format every stored time uses."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def marks(count: int) -> str:
    """``count`` comma-separated parameter placeholders."""
    return ", ".join(["%s"] * count)


def postgres_dsn(url: str) -> str:
    """The URL without query parameters libpq does not know."""
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key in LIBPQ_PARAMS]
    return urlunsplit(parts._replace(query=urlencode(query)))


class Session:
    """One connection's worth of statements. SQL uses %s placeholders in both databases."""

    def __init__(self, connection: Any, *, sqlite: bool) -> None:
        self.connection = connection
        self.sqlite = sqlite

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[Row]:
        cursor = self.connection.execute(sql.replace("%s", "?") if self.sqlite else sql, params)
        rows = cursor.fetchall() if cursor.description else []
        return [dict(row) for row in rows]


class PostStore:
    """The scheduled posts. Postgres gets a connection per unit of work, since a serverless function keeps
    none open between requests; SQLite shares one connection behind a lock."""

    def __init__(self, config: Config) -> None:
        self.url = config.database_url
        self.postgres = self.url.startswith(("postgres://", "postgresql://"))
        self.problem = self._check_config(config)
        self._lock = threading.Lock()
        self._sqlite: sqlite3.Connection | None = None
        self._ready = False

    def _check_config(self, config: Config) -> str | None:
        if self.postgres:
            return None
        if self.url and not self.url.startswith("sqlite:///"):
            return "DATABASE_URL must be a postgres:// URL (or sqlite:///path for a local file)."
        if config.on_vercel:
            return ("DATABASE_URL (or POSTGRES_URL) is not set. On Vercel, scheduled posts need a Postgres database, "
                    "for example Supabase or Neon from the Vercel Marketplace.")
        return None

    @property
    def configured(self) -> bool:
        return self.problem is None

    @property
    def sqlite_path(self) -> str:
        return self.url.removeprefix("sqlite:///") if self.url else str(SQLITE_FILE)

    def public_info(self) -> dict[str, Any]:
        return {"configured": self.configured, "problem": self.problem, "kind": "postgres" if self.postgres else "sqlite"}

    def require_configured(self) -> None:
        if self.problem:
            raise ServiceError(f"The post queue has no database: {self.problem}", kind="config", service=SERVICE,
                               hint="Set it in the environment or .env file and restart Poster.")

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A connection with the tables in place. Database failures become ServiceErrors."""
        self.require_configured()
        try:
            if self.postgres:
                with self._postgres() as session:
                    yield session
            else:
                with self._lock:
                    yield self._sqlite_session()
        except DATABASE_ERRORS as exc:
            log.warning("Database error: %s", exc)
            first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            raise ServiceError(f"The database failed: {truncate(first_line, 200)}", kind="server", service=SERVICE,
                               hint="Check DATABASE_URL (or POSTGRES_URL) and that the database is running.") from exc

    @contextmanager
    def _postgres(self) -> Iterator[Session]:
        if psycopg is None:
            raise ServiceError("The psycopg package is not installed, so Poster cannot use Postgres.", kind="config",
                               service=SERVICE, hint="pip install -r requirements.txt")
        # Autocommit: every statement stands alone, which is all the queue needs (claims are single UPDATEs).
        # No prepared statements: poolers in transaction mode (Supabase's port 6543) do not support them.
        with psycopg.connect(postgres_dsn(self.url), autocommit=True, prepare_threshold=None, connect_timeout=10,
                             row_factory=dict_row) as connection:
            session = Session(connection, sqlite=False)
            self._create_tables(session)
            yield session

    def _sqlite_session(self) -> Session:
        if self._sqlite is None:
            path = self.sqlite_path
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._sqlite = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._sqlite.row_factory = sqlite3.Row
        session = Session(self._sqlite, sqlite=True)
        self._create_tables(session)
        return session

    def _create_tables(self, session: Session) -> None:
        if self._ready:
            return
        for statement in SCHEMA + (() if session.sqlite else POSTGRES_ONLY):
            session.query(statement)
        self._ready = True

    # -- posts ----------------------------------------------------------------------------------------------------

    def insert(self, rows: list[Row]) -> list[Row]:
        """Adds posts and returns them as stored. A post whose request key and channel are already stored
        (the browser sent the same post again) is not added twice: the stored one is returned instead."""
        stored = []
        with self.session() as db:
            for row in rows:
                db.query(f"INSERT INTO poster_posts ({self._columns(row)}) VALUES ({marks(len(row))}) "  # noqa: S608
                         "ON CONFLICT (request_key, channel_id) DO NOTHING", tuple(row.values()))
                stored.extend(db.query("SELECT * FROM poster_posts WHERE request_key = %s AND channel_id = %s",
                                       (row["request_key"], row["channel_id"])))
        return stored

    def get(self, post_id: str) -> Row | None:
        with self.session() as db:
            rows = db.query("SELECT * FROM poster_posts WHERE id = %s", (post_id,))
        return rows[0] if rows else None

    def page(self, statuses: list[str] | None, direction: str, offset: int, limit: int) -> list[Row]:
        """``limit`` posts from ``offset``, ordered by publish time (``direction`` is asc or desc)."""
        order = "ASC" if direction == "asc" else "DESC"
        where = f"WHERE status IN ({marks(len(statuses))}) " if statuses else ""
        with self.session() as db:
            return db.query(f"SELECT * FROM poster_posts {where}ORDER BY due_at {order}, created_at DESC, id "  # noqa: S608
                            "LIMIT %s OFFSET %s", (*(statuses or ()), limit, offset))

    def update(self, post_id: str, **fields: Any) -> None:
        with self.session() as db:
            db.query(f"UPDATE poster_posts SET {self._assignments(fields)} WHERE id = %s",  # noqa: S608
                     (*fields.values(), post_id))

    def update_editable(self, post_id: str, fields: dict[str, Any]) -> Row | None:
        """Changes a post that is not published or being published. None when there is no such post."""
        editable = API["social"]["editable_statuses"]
        with self.session() as db:
            rows = db.query(f"UPDATE poster_posts SET {self._assignments(fields)} WHERE id = %s "  # noqa: S608
                            f"AND status IN ({marks(len(editable))}) RETURNING *",
                            (*fields.values(), post_id, *editable))
        return rows[0] if rows else None

    def delete_editable(self, post_id: str) -> bool:
        editable = API["social"]["editable_statuses"]
        with self.session() as db:
            rows = db.query(f"DELETE FROM poster_posts WHERE id = %s AND status IN ({marks(len(editable))}) "  # noqa: S608
                            "RETURNING id", (post_id, *editable))
        return bool(rows)

    @staticmethod
    def _columns(fields: dict[str, Any]) -> str:
        """The field names as a column list, after checking each is a column."""
        unknown = set(fields) - COLUMNS
        if unknown:
            raise ValueError(f"Unknown post fields: {', '.join(sorted(unknown))}")
        return ", ".join(fields)

    @classmethod
    def _assignments(cls, fields: dict[str, Any]) -> str:
        return ", ".join(f"{name} = %s" for name in cls._columns(fields).split(", "))

    # -- the publisher --------------------------------------------------------------------------------------------

    def due_ids(self, now: datetime, limit: int) -> list[str]:
        """Scheduled posts whose time has come (and whose wait after a failed try is over), soonest first."""
        stamp = iso(now)
        with self.session() as db:
            rows = db.query("SELECT id FROM poster_posts WHERE status = 'scheduled' AND due_at <= %s "
                            "AND (retry_at IS NULL OR retry_at <= %s) ORDER BY due_at LIMIT %s", (stamp, stamp, limit))
        return [row["id"] for row in rows]

    def stale_ids(self, before: datetime, limit: int) -> list[str]:
        """Posts claimed before ``before``, still sending and not checked since: their run was cut off, or
        their answer was lost."""
        stamp = iso(before)
        with self.session() as db:
            rows = db.query("SELECT id FROM poster_posts WHERE status = 'sending' AND claimed_at <= %s "
                            "AND (checked_at IS NULL OR checked_at <= %s) ORDER BY claimed_at LIMIT %s",
                            (stamp, stamp, limit))
        return [row["id"] for row in rows]

    def claim(self, post_id: str, now: datetime) -> Row | None:
        """Marks a scheduled post as sending, for this run only. None when another run claimed it first: the
        UPDATE is atomic, so two runs at once never publish the same post."""
        with self.session() as db:
            rows = db.query("UPDATE poster_posts SET status = 'sending', claimed_at = %s, checked_at = NULL, "
                            "attempts = attempts + 1 WHERE id = %s AND status = 'scheduled' RETURNING *", (iso(now), post_id))
        return rows[0] if rows else None

    def reclaim(self, post_id: str, now: datetime, before: datetime) -> Row | None:
        """Claims a stale sending post for this run's check (see stale_ids), with the same guarantee as
        claim(). ``claimed_at`` keeps the time of the publish try, which the check searches from."""
        stamp = iso(before)
        with self.session() as db:
            rows = db.query("UPDATE poster_posts SET checked_at = %s WHERE id = %s AND status = 'sending' "
                            "AND claimed_at <= %s AND (checked_at IS NULL OR checked_at <= %s) RETURNING *",
                            (iso(now), post_id, stamp, stamp))
        return rows[0] if rows else None

    # -- the activity log ---------------------------------------------------------------------------------------------

    def add_activity(self, rows: list[Row], keep: int) -> int:
        """Adds log entries, skipping any already stored (sent again after a lost answer), then deletes all
        but the newest ``keep``. Returns how many were added."""
        if not rows:
            return 0
        values = [tuple(row.get(column) for column in ACTIVITY_COLUMNS) for row in rows]
        placeholders = ", ".join(f"({marks(len(ACTIVITY_COLUMNS))})" for _ in values)
        with self.session() as db:
            added = db.query(f"INSERT INTO poster_activity ({', '.join(ACTIVITY_COLUMNS)}) VALUES {placeholders} "  # noqa: S608
                             "ON CONFLICT (id) DO NOTHING RETURNING id", tuple(v for row in values for v in row))
            # Older than the keep-th newest entry; entries sharing its time all stay.
            db.query("DELETE FROM poster_activity WHERE ts < "
                     "(SELECT ts FROM poster_activity ORDER BY ts DESC LIMIT 1 OFFSET %s)", (keep - 1,))
        return len(added)

    def activity_page(self, levels: list[str], categories: list[str], offset: int, limit: int) -> list[Row]:
        """``limit`` entries from ``offset``, newest first: those at any of ``levels`` or in any of
        ``categories``, or every entry when both are empty."""
        conditions = [f"level IN ({marks(len(levels))})" if levels else "",
                      f"category IN ({marks(len(categories))})" if categories else ""]
        where = " OR ".join(c for c in conditions if c)
        with self.session() as db:
            return db.query(f"SELECT * FROM poster_activity {'WHERE ' + where if where else ''} "  # noqa: S608
                            "ORDER BY ts DESC, id DESC LIMIT %s OFFSET %s", (*levels, *categories, limit, offset))

    def clear_activity(self) -> int:
        with self.session() as db:
            return len(db.query("DELETE FROM poster_activity RETURNING id"))

    # -- state ----------------------------------------------------------------------------------------------------

    def get_state(self, key: str) -> str | None:
        with self.session() as db:
            rows = db.query("SELECT value FROM poster_state WHERE key = %s", (key,))
        return str(rows[0]["value"]) if rows else None

    def set_state(self, key: str, value: str) -> None:
        with self.session() as db:
            db.query("INSERT INTO poster_state (key, value) VALUES (%s, %s) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))
