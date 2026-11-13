"""Settings → Credentials & Integrations in the real dashboard: dynamic provider fields, provider switching without
a reload, secret masking, validation, health, the administrator password and the Meta Page selection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import add_images, jpeg
from tests.conftest import BLOB_ENV
from tests.fakes import BAD_KEY, BLOB_PUBLIC_HOST, FakeUpstream
from tests.test_providers import KEY

OPENAI_KEY = "sk-openai-browser-0123456789abcd"


@pytest.fixture
def admin(serve: Callable[..., str], page: Page, problems: list[str], fake: FakeUpstream) -> Page:
    """The dashboard in Live mode, on a server that can save credentials."""
    page.route(f"https://{BLOB_PUBLIC_HOST}/**", lambda route: route.fulfill(status=404, body=b""))
    page.goto(serve(SETTINGS_ENCRYPTION_KEY=KEY, **BLOB_ENV))
    expect(page.locator("#mode-badge-label")).to_have_text("Live mode")
    return page


def open_integrations(page: Page) -> None:
    page.click("#open-settings")
    page.click("#open-integrations")
    expect(page.locator("#integrations-dialog")).to_be_visible()
    expect(page.locator("#integrations-sections")).to_be_visible()


def field_keys(page: Page, section: str) -> list[str]:
    return page.locator(f"#int-{section} [data-role=fields] [data-key]").evaluate_all("(nodes) => nodes.map((n) => n.dataset.key)")


def test_settings_links_to_credentials_and_health(admin: Page) -> None:
    page = admin
    page.click("#open-settings")
    expect(page.locator("#integrations-summary")).to_contain_text("Groq · ready")
    expect(page.locator("#integrations-summary")).to_contain_text("Pollinations · ready")
    page.click("#open-integrations")
    rows = page.locator("#integrations-health tbody tr")
    expect(rows).to_have_count(4)
    expect(rows.nth(0)).to_contain_text("Text AI")
    expect(rows.nth(0)).to_contain_text("Groq")
    expect(rows.nth(2)).to_contain_text("Vercel Blob")
    expect(page.locator("#int-text-ai-provider")).to_have_value("groq")
    expect(page.locator("#int-text-ai [data-role=badge]")).to_have_text("Not tested")
    expect(page.locator("#int-meta [data-role=provider-field]")).to_be_hidden()  # one provider: no dropdown


def test_fields_follow_the_chosen_provider(admin: Page) -> None:
    page = admin
    open_integrations(page)
    assert field_keys(page, "text-ai") == ["api_key", "default_model", "available_models", "endpoint"]
    page.select_option("#int-text-ai-provider", "azure")
    assert field_keys(page, "text-ai") == ["endpoint", "deployment", "api_version", "api_key"]
    expect(page.locator("#int-text-ai-provider-note")).to_contain_text("Saving makes Azure OpenAI the active provider")
    page.select_option("#int-text-ai-provider", "openai")
    assert field_keys(page, "text-ai") == ["api_key", "organization_id", "default_model", "available_models", "endpoint"]
    expect(page.locator("#int-text-ai [data-action=models]")).to_be_visible()
    expect(page.locator("#int-image-ai [data-action=models]")).to_be_hidden()
    page.select_option("#int-storage-provider", "upload_endpoint")
    assert field_keys(page, "storage") == ["endpoint", "preset"]


def test_secrets_are_masked(admin: Page) -> None:
    page = admin
    open_integrations(page)
    key = page.locator("#int-text-ai-api-key")
    expect(key).to_have_attribute("type", "password")
    expect(key).to_have_value("")
    expect(key).to_have_attribute("placeholder", "*********-key · leave blank to keep")
    expect(page.locator("#int-text-ai-api-key-hint")).to_contain_text("From GROQ_API_KEY")
    assert "test-groq-key" not in page.content()


def test_switching_the_text_provider_without_a_reload(admin: Page, fake: FakeUpstream) -> None:
    page = admin
    open_integrations(page)
    page.select_option("#int-text-ai-provider", "openai")
    page.fill("#int-text-ai-api-key", OPENAI_KEY)
    page.click("#int-text-ai [data-action=save]")
    expect(page.locator("#int-text-ai [data-role=result]")).to_contain_text("OpenAI is now the active Text AI provider")
    expect(page.locator("#int-text-ai-provider")).to_have_value("openai")
    expect(page.locator("#int-text-ai-api-key")).to_have_value("")
    expect(page.locator("#int-text-ai-api-key")).to_have_attribute("placeholder", "sk-o" + "*" * 24 + "abcd · leave blank to keep")
    expect(page.locator("#integrations-health tbody tr").first).to_contain_text("OpenAI")
    assert OPENAI_KEY not in page.content()
    # The dashboard follows at once: header, Settings, and the model the browser sends.
    expect(page.locator("#pill-groq-label")).to_have_text("OpenAI")
    page.click("#done-integrations")
    expect(page.locator("#set-groq-title-text")).to_have_text("Text AI · OpenAI")
    expect(page.locator("#set-model")).to_have_value("gpt-4.1-mini")
    page.click("#cancel-settings")
    add_images(page, ("harbour.jpg", jpeg((40, 90, 160))))
    page.click("#ed-blank-generate")
    expect(page.locator("#op-title")).to_have_text("Content generation complete")
    chat = [c for c in fake.calls if c.op == "openai.chat"]
    assert len(chat) == 1 and chat[0].json["model"] == "gpt-4.1-mini"
    assert chat[0].kwargs["headers"]["Authorization"] == f"Bearer {OPENAI_KEY}"


def test_required_fields_are_checked_before_sending(admin: Page, fake: FakeUpstream) -> None:
    page = admin
    open_integrations(page)
    page.select_option("#int-text-ai-provider", "azure")
    page.click("#int-text-ai [data-action=save]")
    expect(page.locator("#int-text-ai-endpoint-error")).to_have_text("Endpoint is required.")
    expect(page.locator("#int-text-ai-deployment-error")).to_have_text("Deployment name is required.")
    expect(page.locator("#int-text-ai-endpoint")).to_be_focused()
    page.fill("#int-text-ai-endpoint", "http://example.com")
    page.click("#int-text-ai [data-action=save]")
    expect(page.locator("#int-text-ai-endpoint-error")).to_have_text("Endpoint must be a full https:// address.")
    assert not [c for c in fake.calls if c.op.startswith("azure")]
    expect(page.locator("#int-text-ai-provider option:checked")).to_have_text("Azure OpenAI")


def test_test_connection_and_fetch_models(admin: Page, fake: FakeUpstream) -> None:
    page = admin
    open_integrations(page)
    page.click("#int-text-ai [data-action=test]")
    expect(page.locator("#int-text-ai [data-role=result]")).to_contain_text("2 models available, including qwen/qwen3.8-27b")
    expect(page.locator("#int-text-ai [data-role=badge]")).to_have_text("Connected")
    expect(page.locator("#int-text-ai [data-role=times]")).to_contain_text("Last successful connection")
    expect(page.locator("#integrations-health tbody tr").first).to_contain_text("Connected")
    page.click("#int-text-ai [data-action=models]")
    expect(page.locator("#int-text-ai [data-role=result]")).to_contain_text("2 models available")
    expect(page.locator("#int-text-ai-default-model-options option")).to_have_count(2)


def test_a_failed_test_is_shown_and_unsaved_settings_are_not_kept(admin: Page) -> None:
    page = admin
    open_integrations(page)
    page.select_option("#int-image-ai-provider", "stability")
    page.fill("#int-image-ai-api-key", BAD_KEY)
    page.click("#int-image-ai [data-action=test]")
    expect(page.locator("#int-image-ai [data-role=result]")).to_contain_text("HTTP 401")
    expect(page.locator("#integrations-footer")).to_contain_text("Unsaved changes: Image AI")
    page.click("#done-integrations")
    expect(page.locator("#confirm-dialog")).to_be_visible()
    page.locator("#confirm-actions button", has_text="Discard").click()
    expect(page.locator("#integrations-dialog")).to_be_hidden()


def test_meta_pages_can_be_chosen(admin: Page, served: list[Any]) -> None:
    page = admin
    open_integrations(page)
    expect(page.locator("#int-meta [data-role=pages]")).to_be_hidden()
    page.click("#int-meta [data-action=test]")
    expect(page.locator("#int-meta [data-role=result]")).to_contain_text("2 Facebook Pages and 1 Instagram accounts")
    pages = page.locator("#int-meta [data-role=page-list] label.channel")
    expect(pages).to_have_count(2)
    expect(page.locator("#int-meta [data-role=accounts]")).to_contain_text("@poster.studio")
    expect(page.locator("#int-meta [data-role=pages-count]")).to_have_text("All Pages")
    pages.first.locator("input").check()
    expect(page.locator("#int-meta [data-role=pages-count]")).to_have_text("1 chosen")
    page.click("#int-meta [data-action=save]")
    expect(page.locator("#int-meta [data-role=result]")).to_contain_text("Saved")
    [app] = served
    assert app.extensions["poster"].meta().page_ids == frozenset({"pg1"})


def test_without_an_encryption_key_settings_are_read_only(live: Page) -> None:
    page = live
    open_integrations(page)
    expect(page.locator("#integrations-notice")).to_contain_text("Credentials are read-only here")
    expect(page.locator("#int-text-ai [data-action=save]")).to_be_disabled()
    expect(page.locator("#int-text-ai [data-action=test]")).to_be_enabled()
    expect(page.locator("#int-text-ai-default-model")).to_have_attribute("readonly", "")


def test_the_administrator_password(serve: Callable[..., str], page: Page, problems: list[str]) -> None:
    page.goto(serve(SETTINGS_ENCRYPTION_KEY=KEY, POSTER_ADMIN_PASSWORD="admin-pass-123"))
    page.click("#open-settings")
    page.click("#open-integrations")
    expect(page.locator("#integrations-admin")).to_be_visible()
    expect(page.locator("#integrations-sections")).to_be_hidden()
    expect(page.locator("#integrations-admin-password")).to_be_focused()
    page.fill("#integrations-admin-password", "wrong")
    page.click("#integrations-admin-unlock")
    expect(page.locator("#integrations-admin-password-error")).to_have_text("The administrator password is not correct.")
    page.fill("#integrations-admin-password", "admin-pass-123")
    page.click("#integrations-admin-unlock")
    expect(page.locator("#integrations-sections")).to_be_visible()
    expect(page.locator("#integrations-admin")).to_be_hidden()
    assert page.evaluate("() => localStorage.length === 0 || !JSON.stringify(localStorage).includes('admin-pass-123')")


def test_switching_the_image_provider(admin: Page, fake: FakeUpstream) -> None:
    page = admin
    open_integrations(page)
    page.select_option("#int-image-ai-provider", "flux")
    page.fill("#int-image-ai-api-key", "bfl-browser-key-0123456789")
    page.click("#int-image-ai [data-action=save]")
    expect(page.locator("#int-image-ai [data-role=result]")).to_contain_text("Flux is now the active Image AI provider")
    page.click("#done-integrations")
    page.click("#cancel-settings")
    page.click('label:has(input[name="image-source"][value="generate"])')
    expect(page.locator("#imggen-status")).to_contain_text("Images are created by Flux")
    page.fill("#imggen-prompt", "A lighthouse at dusk")
    page.click("#imggen-button")
    expect(page.locator("#gallery .thumb-name")).to_have_text(["ai-a-lighthouse-at-dusk.png"])
    assert "bfl.submit" in fake.ops() and "pollinations" not in fake.ops()
