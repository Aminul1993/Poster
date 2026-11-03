"""HTTP-level behaviour of every route, against FakeUpstream.

These tests pin down the API contract the dashboard relies on, so the server can be
restructured underneath them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from PIL import Image

from tests.conftest import API_HEADERS, BLOB_ENV, CLOUDINARY_ENV, post
from tests.fakes import BLOB_PUBLIC_HOST, META_TOKEN, image_bytes


def future(days: float = 1) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def page_config(html: str) -> dict[str, Any]:
    match = re.search(r'<script type="application/json" id="server-config">(.*?)</script>', html, re.S)
    assert match, "server-config block missing"
    return json.loads(match.group(1))


# =============================================================================
# Page, headers and request guard
# =============================================================================

class TestPage:
    def test_index_has_a_fresh_csp_nonce_per_request(self, client: Any) -> None:
        first, second = client.get("/"), client.get("/")
        assert first.status_code == 200
        nonces = []
        for page in (first, second):
            nonce = re.search(r"script-src[^;]*'nonce-([^']+)'", page.headers["Content-Security-Policy"]).group(1)
            assert f'nonce="{nonce}"' in page.get_data(as_text=True)
            nonces.append(nonce)
        assert nonces[0] != nonces[1]

    def test_index_embeds_config_without_secrets(self, client: Any) -> None:
        html = client.get("/").get_data(as_text=True)
        config = page_config(html)
        assert config["mode"] == "live"
        assert config["groq"]["configured"] and config["social"]["configured"]
        assert config["social"]["database"]["configured"] is True and config["social"]["database"]["problem"] is None
        for secret in ("test-groq-key", META_TOKEN, "test-pollinations-key", "test-cron-secret"):
            assert secret not in html

    def test_security_headers(self, client: Any) -> None:
        page, api = client.get("/"), client.get("/api/health")
        for response in (page, api):
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["X-Frame-Options"] == "DENY"
            assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        assert api.headers["Cache-Control"] == "no-store"

    def test_health_and_config(self, client: Any) -> None:
        health = client.get("/api/health").get_json()
        assert health["status"] == "ok"
        assert health["groq"] and health["social"] and health["database"] and health["imageGen"] and not health["media"]
        config = client.get("/api/config").get_json()
        assert set(config) >= {"version", "mode", "groq", "social", "media", "imageGen", "limits"}
        assert config["social"]["tokenId"] == hashlib.sha256(META_TOKEN.encode()).hexdigest()[:12]
        assert config["social"]["cronSecretSet"] is True and config["social"]["version"] == "v23.0"
        assert config["groq"]["requestTimeout"] == (120 * 4 + 7) * 2 + 15

    @pytest.mark.parametrize(("env", "mode"), [({"POSTER_MODE": "demo"}, "demo"), ({"GROQ_API_KEY": ""}, "demo"),
                                               ({}, "live")])
    def test_default_mode(self, make_app: Any, env: dict[str, str], mode: str) -> None:
        assert make_app(**env).test_client().get("/api/config").get_json()["mode"] == mode


class TestGuard:
    def test_post_without_header_is_refused(self, client: Any, fake: Any) -> None:
        response = client.post("/api/social/validate", json={})
        assert response.status_code == 403
        assert response.get_json()["error"]["message"] == "Missing X-Poster-Request header."
        assert not fake.calls

    def test_cross_origin_post_is_refused(self, client: Any) -> None:
        response = post(client, "/api/social/validate", {}, headers={"Origin": "https://evil.test"})
        assert response.status_code == 403

    def test_same_origin_post_is_accepted(self, client: Any) -> None:
        assert post(client, "/api/social/validate", {}, headers={"Origin": "http://localhost"}).status_code == 200

    def test_non_json_body(self, client: Any) -> None:
        response = client.post("/api/social/schedule", data="x", headers=API_HEADERS)
        assert response.status_code == 400
        assert response.get_json()["error"]["kind"] == "validation"


class TestErrors:
    def test_unknown_api_route_is_json(self, client: Any) -> None:
        response = client.get("/api/nope")
        assert response.status_code == 404
        assert response.get_json()["error"]["kind"] == "validation"

    def test_unknown_page_is_not_json(self, client: Any) -> None:
        assert client.get("/nope").status_code == 404
        assert client.get("/nope").mimetype == "text/html"

    def test_method_not_allowed_is_json(self, client: Any) -> None:
        response = post(client, "/api/health", {})
        assert response.status_code == 405
        assert response.get_json()["error"]["kind"] == "validation"

    def test_unexpected_error_is_hidden(self, client: Any, fake: Any) -> None:
        fake.fail("meta.me", exc=RuntimeError("secret internals"))
        response = post(client, "/api/social/validate", {})
        assert response.status_code == 500
        assert response.get_json() == {"error": {"message": "Unexpected server error. Check the server log.", "kind": "server"}}

    def test_upload_over_the_body_limit(self, blob_client: Any) -> None:
        big = io.BytesIO(b"\xff\xd8\xff" + b"0" * (22 * 1024 * 1024))
        response = blob_client.post("/api/media/upload", data={"file": (big, "big.jpg")}, headers=API_HEADERS)
        assert response.status_code == 413
        assert "larger than the server allows" in response.get_json()["error"]["message"]


# =============================================================================
# Groq
# =============================================================================

def generate_body(**overrides: Any) -> dict[str, Any]:
    return {"model": "qwen/qwen3.8-27b", "tone": "friendly", "extraInstructions": "", "descMin": 10, "descMax": 2200,
            "image": b64(image_bytes()), "fileName": "beach.jpg", **overrides}


class TestGroqGenerate:
    def test_normalised_content(self, client: Any, fake: Any) -> None:
        response = post(client, "/api/groq/generate", generate_body())
        assert response.status_code == 200
        data = response.get_json()
        assert data["content"] == {
            "description": "A calm morning by the sea, with soft golden light on the water.",
            "alternative_description": "Saltwater, sunshine and slow mornings by the coast.",
            "hashtags": ["#beach", "#sunrise", "#travel", "#ocean", "#calm", "#morning", "#seaside", "#wanderlust",
                         "#explore", "#nature", "#vacation"],
            "category": "Travel",
            "engagement_score": 82,
        }
        assert data["issues"] == [] and data["repaired"] is False and data["retries"] == []
        chat = fake.calls[0]
        assert chat.op == "groq.chat" and chat.url == "https://groq.test/openai/v1/chat/completions"
        assert chat.kwargs["headers"]["Authorization"] == "Bearer test-groq-key"
        assert chat.json["response_format"] == {"type": "json_object"} and chat.json["stream"] is False
        text, image = chat.json["messages"][1]["content"]
        assert image["image_url"]["url"].startswith("data:image/jpeg;base64,") and "warm, conversational" in text["text"]
        assert "Language: write \"description\" and \"alternative_description\" in English." in text["text"]

    def test_caption_language_reaches_the_prompt(self, client: Any, fake: Any) -> None:
        assert post(client, "/api/groq/generate", generate_body(language="bn-en")).status_code == 200
        prompt = fake.calls[0].json["messages"][1]["content"][0]["text"]
        assert "Bengali in Bangla script, mixed naturally with English words and phrases" in prompt

    def test_full_chat_url_endpoint(self, make_app: Any, fake: Any) -> None:
        client = make_app(GROQ_ENDPOINT="https://groq.test/openai/v1/chat/completions/").test_client()
        assert post(client, "/api/groq/generate", generate_body()).status_code == 200
        assert fake.calls[0].url == "https://groq.test/openai/v1/chat/completions"
        assert post(client, "/api/groq/test", {}).get_json()["host"] == "groq.test"
        assert fake.calls[1].url == "https://groq.test/openai/v1/models"

    def test_tolerates_fences_think_tags_aliases_and_inline_tags(self, client: Any, fake: Any) -> None:
        fake.reply('<think>hmm</think>```json\n{"caption": "Coffee time is the best time of day #coffee #latte", '
                   '"tags": "cafe, morning", "score": "73", "categories": ["food"],}\n```')
        data = post(client, "/api/groq/generate", generate_body()).get_json()
        content = data["content"]
        assert content["description"] == "Coffee time is the best time of day"
        assert content["hashtags"][:4] == ["#cafe", "#morning", "#coffee", "#latte"]
        assert len(content["hashtags"]) == 10
        assert content["category"] == "Food" and content["engagement_score"] == 73
        assert "No alternative caption was returned." in data["issues"]

    def test_repairs_malformed_reply_once(self, client: Any, fake: Any) -> None:
        fake.reply("Sorry, here you go: not json", json.dumps({"description": "Fixed caption for the image", "hashtags": []}))
        data = post(client, "/api/groq/generate", generate_body()).get_json()
        assert data["repaired"] is True
        assert data["content"]["description"] == "Fixed caption for the image"
        second = fake.calls[1].json["messages"]
        assert second[-2] == {"role": "assistant", "content": "Sorry, here you go: not json"}
        assert "ONLY one JSON object" in second[-1]["content"]

    def test_gives_up_after_one_repair(self, client: Any, fake: Any) -> None:
        fake.reply("nope", "still nope")
        response = post(client, "/api/groq/generate", generate_body())
        assert response.status_code == 502
        assert response.get_json()["error"]["kind"] == "parse"
        assert fake.ops() == ["groq.chat", "groq.chat"]

    def test_auth_failure_is_not_retried(self, client: Any, fake: Any) -> None:
        fake.fail("groq.chat", 401, {"error": "unauthorized"})
        response = post(client, "/api/groq/generate", generate_body())
        assert response.status_code == 502
        error = response.get_json()["error"]
        assert error["kind"] == "auth" and error["status"] == 401 and error["service"] == "Groq"
        assert fake.ops() == ["groq.chat"]

    def test_server_error_is_retried(self, client: Any, fake: Any, waits: list[float]) -> None:
        fake.fail("groq.chat", 503, times=2)
        data = post(client, "/api/groq/generate", generate_body()).get_json()
        assert [r["attempt"] for r in data["retries"]] == [1, 2]
        assert waits == [1, 2]

    def test_retries_are_reported_on_failure(self, client: Any, fake: Any, waits: list[float]) -> None:
        fake.fail("groq.chat", 500, times=4)
        error = post(client, "/api/groq/generate", generate_body()).get_json()["error"]
        assert error["kind"] == "server" and len(error["retries"]) == 3
        assert waits == [1, 2, 4]

    @pytest.mark.parametrize("overrides", [
        {"image": ""}, {"image": "!!!"}, {"image": b64(b"GIF89a....")}, {"tone": "angry"}, {"language": "fr"}, {"descMin": 50, "descMax": 40},
        {"model": "bad model!"}, {"extraInstructions": "x" * 501}, {"descMax": "long"},
    ])
    def test_validation(self, client: Any, fake: Any, overrides: dict[str, Any]) -> None:
        response = post(client, "/api/groq/generate", generate_body(**overrides))
        assert response.status_code == 400
        assert response.get_json()["error"]["kind"] == "validation"
        assert not fake.calls

    def test_data_url_image_is_accepted(self, client: Any) -> None:
        body = generate_body(image="data:image/png;base64," + b64(image_bytes("PNG")))
        assert post(client, "/api/groq/generate", body).status_code == 200

    def test_not_configured(self, make_app: Any) -> None:
        client = make_app(GROQ_API_KEY="").test_client()
        response = post(client, "/api/groq/generate", generate_body())
        assert response.status_code == 503
        assert response.get_json()["error"]["kind"] == "config"


class TestGroqTest:
    def test_model_listed(self, client: Any) -> None:
        data = post(client, "/api/groq/test", {"model": "qwen/qwen3.8-27b"}).get_json()
        assert data["modelAvailable"] is True and "qwen/qwen3-32b" in data["models"] and data["host"] == "groq.test"

    def test_model_missing(self, client: Any) -> None:
        assert post(client, "/api/groq/test", {"model": "mistral"}).get_json()["modelAvailable"] is False

    def test_no_models_endpoint(self, client: Any, fake: Any) -> None:
        fake.fail("groq.models", 404)
        data = post(client, "/api/groq/test", {}).get_json()
        assert data["models"] == [] and data["modelAvailable"] is None


# =============================================================================
# Pollinations
# =============================================================================

class TestImageGenerate:
    def test_generates_an_image(self, client: Any, fake: Any) -> None:
        data = post(client, "/api/image/generate", {"prompt": "A red fox / in snow", "aspect": "portrait"}).get_json()
        assert data["mimeType"] == "image/png" and (data["width"], data["height"]) == (1024, 1280)
        assert base64.b64decode(data["image"]) == fake.generated_image
        call = fake.calls[0]
        url = urlparse(call.url)
        assert unquote(url.path.rsplit("/", 1)[1]) == "A red fox / in snow"
        query = parse_qs(url.query)
        assert query["width"] == ["1024"] and query["nologo"] == ["true"] and query["seed"] == [str(data["seed"])]
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-pollinations-key"

    @pytest.mark.parametrize("body", [{"prompt": "ab"}, {"prompt": "x" * 1001}, {"prompt": 5},
                                      {"prompt": "a fox", "aspect": "wide"}])
    def test_validation(self, client: Any, body: dict[str, Any]) -> None:
        assert post(client, "/api/image/generate", body).status_code == 400

    def test_not_an_image(self, client: Any, fake: Any) -> None:
        fake.generated_image = b"<html>quota exceeded</html>"
        response = post(client, "/api/image/generate", {"prompt": "a fox"})
        assert response.status_code == 502 and response.get_json()["error"]["kind"] == "parse"

    def test_not_configured(self, make_app: Any) -> None:
        response = post(make_app(POLLINATIONS_API_KEY="").test_client(), "/api/image/generate", {"prompt": "a fox"})
        assert response.status_code == 503


class TestImageEdit:
    def test_edits_an_image(self, client: Any, fake: Any) -> None:
        source = image_bytes("JPEG", (80, 60), (20, 160, 90))
        data = post(client, "/api/image/edit", {"prompt": "Make the  sky\npink", "image": b64(source)}).get_json()
        assert data["mimeType"] == "image/png" and data["model"] == "kontext" and data["retries"] == []
        assert base64.b64decode(data["image"]) == fake.edited_image
        [call] = fake.calls
        assert (call.method, call.op) == ("POST", "pollinations.edit")
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-pollinations-key"
        body = call.json
        assert body["prompt"] == "Make the sky pink" and body["model"] == "kontext" and body["seed"] == data["seed"]
        assert body["response_format"] == "b64_json"
        prefix, _, encoded = body["image"][0]["image_url"].partition(",")
        assert prefix == "data:image/jpeg;base64" and base64.b64decode(encoded) == source

    def test_edit_model_from_the_environment(self, make_app: Any, fake: Any) -> None:
        client = make_app(POLLINATIONS_EDIT_MODEL="nanobanana").test_client()
        assert page_config(client.get("/").get_data(as_text=True))["imageGen"]["editModel"] == "nanobanana"
        post(client, "/api/image/edit", {"prompt": "add a hat", "image": b64(image_bytes("PNG"))})
        assert fake.calls[0].json["model"] == "nanobanana"
        assert fake.calls[0].json["image"][0]["image_url"].startswith("data:image/png;base64,")

    @pytest.mark.parametrize("body", [{"prompt": "add a hat"}, {"prompt": "add a hat", "image": "not base64!"},
                                      {"prompt": "add a hat", "image": b64(b"GIF89a not supported")},
                                      {"prompt": "ab", "image": b64(image_bytes())}])
    def test_validation(self, client: Any, fake: Any, body: dict[str, Any]) -> None:
        assert post(client, "/api/image/edit", body).status_code == 400
        assert fake.calls == []

    def test_retries_a_refusal(self, client: Any, fake: Any) -> None:
        fake.fail("pollinations.edit", 503)
        data = post(client, "/api/image/edit", {"prompt": "add a hat", "image": b64(image_bytes())}).get_json()
        assert base64.b64decode(data["image"]) == fake.edited_image and len(data["retries"]) == 1

    def test_does_not_retry_what_may_have_been_billed(self, client: Any, fake: Any) -> None:
        fake.fail("pollinations.edit", 500)
        response = post(client, "/api/image/edit", {"prompt": "add a hat", "image": b64(image_bytes())})
        assert response.status_code == 502 and response.get_json()["error"]["status"] == 500
        assert fake.ops() == ["pollinations.edit"]

    @pytest.mark.parametrize("reply", [{"data": []}, {"data": [{"b64_json": "%%%"}]},
                                       {"data": [{"b64_json": base64.b64encode(b"<html>no</html>").decode()}]}])
    def test_reply_without_an_image(self, client: Any, fake: Any, reply: dict[str, Any]) -> None:
        fake.fail("pollinations.edit", 200, reply)
        response = post(client, "/api/image/edit", {"prompt": "add a hat", "image": b64(image_bytes())})
        assert response.status_code == 502 and response.get_json()["error"]["kind"] == "parse"

    def test_not_configured(self, make_app: Any, fake: Any) -> None:
        response = post(make_app(POLLINATIONS_API_KEY="").test_client(), "/api/image/edit",
                        {"prompt": "add a hat", "image": b64(image_bytes())})
        assert response.status_code == 503 and fake.calls == []


# =============================================================================
# Facebook and Instagram: account, scheduling and the queue
# =============================================================================

def queue(client: Any) -> list[dict[str, Any]]:
    """Every post in the app's queue, soonest first."""
    return list(client.application.extensions["poster"].social.store.page(None, "asc", 0, 1000))


def add_post(client: Any, text: str, days: float, *, status: str = "scheduled", channel: str = "pg1",
             media: str | None = "https://img.example.com/x.jpg", **fields: Any) -> str:
    """Puts a post straight into the app's queue and returns its ID."""
    instagram = channel == "ig1"
    row = {"id": uuid.uuid4().hex, "request_key": uuid.uuid4().hex, "channel_id": channel,
           "service": "instagram" if instagram else "facebook", "channel_name": "Poster Studio IG" if instagram else "Poster Studio",
           "text": text, "media_url": media, "due_at": future(days), "status": status, "created_at": "2026-01-01T00:00:00Z",
           **fields}
    return str(client.application.extensions["poster"].social.store.insert([row])[0]["id"])


class TestMetaAccount:
    def test_validate(self, client: Any) -> None:
        account = post(client, "/api/social/validate", {}).get_json()["account"]
        assert account == {"id": "su1", "name": "Poster System User"}

    def test_connect_lists_pages_and_their_instagram_accounts(self, client: Any, fake: Any) -> None:
        data = post(client, "/api/social/connect", {}).get_json()
        assert [(c["id"], c["service"], c["usable"]) for c in data["channels"]] == [
            ("pg1", "facebook", True), ("ig1", "instagram", True), ("pg2", "facebook", False)]
        instagram = data["channels"][1]
        assert instagram["handle"] == "poster.studio" and instagram["pageId"] == "pg1" and instagram["problem"] is None
        assert "full control" in data["channels"][2]["problem"]
        assert data["tokenId"] == hashlib.sha256(META_TOKEN.encode()).hexdigest()[:12]
        assert "page-token" not in json.dumps(data)  # Page tokens never leave the server
        assert fake.calls[1].kwargs["params"]["access_token"] == META_TOKEN

    def test_expired_token(self, client: Any, fake: Any) -> None:
        fake.fail("meta.me", 400, {"error": {"message": "Session has expired", "type": "OAuthException", "code": 190,
                                             "error_subcode": 463}})
        error = post(client, "/api/social/validate", {}).get_json()["error"]
        assert error["kind"] == "auth" and error["status"] == 401 and "subcode 463" in error["details"]
        assert "META_ACCESS_TOKEN" in error["hint"]

    def test_missing_permission(self, client: Any, fake: Any) -> None:
        fake.fail("meta.accounts", 403, {"error": {"message": "(#10) Permission denied", "code": 10}})
        error = post(client, "/api/social/connect", {}).get_json()["error"]
        assert error["kind"] == "auth" and error["status"] == 403 and "pages_manage_posts" in error["hint"]

    def test_rate_limit_is_retried(self, client: Any, fake: Any) -> None:
        fake.fail("meta.me", 400, {"error": {"message": "Application request limit reached", "code": 4}})
        assert len(post(client, "/api/social/validate", {}).get_json()["retries"]) == 1

    def test_appsecret_proof(self, make_app: Any, fake: Any) -> None:
        post(make_app(META_APP_SECRET="s3cret").test_client(), "/api/social/validate", {})
        expected = hmac.new(b"s3cret", META_TOKEN.encode(), hashlib.sha256).hexdigest()
        assert fake.calls[0].kwargs["params"]["appsecret_proof"] == expected

    def test_no_pages(self, client: Any, fake: Any) -> None:
        fake.meta.pages = []
        error = post(client, "/api/social/connect", {}).get_json()["error"]
        assert "manages no Facebook Pages" in error["message"]

    def test_not_configured(self, make_app: Any) -> None:
        response = post(make_app(META_ACCESS_TOKEN="").test_client(), "/api/social/connect", {})
        assert response.status_code == 503 and response.get_json()["error"]["kind"] == "config"


def schedule_body(**overrides: Any) -> dict[str, Any]:
    return {"text": "Hello world #test", "channelIds": ["pg1", "ig1"], "dueAt": future(),
            "mediaUrl": "https://cdn.example.com/a.jpg", "requestKey": "req-1", **overrides}


class TestSchedule:
    def test_queues_one_post_per_channel(self, client: Any, fake: Any) -> None:
        body = schedule_body()
        data = post(client, "/api/social/schedule", body).get_json()
        assert [p["channelId"] for p in data["posts"]] == ["pg1", "ig1"] and data["failures"] == []
        assert all(p["status"] == "scheduled" and p["dueAt"] == body["dueAt"] for p in data["posts"])
        # Nothing is published until the post falls due.
        assert fake.ops() == ["meta.accounts"] and fake.meta.all_posts() == []
        assert {(row["service"], row["channel_name"]) for row in queue(client)} == {
            ("facebook", "Poster Studio"), ("instagram", "Poster Studio IG")}

    def test_the_same_request_is_never_queued_twice(self, client: Any) -> None:
        first = post(client, "/api/social/schedule", schedule_body()).get_json()["posts"]
        again = post(client, "/api/social/schedule", schedule_body()).get_json()["posts"]
        assert again == first and len(queue(client)) == 2
        post(client, "/api/social/schedule", schedule_body(requestKey="req-2"))
        assert len(queue(client)) == 4

    def test_channels_that_cannot_take_the_post(self, client: Any) -> None:
        data = post(client, "/api/social/schedule", schedule_body(channelIds=["pg1", "ig1", "pg2", "gone"], mediaUrl=None)).get_json()
        assert [p["channelId"] for p in data["posts"]] == ["pg1"]
        messages = {f["channelId"]: f["message"] for f in data["failures"]}
        assert messages["ig1"] == "Instagram posts need an image."
        assert "full control" in messages["pg2"] and "Reconnect" in messages["gone"]
        assert not any(f["uncertain"] for f in data["failures"])

    @pytest.mark.parametrize(("text", "message"), [("x" * 2201, "2,200 characters"),
                                                   (" ".join(f"#tag{i}" for i in range(31)), "30 hashtags")])
    def test_instagram_limits(self, client: Any, text: str, message: str) -> None:
        data = post(client, "/api/social/schedule", schedule_body(text=text)).get_json()
        assert [p["channelId"] for p in data["posts"]] == ["pg1"] and message in data["failures"][0]["message"]

    def test_every_channel_failing(self, client: Any) -> None:
        response = post(client, "/api/social/schedule", schedule_body(channelIds=["ig1"], mediaUrl=None))
        assert response.status_code == 502
        assert response.get_json()["error"]["message"] == "The post was not scheduled. Poster Studio IG: Instagram posts need an image."
        assert queue(client) == []

    def test_instagram_gets_a_jpeg(self, blob_client: Any) -> None:
        served = "https://poster.test/api/media/image/sea-0a1b2c3d4e5f.png"
        post(blob_client, "/api/social/schedule", schedule_body(mediaUrl=served))
        urls = {row["service"]: row["media_url"] for row in queue(blob_client)}
        assert urls == {"facebook": served, "instagram": served.replace(".png", ".jpg")}

    def test_a_bad_token_queues_nothing(self, client: Any, fake: Any) -> None:
        fake.fail("meta.accounts", 400, {"error": {"message": "Error validating access token", "code": 190}})
        response = post(client, "/api/social/schedule", schedule_body())
        assert response.status_code == 502 and response.get_json()["error"]["kind"] == "auth"
        assert queue(client) == []

    def test_due_at_in_another_timezone(self, client: Any) -> None:
        due = future(1).replace("Z", "+05:30")
        post(client, "/api/social/schedule", schedule_body(dueAt=due, channelIds=["pg1"]))
        stored = queue(client)[0]["due_at"]
        assert stored.endswith("Z") and stored != due

    @pytest.mark.parametrize("overrides", [
        {"text": "  "}, {"text": "x" * 10_001}, {"channelIds": []}, {"channelIds": ["a b"]}, {"channelIds": [f"c{i}" for i in range(26)]},
        {"dueAt": "2020-01-01T00:00:00Z"}, {"dueAt": "2030-01-01T00:00:00"}, {"dueAt": "tomorrow"}, {"dueAt": future(800)},
        {"mediaUrl": "http://cdn.example.com/a.jpg"}, {"mediaUrl": "https://localhost/a.jpg"}, {"mediaUrl": 5},
        {"requestKey": "bad key"}, {"requestKey": 7},
    ])
    def test_validation(self, client: Any, fake: Any, overrides: dict[str, Any]) -> None:
        response = post(client, "/api/social/schedule", schedule_body(**overrides))
        assert response.status_code == 400
        assert not fake.calls


@pytest.fixture
def seeded(client: Any) -> dict[str, str]:
    """Two scheduled posts, one being published, one published and one that failed."""
    ids = {}
    for name, days, status in (("later", 3, "scheduled"), ("soon", 1, "scheduled"), ("middle", 2, "sending"),
                               ("done", -1, "sent"), ("failed", -2, "error")):
        extra: dict[str, Any] = {}
        if status == "sent":
            extra = {"permalink": "https://www.facebook.com/pg1_9", "sent_at": future(-1)}
        if status == "error":
            extra = {"error": "Meta could not publish the Facebook post: Invalid image", "attempts": 1}
        ids[name] = add_post(client, f"{name} post", days, status=status, **extra)
    return ids


class TestPosts:
    def test_upcoming_soonest_first(self, client: Any, fake: Any, seeded: dict[str, str]) -> None:
        data = post(client, "/api/social/posts", {"filter": "upcoming"}).get_json()
        assert [p["text"] for p in data["posts"]] == ["soon post", "middle post", "later post"]
        assert data["next"] is None
        first, middle = data["posts"][0], data["posts"][1]
        assert first["channelName"] == "Poster Studio" and first["service"] == "facebook" and first["published"] is False
        assert first["thumbnail"] == "https://img.example.com/x.jpg"
        assert middle["status"] == "sending" and middle["published"] is True
        assert not fake.calls  # the list comes from the queue, not from Meta

    def test_published(self, client: Any, seeded: dict[str, str]) -> None:
        posts = post(client, "/api/social/posts", {"filter": "published"}).get_json()["posts"]
        assert [p["text"] for p in posts] == ["done post"]
        assert posts[0]["published"] is True and posts[0]["externalLink"] == "https://www.facebook.com/pg1_9"

    def test_all_newest_first_with_failures(self, client: Any, seeded: dict[str, str]) -> None:
        posts = post(client, "/api/social/posts", {}).get_json()["posts"]
        assert [p["text"] for p in posts] == ["later post", "middle post", "soon post", "done post", "failed post"]
        failed = posts[-1]
        assert failed["status"] == "error" and failed["published"] is False and "Invalid image" in failed["error"]

    def test_pagination(self, client: Any) -> None:
        for i in range(35):
            add_post(client, f"post {i:02d}", 1 + i / 100)
        first = post(client, "/api/social/posts", {"filter": "upcoming"}).get_json()
        assert len(first["posts"]) == 30 and first["next"] == "30"
        second = post(client, "/api/social/posts", {"filter": "upcoming", "after": first["next"]}).get_json()
        assert [p["text"] for p in second["posts"]] == [f"post {i}" for i in range(30, 35)] and second["next"] is None

    def test_stored_thumbnails_replace_served_images(self, blob_client: Any) -> None:
        add_post(blob_client, "mine", 1, media="https://poster.test/api/media/image/sea-0a1b2c3d4e5f.jpg")
        posts = post(blob_client, "/api/social/posts", {"filter": "all"}).get_json()["posts"]
        assert posts[0]["thumbnail"] == f"https://{BLOB_PUBLIC_HOST}/poster/sea-0a1b2c3d4e5f.thumb.avif"

    def test_reports_when_the_publisher_last_ran(self, client: Any) -> None:
        assert post(client, "/api/social/posts", {}).get_json()["publisher"] == {"lastRunAt": None}
        client.get("/api/publish/due", headers={"Authorization": "Bearer test-cron-secret"})
        last_run = post(client, "/api/social/posts", {}).get_json()["publisher"]["lastRunAt"]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", last_run)

    @pytest.mark.parametrize("body", [{"filter": "drafts"}, {"after": {"org1": "30"}}, {"after": "abc"}, {"after": 30}])
    def test_validation(self, client: Any, body: dict[str, Any]) -> None:
        assert post(client, "/api/social/posts", body).status_code == 400


class TestEditDelete:
    def test_edit_text_and_time(self, client: Any, fake: Any, seeded: dict[str, str]) -> None:
        due = future(5)
        data = post(client, "/api/social/posts/edit", {"postIds": [seeded["soon"]], "text": "new text", "dueAt": due}).get_json()
        assert data["failures"] == []
        assert data["posts"][0]["text"] == "new text" and data["posts"][0]["dueAt"] == due
        assert not fake.calls

    def test_editing_a_failed_post_puts_it_back_in_the_queue(self, client: Any, seeded: dict[str, str]) -> None:
        edited = post(client, "/api/social/posts/edit", {"postIds": [seeded["failed"]], "dueAt": future(1)}).get_json()["posts"][0]
        assert edited["status"] == "scheduled" and edited["error"] is None and edited["attempts"] == 0

    def test_edit_refuses_published_and_reports_missing(self, client: Any, seeded: dict[str, str]) -> None:
        body = {"postIds": [seeded["done"], seeded["middle"], "gone1"], "text": "x"}
        data = post(client, "/api/social/posts/edit", body).get_json()
        assert data["posts"] == []
        assert [(f["postId"], f["reason"]) for f in data["failures"]] == [
            (seeded["done"], "published"), (seeded["middle"], "published"), ("gone1", "missing")]
        assert "being published right now" in data["failures"][1]["message"]

    def test_edit_keeps_instagram_limits(self, client: Any) -> None:
        post_id = add_post(client, "short", 1, channel="ig1")
        failure = post(client, "/api/social/posts/edit", {"postIds": [post_id], "text": "x" * 2201}).get_json()["failures"][0]
        assert failure["reason"] == "error" and "2,200 characters" in failure["message"]

    def test_edit_needs_a_change(self, client: Any, seeded: dict[str, str]) -> None:
        assert post(client, "/api/social/posts/edit", {"postIds": [seeded["soon"]]}).status_code == 400

    def test_delete(self, client: Any, seeded: dict[str, str]) -> None:
        body = {"postIds": [seeded["soon"], "gone1", seeded["done"], seeded["failed"]]}
        data = post(client, "/api/social/posts/delete", body).get_json()
        assert data["deleted"] == [seeded["soon"], "gone1", seeded["failed"]]
        assert [(f["postId"], f["reason"]) for f in data["failures"]] == [(seeded["done"], "published")]
        assert {row["id"] for row in queue(client)} == {seeded["later"], seeded["middle"], seeded["done"]}

    @pytest.mark.parametrize("ids", [[], "post1", ["bad id"], [f"p{i}" for i in range(26)]])
    def test_post_id_validation(self, client: Any, ids: Any) -> None:
        assert post(client, "/api/social/posts/delete", {"postIds": ids}).status_code == 400


# =============================================================================
# Media
# =============================================================================

def upload(client: Any, data: bytes, name: str = "Beach Day.jpg") -> Any:
    return client.post("/api/media/upload", data={"file": (io.BytesIO(data), name)}, headers=API_HEADERS)


class TestMediaUpload:
    def test_endpoint_upload(self, make_app: Any, fake: Any) -> None:
        response = upload(make_app(**CLOUDINARY_ENV).test_client(), image_bytes())
        assert response.get_json()["url"] == "https://res.media.test/poster/uploaded.jpg"
        call = fake.calls[0]
        assert call.kwargs["data"] == {"upload_preset": "poster_unsigned"}
        assert call.kwargs["files"]["file"][0] == "Beach_Day.jpg"

    def test_blob_upload_stores_avif_copies(self, blob_client: Any, fake: Any) -> None:
        data = upload(blob_client, image_bytes("PNG", (800, 600))).get_json()
        assert re.fullmatch(r"beach-day-[0-9a-f]{12}", data["name"])
        assert data["url"] == f"https://poster.test/api/media/image/{data['name']}.png"
        assert data["avifUrl"] == f"https://{BLOB_PUBLIC_HOST}/poster/{data['name']}.avif"
        assert data["thumbnailUrl"] == f"https://{BLOB_PUBLIC_HOST}/poster/{data['name']}.thumb.avif"
        puts = [c for c in fake.calls if c.op == "blob.put"]
        assert len(puts) == 2
        assert puts[0].kwargs["headers"]["x-vercel-blob-access"] == "public"
        for stored in fake.blobs.values():
            assert stored[4:12] == b"ftypavif"
        with Image.open(io.BytesIO(fake.blobs[data["thumbnailUrl"]])) as thumb:
            assert max(thumb.size) <= 400

    def test_blob_upload_needs_a_public_address(self, make_app: Any) -> None:
        client = make_app(BLOB_READ_WRITE_TOKEN=BLOB_ENV["BLOB_READ_WRITE_TOKEN"]).test_client()
        response = upload(client, image_bytes())
        assert response.status_code == 503 and "local address" in response.get_json()["error"]["message"]

    def test_rejects_non_images(self, blob_client: Any) -> None:
        assert upload(blob_client, b"GIF89a not allowed").status_code == 400
        assert blob_client.post("/api/media/upload", data={}, headers=API_HEADERS).status_code == 400

    def test_not_configured(self, client: Any) -> None:
        response = upload(client, image_bytes())
        assert response.status_code == 503 and response.get_json()["error"]["kind"] == "config"


class TestMediaImage:
    @pytest.fixture
    def stored(self, blob_client: Any) -> str:
        return upload(blob_client, image_bytes("PNG", (300, 200), (10, 200, 30, 128))).get_json()["name"]

    @pytest.mark.parametrize(("suffix", "mimetype", "fmt"), [(".jpg", "image/jpeg", "JPEG"), (".jpeg", "image/jpeg", "JPEG"),
                                                             (".png", "image/png", "PNG"), (".webp", "image/webp", "WEBP"),
                                                             ("", "image/avif", "AVIF"), (".avif", "image/avif", "AVIF")])
    def test_serves_each_format(self, blob_client: Any, stored: str, suffix: str, mimetype: str, fmt: str) -> None:
        response = blob_client.get(f"/api/media/image/{stored}{suffix}")
        assert response.status_code == 200 and response.mimetype == mimetype
        assert "immutable" in response.headers["Cache-Control"]
        with Image.open(io.BytesIO(response.data)) as image:
            assert image.format == fmt and image.size == (300, 200)

    def test_unknown_image(self, blob_client: Any) -> None:
        response = blob_client.get("/api/media/image/nothing-here.jpg")
        assert response.status_code == 404 and response.is_json

    def test_bad_name(self, blob_client: Any, fake: Any) -> None:
        assert blob_client.get("/api/media/image/bad_name.jpg").status_code == 400
        assert not fake.calls

    def test_without_blob(self, client: Any) -> None:
        assert client.get("/api/media/image/some-name.jpg").status_code == 503
