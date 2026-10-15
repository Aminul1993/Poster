"""Configuration from the environment and the .env file."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from poster.config import Config, check_url, is_local_host, read_env_file


def test_defaults() -> None:
    config = Config.from_env({}, env_file=None)
    assert config.ollama_endpoint == "https://ollama.com" and config.model == "gemma4:31b"
    assert config.mode == "auto" and config.host == "127.0.0.1" and config.port == 5000
    assert config.request_budget == 0 and config.max_upload_bytes == 20 * 1024 * 1024
    assert config.ollama_models == () and not config.password


def test_vercel_defaults() -> None:
    config = Config.from_env({"VERCEL": "1"}, env_file=None)
    assert config.on_vercel and config.request_budget == 280 and config.max_upload_bytes == 4 * 1024 * 1024
    assert Config.from_env({"VERCEL": "1", "POSTER_REQUEST_BUDGET": "120"}, env_file=None).request_budget == 120


def test_env_file_values_lose_to_the_environment(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nexport MODEL="llava:13b"\nPOSTER_PORT=8080\nBROKEN LINE\nPOSTER_HOST=0.0.0.0\n',
                        encoding="utf-8")
    assert read_env_file(env_file) == {"MODEL": "llava:13b", "POSTER_PORT": "8080", "POSTER_HOST": "0.0.0.0"}
    config = Config.from_env({"POSTER_HOST": "127.0.0.1"}, env_file=env_file)
    assert config.model == "llava:13b" and config.port == 8080 and config.host == "127.0.0.1"


def test_numbers_are_clamped_and_bad_ones_ignored(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="poster"):
        config = Config.from_env({"OLLAMA_TIMEOUT": "9999", "BUFFER_TIMEOUT": "soon"}, env_file=None)
    assert config.ollama_timeout == 600 and config.buffer_timeout == 30
    assert "BUFFER_TIMEOUT='soon' is not a whole number" in caplog.text


def test_model_allowlist_always_includes_the_default() -> None:
    config = Config.from_env({"OLLAMA_MODEL": "gemma4:31b", "OLLAMA_MODELS": "llava:13b, gemma4:31b,, llava:13b"}, env_file=None)
    assert config.ollama_models == ("gemma4:31b", "llava:13b")


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
