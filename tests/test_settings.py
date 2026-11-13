"""Settings → Credentials & Integrations: the settings API, encrypted storage, masking, access control, audit
logging, health monitoring, the import from the environment and backward compatibility."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from poster.config import API
from poster.crypto import SecretBox, generate_key
from poster.errors import ServiceError
from tests.conftest import BLOB_ENV, CLOUDINARY_ENV, post
from tests.fakes import BAD_KEY, BLOB_STORE, META_TOKEN, FakeUpstream, image_bytes
from tests.test_providers import KEY, TEXT_CASES, save
from tests.test_routes import generate_body

OPENAI_KEY = "sk-openai-secret-0123456789abcdef"
PUBLIC = "https://poster.example.com"


@pytest.fixture
def admin(make_app: Any) -> Any:
    return make_app(SETTINGS_ENCRYPTION_KEY=KEY).test_client()


def integrations(client: Any, **kwargs: Any) -> dict[str, Any]:
    response = client.get("/api/settings/integrations", **kwargs)
    assert response.status_code == 200, response.get_json()
    return dict(response.get_json())


def provider(data: dict[str, Any], provider_type: str, name: str) -> dict[str, Any]:
    return next(p for p in data["integrations"][provider_type]["providers"] if p["name"] == name)


def field(entry: dict[str, Any], key: str) -> dict[str, Any]:
    return next(f for f in entry["fields"] if f["key"] == key)


def activity(client: Any) -> list[dict[str, Any]]:
    return list(client.application.extensions["poster"].activity.page("all", None)["entries"])


def rows(client: Any) -> list[dict[str, Any]]:
    return list(client.application.extensions["poster"].settings.store.settings_rows())


# =============================================================================
# Reading and masking
# =============================================================================

class TestRead:
    def test_lists_every_integration_and_provider(self, admin: Any) -> None:
        data = integrations(admin)
        assert data["editable"] is True and data["encryption"]["configured"] is True
        kinds = data["integrations"]
        assert list(kinds) == ["text_ai", "image_ai", "storage", "meta"]
        assert [p["name"] for p in kinds["text_ai"]["providers"]] == ["groq", "openai", "anthropic", "gemini", "azure"]
        assert [p["name"] for p in kinds["image_ai"]["providers"]] == ["pollinations", "openai", "stability", "flux", "gemini"]
        assert [p["name"] for p in kinds["storage"]["providers"]] == ["vercel_blob", "upload_endpoint"]
        assert {k: v["active"] for k, v in kinds.items()} == {"text_ai": "groq", "image_ai": "pollinations",
                                                              "storage": "vercel_blob", "meta": "meta"}

    def test_provider_specific_fields(self, admin: Any) -> None:
        data = integrations(admin)
        keys = {name: [f["key"] for f in provider(data, "text_ai", name)["fields"]] for name in TEXT_CASES}
        assert keys["openai"][:3] == ["api_key", "organization_id", "default_model"]
        assert keys["azure"] == ["endpoint", "deployment", "api_version", "api_key"]
        assert keys["anthropic"][:2] == ["api_key", "default_model"] and "available_models" in keys["groq"]
        meta = [f["key"] for f in provider(data, "meta", "meta")["fields"]]
        assert meta == ["access_token", "app_secret", "app_id", "graph_version", "page_ids"]

    def test_environment_values_are_shown_with_their_origin(self, admin: Any) -> None:
        groq = provider(integrations(admin), "text_ai", "groq")
        key = field(groq, "api_key")
        assert key["origin"] == "env" and key["hasValue"] and key["value"] == "" and key["masked"] == "*********-key"
        assert field(groq, "default_model") == {**field(groq, "default_model"), "value": "qwen/qwen3.8-27b", "origin": "env"}
        assert groq["active"] and groq["configured"]

    def test_secrets_never_leave_the_server(self, admin: Any, make_app: Any) -> None:
        save(admin, "text-ai", "openai", {"api_key": OPENAI_KEY})
        save(admin, "meta", "meta", {"access_token": "EAAB" + "q" * 60 + "XYZ9", "app_secret": "app-secret-0123456789"})
        responses = [admin.get("/api/settings/integrations"), admin.get("/api/settings/status"), admin.get("/api/config"),
                     admin.get("/"), post(admin, "/api/settings/test/text-ai", {})]
        text = "".join(r.get_data(as_text=True) for r in responses)
        for secret in (OPENAI_KEY, "q" * 60, "app-secret-0123456789", "test-groq-key", META_TOKEN, "test-pollinations-key"):
            assert secret not in text
        masked = field(provider(integrations(admin), "text_ai", "openai"), "api_key")["masked"]
        assert masked == "sk-o" + "*" * 25 + "cdef"


# =============================================================================
# Saving
# =============================================================================

class TestSave:
    def test_credentials_are_encrypted_at_rest(self, admin: Any) -> None:
        assert save(admin, "text-ai", "openai", {"api_key": OPENAI_KEY, "default_model": "gpt-4.1"}).status_code == 200
        [row] = [r for r in rows(admin) if r["provider_name"] == "openai"]
        assert OPENAI_KEY not in json.dumps(row) and "gpt-4.1" not in row["encrypted_config"]
        # Only what was saved: defaults and environment values stay defaults and environment values.
        assert SecretBox(KEY).decrypt(row["encrypted_config"]) == {"api_key": OPENAI_KEY, "default_model": "gpt-4.1"}
        assert row["source"] == "admin" and row["is_active"] == 1 and row["status"] == "untested"

    def test_a_blank_or_masked_secret_keeps_the_saved_one(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "text-ai", "openai", {"api_key": OPENAI_KEY})
        masked = field(provider(integrations(admin), "text_ai", "openai"), "api_key")["masked"]
        save(admin, "text-ai", "openai", {"api_key": "", "default_model": "gpt-4.1"})
        save(admin, "text-ai", "openai", {"api_key": masked})
        post(admin, "/api/groq/generate", generate_body(model=None))
        assert fake.calls[0].kwargs["headers"]["Authorization"] == f"Bearer {OPENAI_KEY}"
        assert fake.calls[0].json["model"] == "gpt-4.1"

    def test_a_new_secret_replaces_it(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "text-ai", "openai", {"api_key": OPENAI_KEY})
        save(admin, "text-ai", "openai", {"api_key": "sk-replacement-key-987654321"})
        post(admin, "/api/groq/generate", generate_body(model=None))
        assert fake.calls[0].kwargs["headers"]["Authorization"] == "Bearer sk-replacement-key-987654321"

    def test_an_environment_secret_left_blank_keeps_following_the_environment(self, admin: Any) -> None:
        save(admin, "text-ai", "groq", {"api_key": "", "default_model": "qwen/qwen3-32b"})
        [row] = [r for r in rows(admin) if r["provider_name"] == "groq"]
        assert "api_key" not in SecretBox(KEY).decrypt(row["encrypted_config"])  # not copied into the database
        groq = admin.application.extensions["poster"].groq
        assert groq.api_key == "test-groq-key" and groq.default_model == "qwen/qwen3-32b"

    def test_saving_without_activating(self, admin: Any) -> None:
        response = save(admin, "text-ai", "anthropic", TEXT_CASES["anthropic"][0], activate=False)
        assert response.get_json()["integration"]["active"] == "groq"
        assert admin.get("/api/config").get_json()["groq"]["provider"] == "groq"

    @pytest.mark.parametrize(("slug", "name", "config", "message"), [
        ("text-ai", "openai", {}, "API key is required"),
        ("text-ai", "openai", {"api_key": "sk with spaces"}, "contains whitespace"),
        ("text-ai", "openai", {"api_key": OPENAI_KEY, "endpoint": "http://api.example.com"}, "must use https://"),
        ("text-ai", "openai", {"api_key": OPENAI_KEY, "colour": "blue"}, "Unknown settings for OpenAI: colour"),
        ("text-ai", "azure", {"api_key": OPENAI_KEY, "endpoint": "https://x.openai.azure.com"}, "Deployment name is required"),
        ("text-ai", "mystery", {}, 'Unknown Text AI provider "mystery"'),
        ("storage", "vercel_blob", {"access_token": "not-a-blob-token"}, "not a Vercel Blob read-write token"),
        ("storage", "upload_endpoint", {"endpoint": "ftp://upload"}, "full http(s) URL"),
        ("meta", "meta", {"graph_version": "23"}, "must look like v23.0"),
        ("meta", "meta", {"page_ids": ["pg1", "bad id!"]}, "Page IDs contain only"),
        ("text-ai", "openai", {"api_key": 5}, "API key must be text"),
    ])
    def test_validation(self, admin: Any, slug: str, name: str, config: dict[str, Any], message: str) -> None:
        response = save(admin, slug, name, config)
        assert response.status_code == 400 and message in response.get_json()["error"]["message"]
        assert admin.get("/api/config").get_json()["groq"]["provider"] == "groq"

    def test_unknown_integration(self, admin: Any) -> None:
        response = post(admin, "/api/settings/integrations/email", {"provider": "x"})
        assert response.status_code == 400 and "Unknown integration" in response.get_json()["error"]["message"]

    def test_without_an_encryption_key_nothing_is_saved(self, client: Any) -> None:
        data = integrations(client)
        assert data["editable"] is False and "SETTINGS_ENCRYPTION_KEY" in data["editableProblem"]
        response = save(client, "text-ai", "openai", {"api_key": OPENAI_KEY})
        assert response.status_code == 503 and response.get_json()["error"]["kind"] == "config"
        assert rows(client) == []
        assert client.get("/api/config").get_json()["integrations"]["editable"] is False

    def test_without_a_database_nothing_is_saved(self, make_app: Any) -> None:
        client = make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL="", VERCEL="1").test_client()
        data = integrations(client)
        assert data["editable"] is False and "needs the database" in data["editableProblem"]
        assert data["integrations"]["text_ai"]["active"] == "groq"  # the environment still works

    @pytest.mark.parametrize("route", ["save", "test", "models"])
    def test_a_saved_key_is_never_sent_to_a_new_address(self, admin: Any, fake: FakeUpstream, route: str) -> None:
        save(admin, "text-ai", "openai", {"api_key": OPENAI_KEY})
        moved = {"provider": "openai", "config": {"endpoint": "https://collector.example/v1"}}
        path = {"save": "/api/settings/integrations/text-ai", "test": "/api/settings/test/text-ai",
                "models": "/api/settings/text-ai/models"}[route]
        response = post(admin, path, moved)
        assert response.status_code == 400 and "Enter the API key again" in response.get_json()["error"]["message"]
        assert not fake.calls
        with_key = {"provider": "openai", "config": {"endpoint": "https://collector.example/v1", "api_key": "sk-typed-again-123456"}}
        assert post(admin, path, with_key).status_code != 400

    def test_an_environment_key_is_not_sent_to_a_new_address_either(self, admin: Any, fake: FakeUpstream) -> None:
        response = save(admin, "text-ai", "groq", {"endpoint": "https://collector.example/openai/v1"})
        assert response.status_code == 400 and not fake.calls

    def test_addresses_that_get_no_secret_change_freely(self, make_app: Any) -> None:
        client = make_app(SETTINGS_ENCRYPTION_KEY=KEY, **BLOB_ENV).test_client()
        assert save(client, "storage", "vercel_blob", {"public_url": "https://poster.example.net"}).status_code == 200

    def test_list_fields_take_lists(self, admin: Any) -> None:
        save(admin, "text-ai", "groq", {"available_models": ["qwen/qwen3-32b", " qwen/qwen3-32b", "llama-4"]})
        groq = admin.application.extensions["poster"].groq
        assert groq.allowed_models == ("qwen/qwen3.8-27b", "qwen/qwen3-32b", "llama-4")


# =============================================================================
# Storage and Meta
# =============================================================================

class TestStorage:
    def test_vercel_blob_from_the_dashboard(self, admin: Any, fake: FakeUpstream) -> None:
        assert admin.get("/api/config").get_json()["media"]["configured"] is False
        token = f"vercel_blob_rw_{BLOB_STORE}_secretsecret"
        assert save(admin, "storage", "vercel_blob", {"access_token": token, "public_url": "https://poster.test"}).status_code == 200
        assert admin.get("/api/config").get_json()["media"] == {**admin.get("/api/config").get_json()["media"],
                                                                 "configured": True, "host": "Vercel Blob"}
        data = post(admin, "/api/settings/test/storage", {}).get_json()
        assert data["ok"] is True and BLOB_STORE in data["detail"]
        [listing] = fake.calls
        assert listing.op == "blob.list" and listing.kwargs["headers"]["Authorization"] == f"Bearer {token}"
        upload = admin.post("/api/media/upload", data={"file": (__import__("io").BytesIO(image_bytes()), "a.jpg")},
                            headers={"X-Poster-Request": "1"})
        assert upload.get_json()["url"].startswith("https://poster.test/api/media/image/a-")

    def test_switching_to_an_upload_endpoint(self, make_app: Any, fake: FakeUpstream) -> None:
        client = make_app(SETTINGS_ENCRYPTION_KEY=KEY, **BLOB_ENV).test_client()
        save(client, "storage", "upload_endpoint", {"endpoint": CLOUDINARY_ENV["MEDIA_UPLOAD_ENDPOINT"], "preset": "poster"})
        upload = client.post("/api/media/upload", data={"file": (__import__("io").BytesIO(image_bytes()), "a.jpg")},
                             headers={"X-Poster-Request": "1"})
        assert upload.get_json()["url"] == "https://res.media.test/poster/uploaded.jpg"
        assert fake.ops() == ["media.upload"] and fake.calls[0].kwargs["data"] == {"upload_preset": "poster"}
        data = post(client, "/api/settings/test/storage", {}).get_json()
        assert data["ok"] is True and "nothing is stored" in data["detail"]

    def test_a_refused_token(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "storage", "vercel_blob", {"access_token": f"vercel_blob_rw_{BLOB_STORE}_secretsecret"})
        fake.fail("blob.list", 403, {"error": {"message": "Access denied"}})
        error = post(admin, "/api/settings/test/storage", {}).get_json()["error"]
        assert error["kind"] == "auth"
        status = {s["type"]: s for s in admin.get("/api/settings/status").get_json()["services"]}["storage"]
        assert status["status"] == "error" and "Access denied" in status["error"]


class TestMeta:
    def test_test_lists_every_page_and_account(self, admin: Any) -> None:
        data = post(admin, "/api/settings/test/meta", {}).get_json()
        assert data["ok"] is True and [p["id"] for p in data["pages"]] == ["pg1", "pg2"]
        assert [a["handle"] for a in data["instagramAccounts"]] == ["poster.studio"]
        assert data["selectedPageIds"] == [] and "page-token" not in json.dumps(data)

    def test_page_selection_limits_the_channels(self, admin: Any) -> None:
        save(admin, "meta", "meta", {"page_ids": ["pg1"]})
        channels = post(admin, "/api/social/connect", {}).get_json()["channels"]
        assert [c["id"] for c in channels] == ["pg1", "ig1"]
        assert post(admin, "/api/settings/test/meta", {}).get_json()["selectedPageIds"] == ["pg1"]

    def test_a_new_token_is_used_at_once(self, admin: Any, fake: FakeUpstream) -> None:
        assert post(admin, "/api/social/validate", {}).status_code == 200
        save(admin, "meta", "meta", {"access_token": "EAAB-replaced-token-1234567890"})
        error = post(admin, "/api/social/validate", {}).get_json()["error"]
        assert error["kind"] == "auth" and fake.calls[-1].kwargs["params"]["access_token"] == "EAAB-replaced-token-1234567890"

    def test_app_secret_proof_follows_the_settings(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "meta", "meta", {"app_secret": "dashboard-secret-123"})
        post(admin, "/api/social/validate", {})
        assert fake.calls[0].kwargs["params"]["appsecret_proof"]

    def test_unsaved_settings_can_be_tested(self, admin: Any, fake: FakeUpstream) -> None:
        response = post(admin, "/api/settings/test/meta", {"config": {"access_token": "EAAB-unsaved-token-12345678"}})
        assert response.status_code == 502 and response.get_json()["error"]["kind"] == "auth"
        assert fake.calls[0].kwargs["params"]["access_token"] == "EAAB-unsaved-token-12345678"
        # The saved settings were not tested, so their status stays as it was.
        status = {s["type"]: s for s in admin.get("/api/settings/status").get_json()["services"]}["meta"]
        assert status["status"] == "untested"


# =============================================================================
# Health monitoring and audit logging
# =============================================================================

class TestHealth:
    def test_status_lists_every_integration(self, admin: Any) -> None:
        services = admin.get("/api/settings/status").get_json()["services"]
        assert [(s["type"], s["service"], s["providerLabel"], s["status"]) for s in services] == [
            ("text_ai", "Text AI", "Groq", "untested"), ("image_ai", "Image AI", "Pollinations", "untested"),
            ("storage", "Blob storage", "Vercel Blob", "not_configured"), ("meta", "Facebook & Instagram",
                                                                           "Facebook & Instagram", "untested")]
        assert "Access token is required" in services[2]["problem"]

    def test_a_passing_test_records_the_connection(self, admin: Any) -> None:
        data = post(admin, "/api/settings/test/text-ai", {}).get_json()
        assert data["ok"] is True and data["modelAvailable"] is True
        [text] = [s for s in admin.get("/api/settings/status").get_json()["services"] if s["type"] == "text_ai"]
        assert text["status"] == "connected" and text["lastTestedAt"] == text["lastSuccessfulConnection"]
        assert text["error"] is None

    def test_a_failing_test_records_the_error_and_keeps_the_last_success(self, admin: Any, fake: FakeUpstream) -> None:
        post(admin, "/api/settings/test/text-ai", {})
        fake.fail("groq.models", 401, {"error": "unauthorized"})
        assert post(admin, "/api/settings/test/text-ai", {}).status_code == 502
        [text] = [s for s in admin.get("/api/settings/status").get_json()["services"] if s["type"] == "text_ai"]
        assert text["status"] == "error" and "HTTP 401" in text["error"] and text["lastSuccessfulConnection"]

    def test_a_missing_model_fails_the_test(self, admin: Any) -> None:
        save(admin, "text-ai", "groq", {"default_model": "not-a-listed-model"})
        data = post(admin, "/api/settings/test/text-ai", {}).get_json()
        assert data["ok"] is False and "not-a-listed-model" in data["detail"]
        assert data["integration"]["status"] == "error"

    def test_changing_settings_resets_the_status(self, admin: Any) -> None:
        post(admin, "/api/settings/test/text-ai", {})
        save(admin, "text-ai", "groq", {"default_model": "qwen/qwen3-32b"})
        assert provider(integrations(admin), "text_ai", "groq")["status"] == "untested"

    def test_status_is_kept_without_an_encryption_key(self, client: Any) -> None:
        post(client, "/api/settings/test/image-ai", {})
        [image] = [s for s in client.get("/api/settings/status").get_json()["services"] if s["type"] == "image_ai"]
        assert image["status"] == "connected"
        [row] = rows(client)
        assert row["encrypted_config"] == "" and row["source"] == "env"


class TestAudit:
    def test_every_change_is_logged_without_values(self, admin: Any) -> None:
        save(admin, "text-ai", "openai", {"api_key": OPENAI_KEY})
        save(admin, "image-ai", "stability", {"api_key": "sk-stability-key-1234567890"})
        save(admin, "storage", "vercel_blob", {"access_token": f"vercel_blob_rw_{BLOB_STORE}_secretsecret"})
        save(admin, "meta", "meta", {"app_secret": "dashboard-secret-123"})
        post(admin, "/api/settings/test/storage", {})
        post(admin, "/api/settings/test/text-ai", {})
        entries = activity(admin)
        messages = [e["message"] for e in entries]
        for expected in ("Text AI credentials updated (OpenAI).", "Text AI provider changed from Groq to OpenAI.",
                         "Image AI settings updated (Stability AI).",
                         "Image AI provider changed from Pollinations to Stability AI.",
                         "Blob storage configuration updated (Vercel Blob).", "Meta credentials updated (Facebook & Instagram).",
                         "Storage validation performed (Vercel Blob): connected.",
                         "Text AI provider connection tested (OpenAI): connected."):
            assert expected in messages
        assert all(e["category"] == "settings" for e in entries)
        assert "Changed: API key." in next(e["details"] for e in entries if e["message"].startswith("Text AI credentials"))
        dumped = json.dumps(entries)
        assert OPENAI_KEY not in dumped and "dashboard-secret-123" not in dumped and "secretsecret" not in dumped

    def test_failed_tests_are_logged_as_errors(self, admin: Any, fake: FakeUpstream) -> None:
        save(admin, "text-ai", "anthropic", {"api_key": BAD_KEY})
        post(admin, "/api/settings/test/text-ai", {})
        failed = [e for e in activity(admin) if e["level"] == "error"]
        assert failed[0]["message"] == "Text AI provider connection tested (Anthropic): failed."
        assert "HTTP 401" in failed[0]["details"]

    def test_saving_unchanged_settings_logs_nothing_new(self, admin: Any) -> None:
        save(admin, "text-ai", "openai", {"api_key": OPENAI_KEY})
        before = len(activity(admin))
        save(admin, "text-ai", "openai", {})
        assert len(activity(admin)) == before


# =============================================================================
# Access control
# =============================================================================

class TestAuditIntegrity:
    def test_browsers_cannot_write_server_entries(self, admin: Any) -> None:
        forged = {"id": "srv-forged", "ts": "2026-01-01T00:00:00Z", "level": "info", "category": "settings",
                  "message": "Meta credentials updated."}
        response = post(admin, "/api/activity", {"entries": [forged]})
        assert response.status_code == 400 and "reserved for the server" in response.get_json()["error"]["message"]

    def test_with_an_admin_password_clearing_keeps_the_audit(self, make_app: Any) -> None:
        client = make_app(SETTINGS_ENCRYPTION_KEY=KEY, POSTER_ADMIN_PASSWORD="admin-pass-123").test_client()
        admin_header = {"X-Poster-Admin": "admin-pass-123"}
        post(client, "/api/settings/integrations/text-ai", {"provider": "openai", "config": {"api_key": OPENAI_KEY}},
             headers=admin_header)
        post(client, "/api/activity", {"entries": [{"id": "b1", "ts": "2026-01-01T00:00:00Z", "level": "info",
                                                    "category": "user", "message": "Hello"}]})
        post(client, "/api/activity/clear", {})
        left = [e["message"] for e in activity(client)]
        assert "Hello" not in left and "Text AI provider changed from Groq to OpenAI." in left
        post(client, "/api/activity/clear", {}, headers=admin_header)
        assert activity(client) == []

    def test_the_public_health_check_names_no_provider(self, admin: Any) -> None:
        health = admin.get("/api/health", base_url=PUBLIC).get_json()
        assert health["groq"] is True and "textAi" not in health and "Groq" not in json.dumps(health)


class TestAccess:
    ROUTES = (("GET", "/api/settings/integrations"), ("GET", "/api/settings/status"), ("GET", "/api/settings/text-ai/models"),
              ("POST", "/api/settings/integrations/text-ai"), ("POST", "/api/settings/test/meta"))

    def call(self, client: Any, method: str, path: str, **kwargs: Any) -> Any:
        if method == "GET":
            return client.get(path, **kwargs)
        body = {"provider": "openai", "config": {"api_key": OPENAI_KEY}} if path.endswith("/text-ai") else {}
        return post(client, path, body, **kwargs)

    @pytest.mark.parametrize(("method", "path"), ROUTES)
    def test_an_admin_password_is_required_when_set(self, make_app: Any, fake: FakeUpstream, method: str, path: str) -> None:
        client = make_app(SETTINGS_ENCRYPTION_KEY=KEY, POSTER_ADMIN_PASSWORD="admin-pass-123").test_client()
        refused = self.call(client, method, path)
        assert refused.status_code == 403 and refused.get_json()["error"]["admin"] is True
        wrong = self.call(client, method, path, headers={"X-Poster-Admin": "nope"})
        assert wrong.status_code == 403 and "not correct" in wrong.get_json()["error"]["message"]
        assert not fake.calls and rows(client) == []
        assert self.call(client, method, path, headers={"X-Poster-Admin": "admin-pass-123"}).status_code == 200

    def test_the_rest_of_the_dashboard_needs_no_admin_password(self, make_app: Any) -> None:
        client = make_app(POSTER_ADMIN_PASSWORD="admin-pass-123").test_client()
        assert client.get("/api/config").get_json()["integrations"]["adminPassword"] is True
        assert post(client, "/api/groq/generate", generate_body()).status_code == 200

    @pytest.mark.parametrize(("method", "path"), ROUTES)
    def test_the_dashboard_password_still_applies(self, make_app: Any, method: str, path: str) -> None:
        client = make_app(SETTINGS_ENCRYPTION_KEY=KEY, POSTER_PASSWORD="dashboard-pass").test_client()
        assert self.call(client, method, path).status_code == 401
        auth = {"Authorization": "Basic " + base64.b64encode(b"me:dashboard-pass").decode()}
        assert self.call(client, method, path, headers=auth).status_code == 200

    @pytest.mark.parametrize(("method", "path"), ROUTES)
    def test_a_public_address_without_a_password_is_refused(self, admin: Any, method: str, path: str) -> None:
        assert self.call(admin, method, path, base_url=PUBLIC).status_code == 503

    def test_changes_need_the_csrf_header(self, admin: Any) -> None:
        response = admin.post("/api/settings/integrations/text-ai", json={"provider": "openai", "config": {"api_key": OPENAI_KEY}})
        assert response.status_code == 403 and rows(admin) == [r for r in rows(admin) if r["provider_name"] != "openai"]


# =============================================================================
# The import from the environment, backward compatibility and other servers
# =============================================================================

class TestImport:
    def test_the_environment_becomes_the_first_records(self, admin: Any) -> None:
        integrations(admin)
        imported = {(r["provider_type"], r["provider_name"]): r for r in rows(admin)}
        assert set(imported) == {("text_ai", "groq"), ("image_ai", "pollinations"), ("meta", "meta")}
        groq = imported[("text_ai", "groq")]
        assert groq["source"] == "env" and groq["is_active"] == 1
        assert SecretBox(KEY).decrypt(groq["encrypted_config"])["api_key"] == "test-groq-key"
        messages = [e["message"] for e in activity(admin)]
        assert "Text AI settings imported from the environment (Groq)." in messages
        assert "Image AI settings imported from the environment (Pollinations)." in messages

    def test_once(self, make_app: Any, tmp_path: Path) -> None:
        database = f"sqlite:///{tmp_path / 'poster.db'}"
        first = make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL=database).test_client()
        integrations(first)
        second = make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL=database).test_client()
        integrations(second)
        assert len(rows(second)) == 3
        assert len([e for e in activity(second) if "imported" in e["message"]]) == 3

    def test_a_rotated_environment_key_still_wins_until_saved_here(self, make_app: Any, tmp_path: Path, fake: FakeUpstream) -> None:
        database = f"sqlite:///{tmp_path / 'poster.db'}"
        integrations(make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL=database).test_client())
        rotated = make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL=database, GROQ_API_KEY="gsk-rotated-in-env").test_client()
        post(rotated, "/api/groq/generate", generate_body())
        assert fake.calls[-1].kwargs["headers"]["Authorization"] == "Bearer gsk-rotated-in-env"
        save(rotated, "text-ai", "groq", {"api_key": "gsk-saved-in-dashboard"})
        post(rotated, "/api/groq/generate", generate_body())
        assert fake.calls[-1].kwargs["headers"]["Authorization"] == "Bearer gsk-saved-in-dashboard"

    def test_nothing_is_imported_without_a_key(self, client: Any) -> None:
        integrations(client)
        assert rows(client) == []
        assert client.get("/api/config").get_json()["groq"]["configured"] is True


class TestResilience:
    def test_other_servers_follow_a_change(self, make_app: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           fake: FakeUpstream) -> None:
        database = f"sqlite:///{tmp_path / 'poster.db'}"
        one = make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL=database).test_client()
        other = make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL=database).test_client()
        assert other.get("/api/config").get_json()["groq"]["provider"] == "groq"
        save(one, "text-ai", "openai", {"api_key": OPENAI_KEY})
        assert other.get("/api/config").get_json()["groq"]["provider"] == "groq"  # within its cache time
        monkeypatch.setitem(API["settings"], "cache_seconds", 0)
        other.application.extensions["poster"].settings.invalidate()
        assert other.get("/api/config").get_json()["groq"]["provider"] == "openai"

    def test_a_database_failure_falls_back_to_the_environment(self, make_app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        client = make_app(SETTINGS_ENCRYPTION_KEY=KEY).test_client()
        store = client.application.extensions["poster"].settings.store

        def broken(*_args: Any) -> Any:
            raise ServiceError("The database failed: connection refused", kind="server", service="Database")

        monkeypatch.setattr(store, "settings_rows", broken)
        assert post(client, "/api/groq/generate", generate_body()).status_code == 200
        data = integrations(client)
        assert data["database"]["configured"] is False and "connection refused" in data["database"]["problem"]

    def test_settings_saved_under_another_key_are_ignored(self, make_app: Any, tmp_path: Path) -> None:
        database = f"sqlite:///{tmp_path / 'poster.db'}"
        save(make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL=database).test_client(), "text-ai", "openai",
             {"api_key": OPENAI_KEY})
        other = make_app(SETTINGS_ENCRYPTION_KEY=generate_key(), DATABASE_URL=database).test_client()
        openai = provider(integrations(other), "text_ai", "openai")
        assert "could not be decrypted" in openai["problem"]
        assert other.get("/api/config").get_json()["groq"]["configured"] is False  # OpenAI is active, without its key

    def test_key_rotation(self, make_app: Any, tmp_path: Path) -> None:
        database = f"sqlite:///{tmp_path / 'poster.db'}"
        old_app = make_app(SETTINGS_ENCRYPTION_KEY=KEY, DATABASE_URL=database)
        save(old_app.test_client(), "text-ai", "openai", {"api_key": OPENAI_KEY})
        new_key = generate_key()
        rotating = make_app(SETTINGS_ENCRYPTION_KEY=f"{new_key},{KEY}", DATABASE_URL=database)
        assert rotating.extensions["poster"].settings.rotate_key() >= 1
        fresh = make_app(SETTINGS_ENCRYPTION_KEY=new_key, DATABASE_URL=database)
        assert fresh.extensions["poster"].groq.api_key == OPENAI_KEY


class TestModels:
    def test_saved_settings(self, admin: Any) -> None:
        data = admin.get("/api/settings/text-ai/models").get_json()
        assert data["provider"] == "groq" and data["models"] == ["qwen/qwen3.8-27b", "qwen/qwen3-32b"]

    def test_unsaved_settings(self, admin: Any, fake: FakeUpstream) -> None:
        data = post(admin, "/api/settings/text-ai/models", {"provider": "openai", "config": {"api_key": OPENAI_KEY}}).get_json()
        assert data["models"] == fake.provider_models["openai"]
        assert admin.get("/api/config").get_json()["groq"]["provider"] == "groq"  # nothing was saved

    def test_another_provider_by_name(self, admin: Any) -> None:
        save(admin, "text-ai", "anthropic", TEXT_CASES["anthropic"][0], activate=False)
        assert admin.get("/api/settings/text-ai/models?provider=anthropic").get_json()["models"][0] == "claude-sonnet-5-5"


class TestMigrationScript:
    def test_commands(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        import tools.migrate_settings as script
        from poster.config import Config
        from tests.conftest import BASE_ENV

        config = Config.from_env({**BASE_ENV, "SETTINGS_ENCRYPTION_KEY": KEY, "DATABASE_URL": f"sqlite:///{tmp_path / 'p.db'}"},
                                 env_file=None)
        assert script.main(["generate-key"]) == 0 and len(capsys.readouterr().out.strip()) == 44
        assert script.main(["migrate"], config) == 0
        assert "Imported: Text AI: Groq, Image AI: Pollinations, Facebook & Instagram: Facebook & Instagram" in capsys.readouterr().out
        assert script.main(["migrate"], config) == 0 and "Nothing to import" in capsys.readouterr().out
        assert script.main(["status"], config) == 0
        out = capsys.readouterr().out
        assert "* Text AI" in out and "test-groq-key" not in out
        assert script.main(["rotate"], config) == 0 and "Encrypted 3" in capsys.readouterr().out
        assert script.main(["sql"]) == 0 and "CREATE TABLE IF NOT EXISTS poster_integration_settings" in capsys.readouterr().out
