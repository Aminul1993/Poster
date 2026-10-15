"""The dashboard in Live mode, against a real Poster server whose upstream services are fakes."""

from __future__ import annotations

from collections.abc import Callable

import requests
from playwright.sync_api import Browser, Page, expect

from tests.browser.conftest import add_images, confirm, jpeg, local_input, toast
from tests.fakes import DEFAULT_REPLY, FakeUpstream


def connect_buffer(page: Page) -> None:
    page.click("#open-settings")
    page.click("#connect-buffer")
    expect(page.locator("#buffer-test-result")).to_contain_text("Connected as owner@example.test")
    expect(page.locator("#channel-list label.channel")).to_have_count(3)
    expect(page.locator("#channel-count")).to_have_text("2 selected")
    page.click("#save-settings")
    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(page.locator("#pill-buffer-detail")).to_have_text("2 channels")


def test_connect_generate_schedule_edit_delete(live: Page, fake: FakeUpstream) -> None:
    page = live
    expect(page.locator("#pill-buffer-detail")).to_have_text("Not connected")
    connect_buffer(page)

    add_images(page, ("harbour.jpg", jpeg((40, 90, 160))))
    page.click("#gen-active")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    expect(page.locator("#ed-description")).to_have_value(DEFAULT_REPLY["description"])
    expect(page.locator("#ed-tags .tag")).to_have_count(11)
    expect(page.locator("#ed-category")).to_have_value("Travel")

    page.fill("#ed-schedule", local_input(2))
    page.click("#ed-schedule-post")
    confirm(page, "Schedule 1 post")
    expect(page.locator("#op-title")).to_have_text("Scheduling complete")
    expect(page.locator("#sched-list > li")).to_have_count(2)
    expect(page.locator("#ed-result-title")).to_contain_text("Scheduled in Buffer")

    posts = list(fake.buffer.posts.values())
    assert len(posts) == 2 and {p["channelId"] for p in posts} == {"ch1", "ch2"}
    image = posts[0]["assets"][0]["source"]
    assert image.startswith("https://poster.test/api/media/image/harbour-") and image.endswith(".jpg")
    assert posts[0]["text"].startswith(DEFAULT_REPLY["description"]) and "#beach" in posts[0]["text"]
    assert fake.ops("blob.") == ["blob.put", "blob.put"]

    page.locator("#sched-list [data-action=edit]").first.click()
    page.fill("#post-due", local_input(3, 15))
    page.click("#post-save")
    toast(page, "The post was updated in Buffer.")
    assert any(p["dueAt"].endswith("T15:00:00Z") for p in fake.buffer.posts.values())

    page.locator("#sched-list [data-action=delete-post]").first.click()
    confirm(page, "Delete post")
    toast(page, "The post was deleted from Buffer.")
    expect(page.locator("#sched-list > li")).to_have_count(1)
    assert len(fake.buffer.posts) == 1


def test_auth_failure_stops_generation_with_a_banner(live: Page, fake: FakeUpstream) -> None:
    page = live
    fake.fail("ollama.chat", 401, {"error": "invalid key"})
    add_images(page, ("one.jpg", jpeg((1, 2, 3))), ("two.jpg", jpeg((200, 2, 3))))
    page.click("#gen-all")
    expect(page.locator("#op-title")).to_have_text("Stopped after an error")
    expect(page.locator("#banners .banner-error")).to_contain_text("Authentication failed")
    expect(page.locator("#pill-ollama-detail")).to_have_text("Error")


def test_image_generation_and_connection_test(live: Page, fake: FakeUpstream) -> None:
    page = live
    page.click('label:has(input[name="image-source"][value="generate"])')
    page.fill("#imggen-prompt", "A lighthouse in a storm")
    page.click("#imggen-button")
    expect(page.locator("#gallery .thumb-name")).to_have_text(["ai-a-lighthouse-in-a-storm.png"])
    assert "pollinations" in fake.ops()

    page.click("#open-settings")
    page.click("#test-ollama")
    expect(page.locator("#ollama-test-result")).to_contain_text("Connected to ollama.test. 2 models available")
    expect(page.locator("#pill-ollama-detail")).to_have_text("Connected")


def test_a_lost_answer_does_not_schedule_twice(live: Page, fake: FakeUpstream) -> None:
    page = live
    connect_buffer(page)
    add_images(page, ("pier.jpg", jpeg((90, 40, 160))))
    page.click("#gen-active")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")

    # Buffer creates the post on the first channel, but its answer never arrives.
    fake.fail("buffer.PosterCreatePost", exc=requests.ReadTimeout("lost"), after=True)
    page.fill("#ed-schedule", local_input(2))
    page.click("#ed-schedule-post")
    confirm(page, "Schedule 1 post")
    expect(page.locator("#op-title")).to_have_text("Scheduling complete")
    expect(page.locator("#gallery .thumb-status")).to_have_text("Scheduled")
    expect(page.locator("#log-list")).to_contain_text("so it counts as scheduled")
    assert len(fake.buffer.posts) == 2


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
    page.click("#gen-active")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    context.close()
    assert not errors
    assert "ollama.chat" in fake.ops()
