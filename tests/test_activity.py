"""The activity log in the database: what the dashboard saves, how it is read back and cleared, and what the
publisher records on its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from poster.config import API
from tests.conftest import post
from tests.test_publisher import publish, schedule
from tests.test_routes import queue


def entry(number: int, level: str = "info", category: str = "user", **fields: Any) -> dict[str, Any]:
    """The dashboard's entry, ``number`` seconds into 2026-01-01."""
    return {"id": f"log_{number:04d}", "ts": f"2026-01-01T10:00:{number % 60:02d}.{number // 60:03d}Z", "level": level,
            "category": category, "message": f"Entry {number}", **fields}


def save(client: Any, *entries: dict[str, Any]) -> Any:
    return post(client, "/api/activity", {"entries": list(entries)})


def listed(client: Any, **body: Any) -> dict[str, Any]:
    response = post(client, "/api/activity/list", body)
    assert response.status_code == 200
    return dict(response.get_json())


def messages(client: Any, **body: Any) -> list[str]:
    return [e["message"] for e in listed(client, **body)["entries"]]


class TestSave:
    def test_entries_come_back_newest_first(self, client: Any) -> None:
        response = save(client, entry(1), entry(3, details="More about it"), entry(2))
        assert response.status_code == 200 and response.get_json()["saved"] == 3
        data = listed(client)
        assert [e["id"] for e in data["entries"]] == ["log_0003", "log_0002", "log_0001"]
        assert data["entries"][0] == {"id": "log_0003", "ts": "2026-01-01T10:00:03.000Z", "level": "info", "category": "user",
                                      "message": "Entry 3", "details": "More about it"}
        assert "details" not in data["entries"][1] and data["next"] is None

    def test_an_entry_sent_again_is_stored_once(self, client: Any) -> None:
        save(client, entry(1), entry(2))
        assert save(client, entry(2), entry(3)).get_json()["saved"] == 1
        assert messages(client) == ["Entry 3", "Entry 2", "Entry 1"]

    def test_a_time_in_the_future_becomes_now(self, client: Any) -> None:
        ahead = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        save(client, {**entry(1), "ts": ahead})
        stored = datetime.fromisoformat(listed(client)["entries"][0]["ts"].replace("Z", "+00:00"))
        assert abs(stored - datetime.now(UTC)) < timedelta(minutes=1)

    def test_long_text_is_shortened(self, client: Any) -> None:
        save(client, entry(1, message="x" * 5000, details="y" * 5000))
        [stored] = listed(client)["entries"]
        assert len(stored["message"]) == API["activity"]["max_message_chars"]
        assert len(stored["details"]) == API["activity"]["max_details_chars"]

    @pytest.mark.parametrize("body", [
        {}, {"entries": []}, {"entries": "x"}, {"entries": [entry(n) for n in range(101)]},
        {"entries": [entry(1, level="debug")]}, {"entries": [entry(1, category="Not a word")]},
        {"entries": [entry(1, message=" ")]}, {"entries": [entry(1, ts="yesterday")]}, {"entries": [entry(1, ts="2026-01-01T10:00:00")]},
        {"entries": [{**entry(1), "id": "no spaces"}]}, {"entries": [entry(1, details=3)]}, {"entries": [5]},
    ])
    def test_refuses_malformed_entries(self, client: Any, body: dict[str, Any]) -> None:
        response = post(client, "/api/activity", body)
        assert response.status_code == 400 and response.get_json()["error"]["kind"] == "validation"
        assert listed(client)["entries"] == []

    def test_keeps_only_the_newest(self, client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(API["activity"], "keep", 3)
        save(client, *(entry(n) for n in range(1, 6)))
        assert messages(client) == ["Entry 5", "Entry 4", "Entry 3"]


class TestList:
    def test_filters_match_the_dashboard(self, client: Any) -> None:
        save(client, entry(1, "error", "ai"), entry(2, "warn", "schedule"), entry(3, "info", "retry"), entry(4, "success", "ai"),
             entry(5, "info", "settings"), entry(6, "info", "system"))
        assert messages(client, filter="error") == ["Entry 1"]
        assert messages(client, filter="warn") == ["Entry 3", "Entry 2"]
        assert messages(client, filter="success") == ["Entry 4"]
        assert messages(client, filter="user") == ["Entry 5"]
        assert len(messages(client, filter="all")) == len(messages(client)) == 6
        assert post(client, "/api/activity/list", {"filter": "debug"}).status_code == 400

    def test_pages(self, client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(API["activity"], "page_size", 2)
        save(client, *(entry(n) for n in range(1, 6)))
        first = listed(client)
        assert [e["message"] for e in first["entries"]] == ["Entry 5", "Entry 4"] and first["next"] == "2"
        second = listed(client, after=first["next"])
        assert [e["message"] for e in second["entries"]] == ["Entry 3", "Entry 2"]
        assert listed(client, after=second["next"]) == {"entries": [entry(1) | {"ts": "2026-01-01T10:00:01.000Z"}], "next": None,
                                                         "retries": []}

    def test_clear_deletes_everything(self, client: Any) -> None:
        save(client, entry(1), entry(2))
        assert post(client, "/api/activity/clear", {}).get_json()["deleted"] == 2
        assert listed(client)["entries"] == []

    def test_without_a_database_it_says_so(self, make_app: Any) -> None:
        response = post(make_app(DATABASE_URL="mysql://db", VERCEL="1").test_client(), "/api/activity/list", {})
        assert response.status_code == 503 and response.get_json()["error"]["service"] == "Database"


class TestPublisherEntries:
    def test_records_what_it_published(self, client: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        publish(client, due)
        [published] = listed(client)["entries"]
        assert published["level"] == "success" and published["category"] == "publish"
        assert published["message"] == 'Poster Studio (Facebook) post "Hello world #test": published.'
        assert published["details"].startswith("https://www.facebook.com/pg1_")

    def test_records_retries_and_failures(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        fake.fail("meta.photos", 400, {"error": {"message": "Calls to this api have exceeded the rate limit.", "code": 32}}, times=4)
        publish(client, due)
        [retry] = listed(client)["entries"]
        assert (retry["level"], retry["category"]) == ("warn", "retry") and "trying again in 2 minutes" in retry["message"]

        fake.fail("meta.photos", 400, {"error": {"message": "Invalid parameter", "code": 100}})
        publish(client, due + timedelta(minutes=2))
        failed = listed(client, filter="error")["entries"]
        assert [e["message"] for e in failed] == ['Poster Studio (Facebook) post "Hello world #test": not published.']
        assert "Invalid parameter" in failed[0]["details"]

    def test_a_database_failure_does_not_stop_publishing(self, client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from poster.errors import ServiceError

        due = schedule(client, channelIds=["pg1"])
        store = client.application.extensions["poster"].social.store

        def broken(*_args: Any) -> int:
            raise ServiceError("The database failed: disk full", kind="server", service="Database")

        monkeypatch.setattr(store, "add_activity", broken)
        assert publish(client, due)["published"] == 1
        assert queue(client)[0]["status"] == "sent"


def test_the_posts_list_needs_only_the_database(make_app: Any) -> None:
    """Without a Meta token nothing can be scheduled or published, but the queue can still be read and changed."""
    client = make_app(META_ACCESS_TOKEN="").test_client()
    assert client.application.extensions["poster"].social.configured is False
    response = post(client, "/api/social/posts", {"filter": "all"})
    assert response.status_code == 200 and response.get_json()["posts"] == []
