"""The dashboard in Live mode, against a real Poster server whose upstream services are fakes."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from playwright.sync_api import Browser, Page, Route, expect

from poster.retry import RetryLog
from tests.browser.conftest import add_images, confirm, jpeg, local_input, queued, toast
from tests.fakes import DEFAULT_REPLY, FakeUpstream, image_bytes


def connect_social(page: Page) -> None:
    page.click("#open-settings")
    page.click("#connect-social")
    expect(page.locator("#social-test-result")).to_contain_text("Connected as Poster System User")
    expect(page.locator("#channel-list label.channel")).to_have_count(3)
    # The Page the token cannot post to is listed, but cannot be chosen, and says why.
    expect(page.locator("#channel-list label.channel.is-disabled")).to_contain_text("cannot create content on Read-only Page")
    expect(page.locator("#channel-count")).to_have_text("2 selected")
    page.click("#save-settings")
    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(page.locator("#pill-social-detail")).to_have_text("2 channels")


def test_connect_generate_schedule_edit_delete_publish(live: Page, fake: FakeUpstream, served: list[Any]) -> None:
    page = live
    [app] = served
    expect(page.locator("#pill-social-detail")).to_have_text("Not connected")
    connect_social(page)

    add_images(page, ("harbour.jpg", jpeg((40, 90, 160))))
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    expect(page.locator("#ed-description")).to_have_value(DEFAULT_REPLY["description"])
    expect(page.locator("#ed-tags .tag")).to_have_count(11)
    expect(page.locator("#ed-category")).to_have_value("Travel")

    page.fill("#ed-schedule", local_input(2))
    page.click("#ed-schedule-post")
    confirm(page, "Schedule 1 post")
    expect(page.locator("#op-title")).to_have_text("Scheduling complete")
    expect(page.locator("#sched-list > li")).to_have_count(1)
    expect(page.locator("#sched-list .sched-channel")).to_have_count(2)
    expect(page.locator("#ed-result-title")).to_contain_text("Scheduled")
    expect(page.locator("#ed-result-edit")).to_be_enabled()
    expect(page.locator("#ed-result-edit")).to_have_text("Edit scheduled post")
    expect(page.locator("#ed-description")).not_to_be_editable()

    posts = queued(app)
    assert len(posts) == 2 and {p["channel_id"] for p in posts} == {"pg1", "ig1"}
    assert {p["status"] for p in posts} == {"scheduled"}
    image = posts[0]["media_url"]
    assert image.startswith("https://poster.test/api/media/image/harbour-") and image.endswith(".jpg")
    assert posts[0]["text"].startswith(DEFAULT_REPLY["description"]) and "#beach" in posts[0]["text"]
    assert fake.ops("blob.") == ["blob.put", "blob.put"]
    assert not fake.meta.all_posts()  # nothing is published before its time

    # Edit in place: the editor saves the caption straight to every channel copy in the queue.
    page.click("#ed-result-edit")
    expect(page.locator("#ed-description")).to_be_focused()
    page.fill("#ed-description", "Harbour lights, edited in place")
    page.click("#ed-social-save")
    toast(page, "Saved on 2 channels.")
    expect(page.locator("#ed-description")).not_to_be_editable()
    expect(page.locator("#ed-description")).to_have_value("Harbour lights, edited in place")
    assert all(p["text"].startswith("Harbour lights, edited in place") for p in queued(app))

    # Edit in Scheduled posts opens a post from this batch in the editor, not in a second, different editor.
    page.locator("#sched-list [data-action=edit]").first.click()
    expect(page.locator("#ed-description")).to_be_focused()
    page.fill("#ed-schedule", local_input(3, 15))
    page.click("#ed-social-save")
    expect(page.locator("#ed-result-edit")).to_be_focused()  # the save is done (the earlier toast may still show)
    assert all(p["due_at"].endswith("T15:00:00Z") for p in queued(app))

    # Once its image has left the gallery, the post is edited in the dialog.
    page.locator("#gallery .thumb-card").first.locator("[data-action=remove]").click()
    page.locator("#sched-list [data-action=edit]").first.click()
    expect(page.locator("#post-dialog")).to_be_visible()
    page.fill("#post-due", local_input(3, 16))
    expect(page.locator("#post-due-hint")).to_contain_text(" · ")
    page.click("#post-save")
    toast(page, "The scheduled post was updated.")
    assert any(p["due_at"].endswith("T16:00:00Z") for p in queued(app))

    # Delete only one channel's copy, from its line in the list: the post stays, on the other channel.
    page.locator("#sched-list [data-action=delete-one]").first.click()
    confirm(page, "copy")
    toast(page, "copy deleted;")
    expect(page.locator("#sched-list > li")).to_have_count(1)
    expect(page.locator("#sched-list .sched-channel")).to_have_count(1)
    expect(page.locator("#sched-list [data-action=delete-one]")).to_have_count(0)
    [left] = queued(app)

    # The publisher publishes it once its time has come; the list then shows it as published, with its link.
    due = datetime.fromisoformat(left["due_at"].replace("Z", "+00:00"))
    summary = app.extensions["poster"].social.publish_due(RetryLog("test"), now=due + timedelta(minutes=1))
    assert summary["published"] == 1
    assert len(fake.meta.all_posts()) == 1
    page.click("#sched-refresh")
    expect(page.locator("#sched-list .chip", has_text="Published")).to_have_count(1)
    expect(page.locator("#sched-list a[aria-label^='Open the published']")).to_have_count(1)
    expect(page.locator("#sched-list [data-action=edit]")).to_have_count(0)
    expect(page.locator("#sched-source")).to_contain_text("Publisher last ran")


def test_overdue_posts_warn_when_the_publisher_is_not_running(live: Page, served: list[Any]) -> None:
    page = live
    [app] = served
    store = app.extensions["poster"].social.store
    past = (datetime.now(UTC) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.insert([{"id": "late1", "request_key": "k-late", "channel_id": "pg1", "service": "facebook",
                   "channel_name": "Poster Studio", "text": "Running late", "media_url": None, "due_at": past,
                   "status": "scheduled", "created_at": past}])
    page.click("#sched-refresh")
    expect(page.locator("#sched-list .chip", has_text="Waiting to publish")).to_have_count(1)
    alert = page.locator("#sched-publisher-alert")
    expect(alert).to_be_visible()
    expect(alert).to_contain_text("1 post is past its publish time")
    expect(alert).to_contain_text("/api/publish/due")

    # Once the publisher has run, the warning goes: it is working, and will get to the post.
    store.set_state("publisher_last_run", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    page.click("#sched-refresh")
    expect(page.locator("#sched-source")).to_contain_text("Publisher last ran")
    expect(alert).to_be_hidden()


def test_a_refusal_says_why_in_settings(live: Page) -> None:
    page = live
    add_images(page, ("pier.jpg", jpeg((90, 40, 160))))
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    page.fill("#ed-schedule", local_input(2))
    page.click("#ed-schedule-post")
    # No channel is chosen yet: the reason is in the drawer it opens, under Facebook & Instagram, not in a toast behind it.
    expect(page.locator("#settings-dialog")).to_be_visible()
    reason = page.locator("#settings-social #settings-reason")
    expect(reason).to_be_visible()
    expect(reason).to_contain_text("No channels selected")
    expect(reason).to_be_focused()


def test_auth_failure_stops_generation_with_a_banner(live: Page, fake: FakeUpstream) -> None:
    page = live
    fake.fail("groq.chat", 401, {"error": "invalid key"})
    add_images(page, ("one.jpg", jpeg((1, 2, 3))), ("two.jpg", jpeg((200, 2, 3))))
    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Stopped after an error")
    expect(page.locator("#banners .banner-error")).to_contain_text("Authentication failed")
    expect(page.locator("#pill-groq-detail")).to_have_text("Error")


def test_image_generation_and_connection_test(live: Page, fake: FakeUpstream) -> None:
    page = live
    page.click('label:has(input[name="image-source"][value="generate"])')
    page.fill("#imggen-prompt", "A lighthouse in a storm")
    page.click("#imggen-button")
    expect(page.locator("#gallery .thumb-name")).to_have_text(["ai-a-lighthouse-in-a-storm.png"])
    assert "pollinations" in fake.ops()

    page.click("#open-settings")
    page.click("#test-groq")
    expect(page.locator("#groq-test-result")).to_contain_text("Connected to groq.test. 2 models available")
    expect(page.locator("#pill-groq-detail")).to_have_text("Connected")


def test_image_edit(live: Page, fake: FakeUpstream) -> None:
    page = live
    page.click('label:has(input[name="image-source"][value="edit"])')
    expect(page.locator("#imgedit-status")).to_contain_text("Pollinations (kontext)")
    page.set_input_files("#imgedit-input", files=[{"name": "pier.png", "mimeType": "image/png",
                                                    "buffer": image_bytes("PNG", (300, 200), (90, 40, 160))}])
    page.fill("#imgedit-prompt", "Add a red sailing boat")
    page.keyboard.press("Control+Enter")
    expect(page.locator("#gallery .thumb-name")).to_have_text(["ai-edit-add-a-red-sailing-boat.png"])
    [call] = [c for c in fake.calls if c.op == "pollinations.edit"]
    assert call.json["prompt"] == "Add a red sailing boat" and call.json["model"] == "kontext"
    assert call.json["image"][0]["image_url"].startswith("data:image/jpeg;base64,")

    fake.fail("pollinations.edit", 400, {"error": {"message": "content_blocked"}})
    page.click("#imgedit-button")
    expect(page.locator("#imgedit-error")).to_contain_text("Pollinations rejected the request (HTTP 400)")
    expect(page.locator("#gallery .thumb-card")).to_have_count(1)


def test_a_lost_answer_does_not_schedule_twice(live: Page, served: list[Any], problems: list[str]) -> None:
    page = live
    [app] = served
    connect_social(page)
    add_images(page, ("pier.jpg", jpeg((90, 40, 160))))
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")

    # The server queues the post, but its answer never reaches the browser.
    keys: list[str] = []

    def lose_first_answer(route: Route) -> None:
        keys.append(json.loads(route.request.post_data or "{}")["requestKey"])
        if len(keys) == 1:
            route.fetch()
            route.abort()
        else:
            route.continue_()

    page.route("**/api/social/schedule", lose_first_answer)
    page.fill("#ed-schedule", local_input(2))
    page.click("#ed-schedule-post")
    confirm(page, "Schedule 1 post")
    expect(page.locator("#op-title")).to_have_text("Scheduling complete")
    expect(page.locator("#gallery .thumb-status")).to_have_text("Scheduled")
    expect(page.locator("#log-list")).to_contain_text("Sending the same request again")
    # The browser sent the same request twice, with the same key, and the server queued the post once.
    assert len(keys) == 2 and keys[0] == keys[1]
    assert len(queued(app)) == 2
    # Chromium logs the answer this test threw away; that one failure is the point of the test.
    problems[:] = [p for p in problems if "net::ERR_FAILED" not in p]


def test_password_protected_dashboard(serve: Callable[..., str], browser: Browser, fake: FakeUpstream) -> None:
    url = serve(POSTER_PASSWORD="s3cret-pass")
    anonymous = browser.new_context()  # a headless browser cannot answer the login prompt, so ask directly
    refused = anonymous.request.get(url)
    assert refused.status == 401 and refused.headers["www-authenticate"].startswith("Basic")
    anonymous.close()

    context = browser.new_context(http_credentials={"username": "me", "password": "s3cret-pass"}, timezone_id="UTC")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    expect(page.locator("#mode-badge-label")).to_have_text("Live mode")
    add_images(page, ("locked.jpg", jpeg((5, 50, 5))))
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    context.close()
    assert not errors
    assert "groq.chat" in fake.ops()


def test_caption_language_reaches_the_model(live: Page, fake: FakeUpstream) -> None:
    page = live
    add_images(page, ("market.jpg", jpeg((150, 60, 20))))
    page.select_option("#ed-language", "bn-en")
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    [chat] = [c for c in fake.calls if c.op == "groq.chat"]
    assert "Bengali in Bangla script, mixed naturally with English" in chat.json["messages"][1]["content"][0]["text"]
