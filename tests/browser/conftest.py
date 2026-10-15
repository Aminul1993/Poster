"""Browser tests: the real dashboard in Chromium/Edge against a live Poster server
backed by FakeUpstream. Run with ``pytest tests/browser`` (add ``--browser-channel msedge``
to use an installed Edge instead of Playwright's Chromium)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import ConsoleMessage, Page, expect
from werkzeug.serving import make_server

from tests.conftest import BLOB_ENV
from tests.fakes import BLOB_PUBLIC_HOST, FakeUpstream, image_bytes

expect.set_options(timeout=15_000)


@pytest.fixture
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "timezone_id": "UTC", "locale": "en-GB", "accept_downloads": True,
            "viewport": {"width": 1400, "height": 1000}}


@pytest.fixture
def serve(make_app: Callable[..., Any]) -> Iterator[Callable[..., str]]:
    """Starts Poster on a free local port and returns its URL."""
    servers: list[Any] = []

    def start(**env: str) -> str:
        server = make_server("127.0.0.1", 0, make_app(**env), threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    for server in servers:
        server.shutdown()


@pytest.fixture
def problems(page: Page) -> Iterator[list[str]]:
    """Console errors and uncaught exceptions; a test fails if any remain at the end."""
    found: list[str] = []

    def on_console(message: ConsoleMessage) -> None:
        # The browser logs every non-2xx API answer; the app handles those, so only other errors count.
        if message.type == "error" and "the server responded with a status of" not in message.text:
            found.append(f"console: {message.text}")

    page.on("console", on_console)
    page.on("pageerror", lambda error: found.append(f"pageerror: {error}"))
    yield found
    assert not found, "\n".join(found)


@pytest.fixture
def demo(serve: Callable[..., str], page: Page, problems: list[str]) -> Page:
    page.goto(serve(POSTER_MODE="demo"))
    expect(page.locator("#mode-badge-label")).to_have_text("Demo mode")
    return page


@pytest.fixture
def live(serve: Callable[..., str], page: Page, problems: list[str], fake: FakeUpstream) -> Page:
    # The stored thumbnails live on Vercel Blob; the browser gets them from the fake store.
    page.route(f"https://{BLOB_PUBLIC_HOST}/**", lambda route: route.fulfill(
        status=200 if route.request.url in fake.blobs else 404, body=fake.blobs.get(route.request.url, b""),
        content_type="image/avif"))
    page.goto(serve(**BLOB_ENV))
    expect(page.locator("#mode-badge-label")).to_have_text("Live mode")
    return page


def local_input(days: float = 2, hour: int = 10) -> str:
    """A datetime-local value in the future (the browser runs in UTC)."""
    when = (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0)
    return when.strftime("%Y-%m-%dT%H:%M")


def jpeg(color: tuple[int, int, int], size: tuple[int, int] = (320, 240)) -> bytes:
    return image_bytes("JPEG", size, color)


def add_images(page: Page, *files: tuple[str, bytes]) -> None:
    page.set_input_files("#file-input", files=[{"name": name, "mimeType": "image/jpeg", "buffer": data} for name, data in files])


def confirm(page: Page, label: str) -> None:
    dialog = page.locator("#confirm-dialog")
    expect(dialog).to_be_visible()
    dialog.locator("#confirm-actions button", has_text=label).click()
    expect(dialog).to_be_hidden()


def toast(page: Page, text: str) -> None:
    expect(page.locator("#toast-region .toast", has_text=text).first).to_be_visible()
