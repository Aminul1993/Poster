"""Configuration from the environment and the .env file."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from poster.config import Config, check_url, is_local_host, read_env_file
from poster.store import PostStore, postgres_dsn


def test_defaults() -> None:
    config = Config.from_env({}, env_file=None)
    assert config.groq_endpoint == "https://api.groq.com/openai/v1" and config.model == "qwen/qwen3.8-27b"
    assert config.mode == "auto" and config.host == "127.0.0.1" and config.port == 5000
    assert config.request_budget == 0 and config.max_upload_bytes == 20 * 1024 * 1024
    assert config.groq_models == () and not config.password


def test_vercel_defaults() -> None:
    config = Config.from_env({"VERCEL": "1"}, env_file=None)
    assert config.on_vercel and config.request_budget == 280 and config.max_upload_bytes == 4 * 1024 * 1024
    assert Config.from_env({"VERCEL": "1", "POSTER_REQUEST_BUDGET": "120"}, env_file=None).request_budget == 120


def test_env_file_values_lose_to_the_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nexport MODEL="qwen/qwen3-32b"\nPOSTER_PORT=8080\nBROKEN LINE\nPOSTER_HOST=0.0.0.0\n',
                        encoding="utf-8")
    assert read_env_file(env_file) == {"MODEL": "qwen/qwen3-32b", "POSTER_PORT": "8080", "POSTER_HOST": "0.0.0.0"}
    config = Config.from_env({"POSTER_HOST": "127.0.0.1"}, env_file=env_file)
    assert config.model == "qwen/qwen3-32b" and config.port == 8080 and config.host == "127.0.0.1"


def test_numbers_are_clamped_and_bad_ones_ignored(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="poster"):
        config = Config.from_env({"GROQ_TIMEOUT": "9999", "META_TIMEOUT": "soon"}, env_file=None)
    assert config.groq_timeout == 600 and config.meta_timeout == 30
    assert "META_TIMEOUT='soon' is not a whole number" in caplog.text


def test_model_allowlist_always_includes_the_default() -> None:
    config = Config.from_env({"GROQ_MODEL": "qwen/qwen3.8-27b", "GROQ_MODELS": "qwen/qwen3-32b, qwen/qwen3.8-27b,, qwen/qwen3-32b"}, env_file=None)
    assert config.groq_models == ("qwen/qwen3.8-27b", "qwen/qwen3-32b")


@pytest.mark.parametrize(("host", "local"), [("localhost", True), ("127.0.0.5", True), ("::1", True), ("[::1]", True),
                                             ("app.localhost", True), ("0.0.0.0", True), ("poster.example.com", False),
                                             ("10.0.0.5", False), ("", False)])
def test_is_local_host(host: str, local: bool) -> None:
    assert is_local_host(host) is local


@pytest.mark.parametrize(("url", "https_only", "problem"), [
    ("https://api.example.com", True, None), ("http://localhost:11434", True, None),
    ("http://api.example.com", True, "must use https://"), ("ftp://x", False, "full http(s) URL"),
    ("https://user:pw@api.example.com", False, "must not contain a username"),
])
def test_check_url(url: str, https_only: bool, problem: str | None) -> None:
    result = check_url(url, "X", https_only=https_only)
    assert (result is None) if problem is None else (problem in (result or ""))


def test_database_url_from_the_vercel_integration() -> None:
    supabase = "postgres://postgres.abc:pw@aws-0-eu.pooler.supabase.com:6543/postgres?sslmode=require&supa=base-pooler.x"
    assert Config.from_env({"POSTGRES_URL": supabase}, env_file=None).database_url == supabase
    both = Config.from_env({"POSTGRES_URL": supabase, "DATABASE_URL": "postgresql://db.test/poster"}, env_file=None)
    assert both.database_url == "postgresql://db.test/poster"


def test_postgres_url_keeps_only_what_libpq_knows() -> None:
    url = "postgres://u:p@pooler.test:6543/postgres?sslmode=require&supa=base-pooler.x&pgbouncer=true"
    assert postgres_dsn(url) == "postgres://u:p@pooler.test:6543/postgres?sslmode=require"


@pytest.mark.parametrize(("env", "problem"), [
    ({}, None),  # a local SQLite file
    ({"DATABASE_URL": "postgres://db.test/poster"}, None),
    ({"DATABASE_URL": "mysql://db.test/poster"}, "must be a postgres:// URL"),
    ({"VERCEL": "1"}, "On Vercel, scheduled posts need a Postgres database"),
])
def test_database_problems(env: dict[str, str], problem: str | None) -> None:
    store = PostStore(Config.from_env({"DATABASE_URL": "", "POSTGRES_URL": "", **env}, env_file=None))
    assert (store.problem is None) if problem is None else (problem in (store.problem or ""))
