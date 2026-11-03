"""The activity log and the posts list, read from the server's database."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Page, expect

from tests.browser.conftest import confirm


def saved_messages(app: Any) -> list[str]:
    return [e["message"] for e in app.extensions["poster"].activity.page("all", None)["entries"]]


def test_the_log_is_kept_in_the_database(live: Page, served: list[Any]) -> None:
    page = live
    [app] = served
    page.click("#theme-toggle")
    theme = page.locator("#log-list .log-msg", has_text="theme.")
    expect(theme).to_have_count(1)
    expect(page.locator("#log-source")).to_contain_text("Read from the database")
    expect(page.locator("#log-source")).not_to_contain_text("waiting to be saved")
    assert any(m.startswith("Switched to the") for m in saved_messages(app))

    # Another browser (here: this one with its storage cleared) reads the same log.
    page.evaluate("localStorage.clear()")
    page.reload()
    expect(theme).to_have_count(1)

    # The publisher's entries appear on Refresh.
    app.extensions["poster"].activity.record("error", "publish", 'Poster Studio (Facebook) post "Hello": not published.', "Invalid parameter")
    page.click("#log-refresh")
    failure = page.locator("#log-list .log-item[data-level='error']")
    expect(failure).to_contain_text("not published.")
    expect(failure).to_contain_text("Invalid parameter")

    # Filters are applied by the server.
    page.select_option("#log-filter", "error")
    expect(page.locator("#log-list > li")).to_have_count(1)
    page.select_option("#log-filter", "all")
    page.click("#clear-log")
    confirm(page, "Clear log")
    expect(page.locator("#log-count")).to_have_text("0")
    assert saved_messages(app) == []


def test_a_log_from_browser_storage_is_saved(serve: Callable[..., str], page: Page, problems: list[str], served: list[Any]) -> None:
    earlier = [{"id": "log_old1", "ts": "2026-01-01T09:00:00.000Z", "level": "success", "category": "schedule",
                "message": 'Scheduled "beach.jpg" for Fri, 2 Jan, 10:00 am on 2 channels.'}]
    page.add_init_script(f"if (!sessionStorage.seeded) {{ localStorage.setItem('poster.log', {json.dumps(json.dumps(earlier))}); "
                         "sessionStorage.seeded = '1'; }")
    page.goto(serve(POSTER_MODE="demo"))
    expect(page.locator("#log-list .log-msg", has_text='Scheduled "beach.jpg"')).to_have_count(1)
    expect(page.locator("#log-source")).not_to_contain_text("waiting to be saved")
    [app] = served
    assert earlier[0]["message"] in saved_messages(app)
    page.wait_for_function("() => JSON.parse(localStorage.getItem('poster.log')).length === 0")  # nothing left to save


def test_without_a_database_the_log_stays_in_the_browser(serve: Callable[..., str], page: Page, problems: list[str]) -> None:
    page.goto(serve(POSTER_MODE="demo", DATABASE_URL="mysql://db.example.com/poster"))
    expect(page.locator("#log-list > li").first).to_be_visible()
    expect(page.locator("#log-source")).to_have_text("Kept in this browser only: the server has no database")
    expect(page.locator("#log-refresh")).to_be_hidden()


def test_scheduled_posts_show_without_a_meta_token(serve: Callable[..., str], page: Page, problems: list[str],
                                                   served: list[Any]) -> None:
    url = serve(META_ACCESS_TOKEN="")
    [app] = served
    app.extensions["poster"].social.store.insert([{
        "id": "p1", "request_key": "k1", "channel_id": "pg1", "service": "facebook", "channel_name": "Poster Studio",
        "text": "Queued before the token was removed", "media_url": None, "due_at": "2030-01-01T10:00:00Z",
        "status": "scheduled", "created_at": "2026-01-01T00:00:00Z"}])
    page.goto(url)
    expect(page.locator("#mode-badge-label")).to_have_text("Live mode")
    expect(page.locator("#sched-list > li")).to_have_count(1)
    expect(page.locator("#sched-list")).to_contain_text("Queued before the token was removed")
    expect(page.locator("#sched-source")).to_contain_text("Read from the queue")
