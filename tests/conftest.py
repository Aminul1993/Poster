"""Shared fixtures. Every test runs against FakeUpstream: no request leaves the machine,
and the developer's own .env never reaches the app under test."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import fakes  # noqa: E402
from tests.fakes import FakeUpstream  # noqa: E402

# Every setting Poster reads, so values from a local .env can never leak into a test.
BASE_ENV: dict[str, str] = {
    "OLLAMA_ENDPOINT": f"https://{fakes.OLLAMA_HOST}",
    "OLLAMA_API_KEY": "test-ollama-key",
    "OLLAMA_MODEL": "",
    "OLLAMA_MODELS": "",
    "MODEL": "gemma4:31b",
    "BUFFER_ACCESS_TOKEN": "test-buffer-token",
    "BUFFER_API_URL": f"https://{fakes.BUFFER_HOST}",
    "BLOB_READ_WRITE_TOKEN": "",
    "BLOB_STORE_ID": "",
    "VERCEL_BLOB_API_URL": f"https://{fakes.BLOB_API_HOST}/api/blob",
    "PUBLIC_BASE_URL": "",
    "MEDIA_UPLOAD_ENDPOINT": "",
    "MEDIA_UPLOAD_PRESET": "",
    "POLLINATIONS_API_KEY": "test-pollinations-key",
    "POLLINATIONS_EDIT_MODEL": "",
    "OLLAMA_TIMEOUT": "",
    "BUFFER_TIMEOUT": "",
    "MEDIA_TIMEOUT": "",
    "POLLINATIONS_TIMEOUT": "",
    "POSTER_MODE": "auto",
    "POSTER_HOST": "127.0.0.1",
    "POSTER_PORT": "",
    "POSTER_PASSWORD": "",
    "POSTER_REQUEST_BUDGET": "",
    "FLASK_DEBUG": "0",
    "VERCEL": "",
}

BLOB_ENV: dict[str, str] = {
    "BLOB_READ_WRITE_TOKEN": f"vercel_blob_rw_{fakes.BLOB_STORE}_secretsecret",
    "PUBLIC_BASE_URL": "https://poster.test",
}

CLOUDINARY_ENV: dict[str, str] = {
    "MEDIA_UPLOAD_ENDPOINT": f"https://{fakes.CLOUDINARY_HOST}/v1_1/demo/image/upload",
    "MEDIA_UPLOAD_PRESET": "poster_unsigned",
}

# Headers the dashboard sends with every state-changing API call.
API_HEADERS = {"X-Poster-Request": "1"}


try:
    import poster as _poster
except ImportError:  # the single-file app.py, before the poster package existed
    _poster = None


@pytest.fixture(autouse=True)
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeUpstream:
    upstream = FakeUpstream()
    monkeypatch.setattr(requests.Session, "request",
                        lambda session, method, url, **kwargs: upstream.session_request(session, method, url, **kwargs))
    return upstream


@pytest.fixture(autouse=True)
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Retry back-off is instant in tests; this records the waits instead."""
    recorded: list[float] = []
    if _poster is not None:
        from poster import retry
        monkeypatch.setattr(retry, "_sleep", recorded.append)
    return recorded


@pytest.fixture
def make_app(monkeypatch: pytest.MonkeyPatch, waits: list[float]) -> Callable[..., Any]:
    """Builds the Flask app from BASE_ENV plus overrides, never reading the real .env."""

    def build(**overrides: str) -> Any:
        env = {**BASE_ENV, **overrides}
        if _poster is not None:
            from poster.config import Config
            app = _poster.create_app(Config.from_env(env, env_file=None))
        else:
            import importlib
            for key, value in env.items():  # set before import, so the .env file cannot override them
                monkeypatch.setenv(key, value)
            import app as legacy
            legacy = importlib.reload(legacy)
            legacy.retry_request.__kwdefaults__["sleep"] = waits.append
            app = legacy.app
        app.testing = True
        return app

    return build


@pytest.fixture
def client(make_app: Callable[..., Any]) -> Any:
    return make_app().test_client()


@pytest.fixture
def blob_client(make_app: Callable[..., Any]) -> Any:
    return make_app(**BLOB_ENV).test_client()


def post(client: Any, path: str, json: Any = None, **kwargs: Any) -> Any:
    """POST as the dashboard does (with the CSRF header)."""
    headers = {**API_HEADERS, **kwargs.pop("headers", {})}
    return client.post(path, json=json, headers=headers, **kwargs)
