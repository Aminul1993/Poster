"""The publisher: publishing queued posts when they fall due, retrying what failed safely, and never
publishing a post twice, even when Meta's answer is lost or two runs overlap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import requests

from poster.errors import ServiceError
from poster.retry import RetryLog
from poster.store import iso
from tests.conftest import post
from tests.test_routes import queue, schedule_body

CRON = {"Authorization": "Bearer test-cron-secret"}


def schedule(client: Any, minutes: float = 0.1, **overrides: Any) -> datetime:
    """Queues a post due in ``minutes`` and returns its publish time. The fake Graph API stamps posts with
    the real time, so posts are due within seconds for the lost-answer checks to find them."""
    due = (datetime.now(UTC) + timedelta(minutes=minutes)).replace(microsecond=0)
    body = schedule_body(dueAt=due.isoformat().replace("+00:00", "Z"), **overrides)
    assert post(client, "/api/social/schedule", body).status_code == 200
    return due


def publish(client: Any, at: datetime) -> dict[str, int]:
    """One publisher run, as if it were ``at`` now."""
    return dict(client.application.extensions["poster"].social.publish_due(RetryLog("Publish"), now=at))


def by_service(client: Any) -> dict[str, dict[str, Any]]:
    return {row["service"]: row for row in queue(client)}


class TestRoute:
    def test_needs_the_cron_secret(self, client: Any) -> None:
        assert client.get("/api/publish/due").status_code == 401
        assert client.get("/api/publish/due", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get("/api/publish/due", headers=CRON)
        assert response.status_code == 200
        assert response.get_json() == {"published": 0, "retrying": 0, "failed": 0, "checking": 0, "retries": []}

    def test_needs_no_dashboard_password(self, make_app: Any) -> None:
        locked = make_app(POSTER_PASSWORD="correct horse battery staple").test_client()
        assert locked.get("/api/publish/due", base_url="https://poster.example.com", headers=CRON).status_code == 200
        assert locked.get("/api/publish/due", base_url="https://poster.example.com").status_code == 401

    def test_without_a_secret_it_is_off(self, make_app: Any) -> None:
        response = make_app(CRON_SECRET="").test_client().get("/api/publish/due", headers={"Authorization": "Bearer "})
        assert response.status_code == 503 and "CRON_SECRET" in response.get_json()["error"]["message"]

    def test_publishes_what_is_due(self, client: Any, fake: Any) -> None:
        schedule(client, channelIds=["pg1"])
        store = client.application.extensions["poster"].social.store
        store.update(queue(client)[0]["id"], due_at="2020-01-01T00:00:00Z")
        assert client.get("/api/publish/due", headers=CRON).get_json()["published"] == 1
        assert len(fake.meta.feed["pg1"]) == 1


class TestPublishing:
    def test_publishes_facebook_and_instagram_posts_when_due(self, client: Any, fake: Any) -> None:
        due = schedule(client)
        assert publish(client, due - timedelta(seconds=1)) == {"published": 0, "retrying": 0, "failed": 0, "checking": 0}
        assert fake.ops() == ["meta.accounts"]  # only the schedule request's lookup

        assert publish(client, due)["published"] == 2
        [facebook_post] = fake.meta.feed["pg1"]
        [instagram_post] = fake.meta.media["ig1"]
        assert facebook_post["message"] == instagram_post["caption"] == "Hello world #test"
        assert facebook_post["image"] == instagram_post["image"] == "https://cdn.example.com/a.jpg"
        rows = by_service(client)
        assert rows["facebook"]["status"] == rows["instagram"]["status"] == "sent"
        assert rows["facebook"]["permalink"].startswith("https://www.facebook.com/pg1_")
        assert rows["instagram"]["permalink"].startswith("https://www.instagram.com/p/")
        # Each Page acts with its own token.
        photos = next(c for c in fake.calls if c.op == "meta.photos")
        assert photos.kwargs["data"]["access_token"] == "page-token-1"

        listed = post(client, "/api/social/posts", {"filter": "published"}).get_json()["posts"]
        assert {p["service"] for p in listed} == {"facebook", "instagram"} and all(p["externalLink"] for p in listed)

    def test_a_text_post_goes_to_the_page_feed(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"], mediaUrl=None)
        assert publish(client, due)["published"] == 1
        assert "meta.feed" in fake.ops() and "meta.photos" not in fake.ops()

    def test_nothing_due_calls_nothing(self, client: Any, fake: Any) -> None:
        publish(client, datetime.now(UTC))
        assert fake.calls == []

    def test_a_second_run_publishes_nothing_again(self, client: Any, fake: Any) -> None:
        due = schedule(client)
        publish(client, due)
        assert publish(client, due + timedelta(minutes=1))["published"] == 0
        assert len(fake.meta.all_posts()) == 2

    def test_a_post_another_run_claimed_is_left_alone(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        store = client.application.extensions["poster"].social.store
        assert store.claim(queue(client)[0]["id"], due) is not None  # another run got there first
        assert publish(client, due)["published"] == 0
        assert fake.meta.all_posts() == []

    def test_waits_for_instagram_to_process_the_image(self, client: Any, fake: Any, waits: list[float]) -> None:
        fake.meta.container_status = "IN_PROGRESS"
        due = schedule(client, channelIds=["ig1"])
        assert publish(client, due)["published"] == 1
        assert waits == [1] and fake.ops("meta.container") == ["meta.container"] * 2

    def test_an_image_instagram_refuses(self, client: Any, fake: Any) -> None:
        fake.meta.container_status = "ERROR"
        due = schedule(client, channelIds=["ig1"])
        assert publish(client, due)["failed"] == 1
        row = queue(client)[0]
        assert row["status"] == "error" and "JPEG" in row["error"] and row["container_id"] is None


class TestFailures:
    def test_a_rate_limit_is_tried_again_later(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        fake.fail("meta.photos", 400, {"error": {"message": "Calls to this api have exceeded the rate limit.", "code": 32}},
                  times=4)
        assert publish(client, due)["retrying"] == 1
        row = queue(client)[0]
        assert row["status"] == "scheduled" and row["attempts"] == 1
        assert row["retry_at"] == iso(due + timedelta(minutes=2))
        assert "tries again" in row["error"]

        assert publish(client, due + timedelta(minutes=1))["published"] == 0  # still waiting
        assert publish(client, due + timedelta(minutes=2))["published"] == 1
        sent = queue(client)[0]
        assert sent["status"] == "sent" and sent["error"] is None and len(fake.meta.feed["pg1"]) == 1

    def test_a_refusal_is_final_until_the_post_is_edited(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        fake.fail("meta.photos", 400, {"error": {"message": "Invalid parameter", "code": 100}})
        assert publish(client, due)["failed"] == 1
        row = queue(client)[0]
        assert row["status"] == "error" and "Invalid parameter" in row["error"]
        assert publish(client, due + timedelta(hours=1))["published"] == 0

        post(client, "/api/social/posts/edit", {"postIds": [row["id"]], "text": "Hello again #test"})
        assert publish(client, due + timedelta(hours=1))["published"] == 1
        assert fake.meta.feed["pg1"][0]["message"] == "Hello again #test"

    def test_gives_up_after_the_last_try(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        social = client.application.extensions["poster"].social
        social.store.update(queue(client)[0]["id"], attempts=4)
        fake.fail("meta.photos", 503, times=4)
        assert publish(client, due)["failed"] == 1
        assert "gave up after 5 tries" in queue(client)[0]["error"]

    def test_a_bad_token_stops_the_run_and_keeps_posts_queued(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        fake.fail("meta.photos", 400, {"error": {"message": "Error validating access token", "code": 190}})
        with pytest.raises(ServiceError) as raised:
            publish(client, due)
        assert raised.value.kind == "auth"
        row = queue(client)[0]
        assert row["status"] == "scheduled" and row["attempts"] == 0 and "access token" in row["error"]
        assert publish(client, due)["published"] == 1  # once the token works again

    def test_a_post_the_token_lost_access_to(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        fake.meta.pages[0]["tasks"] = ["ANALYZE"]
        assert publish(client, due)["failed"] == 1
        assert "full control" in queue(client)[0]["error"]


class TestLostAnswers:
    def test_a_facebook_post_that_went_out_is_found_not_posted_again(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        fake.fail("meta.photos", exc=requests.ReadTimeout("lost"), after=True)
        assert publish(client, due)["checking"] == 1
        row = queue(client)[0]
        assert row["status"] == "sending" and "Checking whether it was published" in row["error"]

        assert publish(client, due + timedelta(minutes=5))["checking"] == 0  # not stale yet: left alone
        assert publish(client, due + timedelta(minutes=11))["published"] == 1
        assert queue(client)[0]["status"] == "sent" and queue(client)[0]["permalink"] == "https://www.facebook.com/pg1_p1"
        assert len(fake.meta.feed["pg1"]) == 1

    def test_a_check_that_fails_is_repeated_from_the_publish_time(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        fake.fail("meta.photos", exc=requests.ReadTimeout("lost"), after=True)
        publish(client, due)
        fake.fail("meta.posts", 503, times=4)
        assert publish(client, due + timedelta(minutes=11))["checking"] == 1
        assert publish(client, due + timedelta(minutes=15))["checking"] == 0  # checked recently: left alone
        assert publish(client, due + timedelta(minutes=22))["published"] == 1
        assert len(fake.meta.feed["pg1"]) == 1

    def test_a_facebook_post_that_did_not_go_out_is_tried_again(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["pg1"])
        fake.fail("meta.photos", exc=requests.ReadTimeout("lost"))
        publish(client, due)
        assert publish(client, due + timedelta(minutes=11))["retrying"] == 1
        later = datetime.fromisoformat(queue(client)[0]["retry_at"].replace("Z", "+00:00"))
        assert publish(client, later)["published"] == 1
        assert len(fake.meta.feed["pg1"]) == 1

    def test_an_instagram_container_already_published_is_not_published_again(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["ig1"])
        fake.fail("meta.media_publish", exc=requests.ReadTimeout("lost"), after=True)
        assert publish(client, due)["checking"] == 1
        assert publish(client, due + timedelta(minutes=11))["published"] == 1
        assert len(fake.meta.media["ig1"]) == 1 and fake.ops("meta.media_publish") == ["meta.media_publish"]
        assert queue(client)[0]["permalink"].startswith("https://www.instagram.com/p/")

    def test_an_instagram_retry_reuses_the_uploaded_image(self, client: Any, fake: Any) -> None:
        due = schedule(client, channelIds=["ig1"])
        fake.fail("meta.media_publish", 400, {"error": {"message": "Please reduce the amount of calls", "code": 4}}, times=4)
        assert publish(client, due)["retrying"] == 1
        assert queue(client)[0]["container_id"] == "c1"
        publish(client, due + timedelta(minutes=2))
        assert fake.ops().count("meta.media") == 1 and len(fake.meta.media["ig1"]) == 1
