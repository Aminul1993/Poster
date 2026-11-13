"""The provider layer: the registries, the factories, each provider's API calls, and switching providers at runtime
through Credentials & Integrations while the app keeps running."""

from __future__ import annotations

import base64
import json
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

import pytest

from poster.config import Config
from poster.crypto import generate_key
from poster.errors import ServiceError
from poster.http import HttpClient
from poster.retry import RetryCallback, RetryLog
from poster.services import Field
from poster.services.ai import TEXT_AI_PROVIDERS, TextAIProvider
from poster.services.ai.text_provider import Message, OpenAICompatibleProvider
from poster.services.imaging import IMAGE_AI_PROVIDERS
from tests.conftest import BASE_ENV, post
from tests.fakes import AZURE_HOST, BAD_KEY, BFL_HOST, DEFAULT_REPLY, FakeUpstream, image_bytes
from tests.test_routes import b64, generate_body

KEY = generate_key()

# Settings that configure each text AI provider, and the call it makes to write a post.
TEXT_CASES: dict[str, tuple[dict[str, str], str]] = {
    "groq": ({"api_key": "gsk-switched-groq-key-123456"}, "groq.chat"),
    "openai": ({"api_key": "sk-openai-test-key-123456789"}, "openai.chat"),
    "anthropic": ({"api_key": "sk-ant-test-key-1234567890"}, "anthropic.messages"),
    "gemini": ({"api_key": "AIza-test-key-123456789012"}, "gemini.generate"),
    "azure": ({"api_key": "azure-test-key-1234567890", "endpoint": f"https://{AZURE_HOST}", "deployment": "captions"},
              "azure.chat"),
}
# The same for each image AI provider: its settings, and the calls a generation and an edit make.
IMAGE_CASES: dict[str, tuple[dict[str, str], list[str], list[str]]] = {
    "pollinations": ({"api_key": "pk-switched-key-1234567890"}, ["pollinations"], ["pollinations.edit"]),
    "openai": ({"api_key": "sk-openai-test-key-123456789"}, ["openai.images"], ["openai.edit"]),
    "stability": ({"api_key": "sk-stability-key-1234567890"}, ["stability.generate"], ["stability.generate"]),
    "flux": ({"api_key": "bfl-test-key-12345678901234"}, ["bfl.submit", "bfl.poll", "bfl.poll", "bfl.download"],
             ["bfl.submit", "bfl.poll", "bfl.poll", "bfl.download"]),
    "gemini": ({"api_key": "AIza-test-key-123456789012"}, ["gemini.image"], ["gemini.image"]),
}


@pytest.fixture
def admin(make_app: Any) -> Any:
    """An app that can save credentials (SETTINGS_ENCRYPTION_KEY is set)."""
    return make_app(SETTINGS_ENCRYPTION_KEY=KEY).test_client()


def save(client: Any, slug: str, provider: str, config: dict[str, Any] | None = None, **extra: Any) -> Any:
    return post(client, f"/api/settings/integrations/{slug}", {"provider": provider, "config": config or {}, **extra})


def config() -> Config:
    return Config.from_env(BASE_ENV, env_file=None)


# =============================================================================
# Registry and factories
# =============================================================================

class TestRegistry:
    def test_text_providers(self) -> None:
        assert list(TEXT_AI_PROVIDERS) == ["groq", "openai", "anthropic", "gemini", "azure"]

    def test_image_providers(self) -> None:
        assert list(IMAGE_AI_PROVIDERS) == ["pollinations", "openai", "stability", "flux", "gemini"]

    @pytest.mark.parametrize("cls", [*TEXT_AI_PROVIDERS.values(), *IMAGE_AI_PROVIDERS.values()])
    def test_every_provider_describes_itself(self, cls: Any) -> None:
        registry = TEXT_AI_PROVIDERS if issubclass(cls, TextAIProvider) else IMAGE_AI_PROVIDERS
        assert registry[cls.name] is cls and cls.label and cls.service
        api_key = cls.field("api_key")
        assert api_key is not None and api_key.secret and api_key.required
        assert json.loads(json.dumps(cls.describe()))["fields"][0]["key"]
        # Without settings, a provider is unconfigured and says which field is missing, rather than failing.
        provider = cls(HttpClient(), config(), {})
        assert not provider.configured and provider.problem

    @pytest.mark.parametrize("cls", [*TEXT_AI_PROVIDERS.values(), *IMAGE_AI_PROVIDERS.values()])
    def test_every_provider_reports_its_public_info_without_secrets(self, cls: Any) -> None:
        values = {f.key: "secret-value-1234567890" if f.secret else (f.default or "value") for f in cls.fields}
        values.update({k: v for k, v in (("endpoint", "https://api.example.com"),) if cls.field(k)})
        info = cls(HttpClient(), config(), values).public_info()
        assert info["provider"] == cls.name and "secret-value" not in json.dumps(info)


class TestFactories:
    def test_environment_only_resolves_the_original_providers(self, client: Any) -> None:
        services = client.application.extensions["poster"]
        assert services.text_ai.get_active_provider().name == "groq"
        assert services.image_ai.get_active_provider().name == "pollinations"

    def test_the_environment_can_choose_a_provider(self, make_app: Any) -> None:
        app = make_app(TEXT_AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-from-env-123456789",
                       IMAGE_AI_PROVIDER="stability", STABILITY_API_KEY="sk-stab-from-env-12345678")
        services = app.extensions["poster"]
        text, image = services.text_ai.get_active_provider(), services.image_ai.get_active_provider()
        assert (text.name, text.configured, text.api_key) == ("anthropic", True, "sk-ant-from-env-123456789")
        assert (image.name, image.configured) == ("stability", True)

    def test_an_unknown_environment_choice_falls_back(self, make_app: Any) -> None:
        assert make_app(TEXT_AI_PROVIDER="nope").extensions["poster"].text_ai.get_active_provider().name == "groq"

    def test_the_active_provider_is_reused_until_settings_change(self, admin: Any) -> None:
        factory = admin.application.extensions["poster"].text_ai
        first = factory.get_active_provider()
        assert factory.get_active_provider() is first
        save(admin, "text-ai", "groq", {"default_model": "qwen/qwen3-32b"})
        second = factory.get_active_provider()
        assert second is not first and second.default_model == "qwen/qwen3-32b"

    def test_unknown_provider(self, client: Any) -> None:
        with pytest.raises(ServiceError) as caught:
            client.application.extensions["poster"].text_ai.build("nope")
        assert caught.value.kind == "validation"

    def test_old_names_still_work(self) -> None:
        from poster.services.groq import GenerationOptions, GroqService
        from poster.services.imagegen import ImageGenService
        groq = GroqService(HttpClient(), config())
        assert groq.configured and groq.default_model == "qwen/qwen3.8-27b" and GenerationOptions
        assert ImageGenService(HttpClient(), config()).configured


class EchoProvider(TextAIProvider):
    """A provider written for this test only: registering it is all it takes to use it."""

    name = "echo"
    label = "Echo"
    service = "Echo"
    default_model_name = "echo-1"
    fields = (Field("api_key", "API key", "secret", required=True),)
    seen: ClassVar[list[str]] = []

    def complete(self, messages: list[Message], model: str, on_retry: RetryCallback) -> str:
        self.seen.append(model)
        return json.dumps({**DEFAULT_REPLY, "description": f"Written by {model}"})

    def fetch_models(self, on_retry: RetryCallback) -> list[str]:
        return ["echo-1", "echo-2"]


def test_a_new_provider_needs_only_a_class_and_its_registration(admin: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TEXT_AI_PROVIDERS, "echo", EchoProvider)
    listed = admin.get("/api/settings/integrations").get_json()["integrations"]["text_ai"]["providers"]
    assert "echo" in [p["name"] for p in listed]
    assert save(admin, "text-ai", "echo", {"api_key": "echo-key-1234567890"}).status_code == 200
    data = post(admin, "/api/groq/generate", generate_body(model="echo-1")).get_json()
    assert data["content"]["description"] == "Written by echo-1" and EchoProvider.seen == ["echo-1"]
    assert post(admin, "/api/settings/test/text-ai", {}).get_json()["modelAvailable"] is True


# =============================================================================
# Text AI providers' API calls
# =============================================================================

def generate_with(admin: Any, fake: FakeUpstream, provider: str, **body: Any) -> dict[str, Any]:
    settings, _ = TEXT_CASES[provider]
    assert save(admin, "text-ai", provider, settings).status_code == 200
    response = post(admin, "/api/groq/generate", generate_body(model=None, **body))
    assert response.status_code == 200, response.get_json()
    return dict(response.get_json())


class TestTextProviders:
    def test_openai(self, admin: Any, fake: FakeUpstream) -> None:
        data = generate_with(admin, fake, "openai")
        [call] = fake.calls
        assert call.url == "https://api.openai.com/v1/chat/completions"
        assert call.kwargs["headers"]["Authorization"] == "Bearer sk-openai-test-key-123456789"
        assert call.json["model"] == "gpt-4.1-mini" and "temperature" not in call.json
        assert call.json["response_format"] == {"type": "json_object"}
        assert call.json["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert data["content"]["category"] == "Travel" and data["model"] == "gpt-4.1-mini"

    def test_openai_organization(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "text-ai", "openai", {"api_key": "sk-openai-test-key-123456789", "organization_id": "org-123"})
        post(admin, "/api/groq/generate", generate_body(model=None))
        assert fake.calls[0].kwargs["headers"]["OpenAI-Organization"] == "org-123"

    def test_anthropic(self, admin: Any, fake: FakeUpstream) -> None:
        data = generate_with(admin, fake, "anthropic", image=b64(image_bytes("PNG")))
        [call] = fake.calls
        assert call.url == "https://api.anthropic.com/v1/messages"
        headers = call.kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-test-key-1234567890" and headers["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in headers
        assert call.json["model"] == "claude-sonnet-5-5" and call.json["max_tokens"] == 2048
        assert "expert social media strategist" in call.json["system"]
        image, text = call.json["messages"][0]["content"]
        assert image["source"]["media_type"] == "image/png" and "warm, conversational" in text["text"]
        assert data["content"]["engagement_score"] == 82

    def test_gemini(self, admin: Any, fake: FakeUpstream) -> None:
        generate_with(admin, fake, "gemini")
        [call] = fake.calls
        assert call.url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
        assert call.kwargs["headers"]["x-goog-api-key"] == "AIza-test-key-123456789012"
        assert "key=" not in call.url  # never in a URL, which could be logged
        assert call.json["generationConfig"]["responseMimeType"] == "application/json"
        parts = call.json["contents"][0]["parts"]
        assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"

    def test_azure(self, admin: Any, fake: FakeUpstream) -> None:
        data = generate_with(admin, fake, "azure")
        [call] = fake.calls
        url = urlparse(call.url)
        assert url.path == "/openai/deployments/captions/chat/completions"
        assert parse_qs(url.query) == {"api-version": ["2024-10-21"]}
        assert call.kwargs["headers"]["api-key"] == "azure-test-key-1234567890" and "model" not in call.json
        assert data["model"] == "captions"

    @pytest.mark.parametrize("provider", ["anthropic", "gemini"])
    def test_repair_turn_in_each_api_shape(self, admin: Any, fake: FakeUpstream, provider: str) -> None:
        fake.reply("not json at all", json.dumps({"description": "Fixed caption for the image", "hashtags": []}))
        data = generate_with(admin, fake, provider)
        assert data["repaired"] is True and data["content"]["description"] == "Fixed caption for the image"
        second = fake.calls[1].json
        turns = second["messages"] if provider == "anthropic" else second["contents"]
        assert [t["role"] for t in turns] == ["user", "assistant" if provider == "anthropic" else "model", "user"]

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
    def test_lists_models(self, admin: Any, fake: FakeUpstream, provider: str) -> None:
        save(admin, "text-ai", provider, TEXT_CASES[provider][0])
        data = admin.get("/api/settings/text-ai/models").get_json()
        assert data["provider"] == provider and data["models"] == fake.provider_models[provider]

    def test_gemini_lists_only_models_that_write(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "text-ai", "gemini", TEXT_CASES["gemini"][0])
        assert admin.get("/api/settings/text-ai/models").get_json()["models"] == ["gemini-2.5-flash", "gemini-2.5-flash-image"]
        assert fake.calls[0].kwargs["params"] == {"pageSize": 200}

    def test_azure_test_asks_the_deployment(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "text-ai", "azure", TEXT_CASES["azure"][0])
        data = post(admin, "/api/settings/test/text-ai", {}).get_json()
        assert data["ok"] is True and data["models"] == ["captions"]
        assert fake.calls[0].json["max_completion_tokens"] == 16

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "azure"])
    def test_a_refused_key_is_an_auth_error(self, admin: Any, fake: FakeUpstream, provider: str) -> None:
        save(admin, "text-ai", provider, {**TEXT_CASES[provider][0], "api_key": BAD_KEY})
        error = post(admin, "/api/groq/generate", generate_body(model=None)).get_json()["error"]
        assert error["kind"] == "auth" and error["status"] == 401
        assert len(fake.calls) == 1  # never retried

    def test_empty_reply(self, admin: Any, fake: FakeUpstream) -> None:
        fake.reply("   ")
        save(admin, "text-ai", "anthropic", TEXT_CASES["anthropic"][0])
        error = post(admin, "/api/groq/generate", generate_body(model=None)).get_json()["error"]
        assert error["kind"] == "parse" and "Anthropic returned an empty reply" in error["message"]

    def test_server_errors_are_retried_for_every_provider(self, admin: Any, fake: FakeUpstream, waits: list[float]) -> None:
        save(admin, "text-ai", "openai", TEXT_CASES["openai"][0])
        fake.fail("openai.chat", 503, times=2)
        data = post(admin, "/api/groq/generate", generate_body(model=None)).get_json()
        assert len(data["retries"]) == 2 and waits == [1, 2]


class TestUnitWireFormats:
    def test_openai_compatible_messages(self) -> None:
        wire = OpenAICompatibleProvider.wire_messages([Message("user", "hi", "QUJD"), Message("assistant", "raw")])
        assert wire[0]["role"] == "system" and wire[2] == {"role": "assistant", "content": "raw"}
        assert wire[1]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


# =============================================================================
# Image AI providers' API calls
# =============================================================================

class TestImageProviders:
    @pytest.mark.parametrize("provider", list(IMAGE_CASES))
    def test_generates_and_edits(self, admin: Any, fake: FakeUpstream, provider: str) -> None:
        settings, generate_ops, edit_ops = IMAGE_CASES[provider]
        assert save(admin, "image-ai", provider, settings).status_code == 200
        generated = post(admin, "/api/image/generate", {"prompt": "A red fox in snow", "aspect": "portrait"})
        assert generated.status_code == 200, generated.get_json()
        data = generated.get_json()
        assert base64.b64decode(data["image"]) == fake.generated_image and data["mimeType"] == "image/png"
        assert fake.ops() == generate_ops
        fake.calls.clear()
        edited = post(admin, "/api/image/edit", {"prompt": "Make the sky pink", "image": b64(image_bytes())}).get_json()
        assert base64.b64decode(edited["image"]) == fake.edited_image and edited["model"]
        assert fake.ops() == edit_ops

    def test_openai_images_requests(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "image-ai", "openai", IMAGE_CASES["openai"][0])
        post(admin, "/api/image/generate", {"prompt": "A red fox", "aspect": "landscape"})
        body = fake.calls[0].json
        assert body == {"model": "gpt-image-1", "prompt": "A red fox", "n": 1, "size": "1536x1024"}
        post(admin, "/api/image/edit", {"prompt": "Add a hat", "image": b64(image_bytes())})
        edit = fake.calls[1].kwargs
        assert edit["files"]["image"][2] == "image/jpeg" and edit["data"]["model"] == "gpt-image-1"

    def test_stability_requests(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "image-ai", "stability", IMAGE_CASES["stability"][0])
        post(admin, "/api/image/generate", {"prompt": "A red fox", "aspect": "story"})
        call = fake.calls[0]
        assert call.url.endswith("/v2beta/stable-image/generate/core") and call.kwargs["headers"]["Accept"] == "image/*"
        assert call.kwargs["data"]["aspect_ratio"] == "9:16" and "model" not in call.kwargs["data"]
        post(admin, "/api/image/edit", {"prompt": "Add a hat", "image": b64(image_bytes())})
        edit = fake.calls[1]
        assert edit.url.endswith("/generate/sd3") and edit.kwargs["data"]["mode"] == "image-to-image"
        assert edit.kwargs["data"]["model"] == "sd3.5-large"

    def test_flux_polls_until_ready(self, admin: Any, fake: FakeUpstream, waits: list[float]) -> None:
        save(admin, "image-ai", "flux", IMAGE_CASES["flux"][0])
        fake.flux_pending = 3
        post(admin, "/api/image/generate", {"prompt": "A red fox", "aspect": "landscape"})
        submit = fake.calls[0]
        assert submit.url == f"https://{BFL_HOST}/v1/flux-pro-1.1" and submit.kwargs["headers"]["x-key"]
        assert (submit.json["width"], submit.json["height"]) == (1344, 768)
        assert fake.ops().count("bfl.poll") == 4 and waits == [1.5, 1.5, 1.5]
        assert "x-key" not in (fake.calls[-1].kwargs.get("headers") or {})  # the signed download needs no key

    @pytest.mark.parametrize("polling_url", ["https://attacker.example/poll", r"https://evil.example\@api.bfl.ai/x",
                                             "https://api.bfl.ai@evil.example/x", "http://api.bfl.ai/v1/get_result",
                                             "https://api.bfl.ai.evil.example/x", "https://user:pw@api.bfl.ai/x"])
    def test_flux_never_sends_the_key_elsewhere(self, admin: Any, fake: FakeUpstream, polling_url: str) -> None:
        save(admin, "image-ai", "flux", IMAGE_CASES["flux"][0])
        fake.fail("bfl.submit", 200, {"id": "t1", "polling_url": polling_url})
        error = post(admin, "/api/image/generate", {"prompt": "A red fox"}).get_json()["error"]
        assert error["kind"] == "parse" and ("unexpected address" in error["message"] or "did not start" in error["message"])
        assert fake.ops() == ["bfl.submit"]  # never polled

    def test_flux_regional_hosts_and_no_redirects(self, admin: Any, fake: FakeUpstream) -> None:
        provider = admin.application.extensions["poster"].image_ai.build("flux", IMAGE_CASES["flux"][0])
        assert provider.poll_host("https://api.eu1.bfl.ai/v1/get_result?id=1") == "api.eu1.bfl.ai"
        assert provider.poll_host("https://api.bfl.ai/v1/get_result?id=1") == "api.bfl.ai"
        save(admin, "image-ai", "flux", IMAGE_CASES["flux"][0])
        post(admin, "/api/image/generate", {"prompt": "A red fox"})
        keyed = [c for c in fake.calls if c.op in ("bfl.submit", "bfl.poll")]
        assert keyed and all(c.kwargs.get("allow_redirects") is False for c in keyed)

    def test_flux_moderation(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "image-ai", "flux", IMAGE_CASES["flux"][0])
        fake.fail("bfl.poll", 200, {"status": "Content Moderated"})
        error = post(admin, "/api/image/generate", {"prompt": "A red fox"}).get_json()["error"]
        assert error["kind"] == "client" and "Moderated" in error["message"]

    def test_gemini_image_aspect(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "image-ai", "gemini", IMAGE_CASES["gemini"][0])
        post(admin, "/api/image/generate", {"prompt": "A red fox", "aspect": "portrait"})
        call = fake.calls[0]
        assert call.url.endswith("/models/gemini-2.5-flash-image:generateContent")
        assert call.json["generationConfig"] == {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "4:5"}}

    @pytest.mark.parametrize("provider", ["openai", "stability", "gemini"])
    def test_edits_are_not_retried_after_a_server_error(self, admin: Any, fake: FakeUpstream, provider: str) -> None:
        settings, _, edit_ops = IMAGE_CASES[provider]
        save(admin, "image-ai", provider, settings)
        fake.fail(edit_ops[0], 500)
        response = post(admin, "/api/image/edit", {"prompt": "Add a hat", "image": b64(image_bytes())})
        assert response.status_code == 502 and fake.ops() == edit_ops[:1]

    @pytest.mark.parametrize("provider", list(IMAGE_CASES))
    def test_connection_tests_bill_nothing(self, admin: Any, fake: FakeUpstream, provider: str) -> None:
        save(admin, "image-ai", provider, IMAGE_CASES[provider][0])
        data = post(admin, "/api/settings/test/image-ai", {}).get_json()
        assert data["ok"] is True and data["detail"] and data["integration"]["status"] == "connected"
        billed = {"pollinations", "pollinations.edit", "openai.images", "openai.edit", "stability.generate", "bfl.submit",
                  "gemini.image"}
        assert not billed & set(fake.ops())

    def test_pollinations_test_checks_the_edit_model(self, admin: Any) -> None:
        save(admin, "image-ai", "pollinations", {"api_key": "pk-switched-key-1234567890", "edit_model": "flux"})
        error = post(admin, "/api/settings/test/image-ai", {}).get_json()["error"]
        assert '"flux" cannot edit images' in error["message"] and "kontext" in error["message"]


# =============================================================================
# Runtime switching: one app, no restart
# =============================================================================

@pytest.mark.parametrize("provider", list(TEXT_CASES))
def test_text_ai_runtime_switching(admin: Any, fake: FakeUpstream, provider: str) -> None:
    assert post(admin, "/api/groq/generate", generate_body()).status_code == 200
    assert fake.ops() == ["groq.chat"]
    settings, op = TEXT_CASES[provider]
    assert save(admin, "text-ai", provider, settings).status_code == 200
    assert admin.get("/api/config").get_json()["groq"]["provider"] == provider
    fake.calls.clear()
    assert post(admin, "/api/groq/generate", generate_body(model=None)).status_code == 200
    assert fake.ops() == [op]
    # And back again, still without a restart.
    assert save(admin, "text-ai", "groq", {}).status_code == 200
    fake.calls.clear()
    post(admin, "/api/groq/generate", generate_body())
    assert fake.ops() == ["groq.chat"]


@pytest.mark.parametrize("provider", list(IMAGE_CASES))
def test_image_ai_runtime_switching(admin: Any, fake: FakeUpstream, provider: str) -> None:
    settings, generate_ops, _ = IMAGE_CASES[provider]
    assert save(admin, "image-ai", provider, settings).status_code == 200
    config = admin.get("/api/config").get_json()["imageGen"]
    assert config["provider"] == provider and config["configured"] and config["canEdit"]
    assert post(admin, "/api/image/generate", {"prompt": "A red fox in snow"}).status_code == 200
    assert fake.ops() == generate_ops


def test_a_model_chosen_for_another_provider_is_not_sent(admin: Any, fake: FakeUpstream) -> None:
    save(admin, "text-ai", "openai", TEXT_CASES["openai"][0])
    post(admin, "/api/groq/generate", generate_body(model="qwen/qwen3.8-27b", provider="groq"))
    assert fake.calls[0].json["model"] == "gpt-4.1-mini"
    post(admin, "/api/groq/generate", generate_body(model="gpt-4.1", provider="openai"))
    assert fake.calls[1].json["model"] == "gpt-4.1"


def test_publisher_uses_the_current_meta_credentials(admin: Any, fake: FakeUpstream) -> None:
    """The publisher thread resolves the Meta client per use, like the routes."""
    social = admin.application.extensions["poster"].social
    assert social.meta.token == "test-meta-token"
    save(admin, "meta", "meta", {"access_token": "EAAB-new-token-1234567890"})
    assert social.meta.token == "EAAB-new-token-1234567890"
    summary = social.publish_due(RetryLog("Publish"))
    assert summary == {"published": 0, "retrying": 0, "failed": 0, "checking": 0}
