"""HTTP-level behaviour of every route, against FakeUpstream.

These tests pin down the API contract the dashboard relies on, so the server can be
restructured underneath them.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from PIL import Image

from tests.conftest import API_HEADERS, BLOB_ENV, CLOUDINARY_ENV, post
from tests.fakes import BLOB_PUBLIC_HOST, image_bytes


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
        assert config["ollama"]["configured"] and config["buffer"]["configured"]
        for secret in ("test-ollama-key", "test-buffer-token", "test-pollinations-key"):
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
        assert health["ollama"] and health["buffer"] and health["imageGen"] and not health["media"]
        config = client.get("/api/config").get_json()
        assert set(config) >= {"version", "mode", "ollama", "buffer", "media", "imageGen", "limits"}
        assert config["buffer"]["tokenId"] == hashlib.sha256(b"test-buffer-token").hexdigest()[:12]
        assert config["ollama"]["requestTimeout"] == (120 * 4 + 7) * 2 + 15

    @pytest.mark.parametrize(("env", "mode"), [({"POSTER_MODE": "demo"}, "demo"), ({"OLLAMA_ENDPOINT": "https://ollama.com",
                                                "OLLAMA_API_KEY": ""}, "demo"), ({}, "live")])
    def test_default_mode(self, make_app: Any, env: dict[str, str], mode: str) -> None:
        assert make_app(**env).test_client().get("/api/config").get_json()["mode"] == mode


class TestGuard:
    def test_post_without_header_is_refused(self, client: Any, fake: Any) -> None:
        response = client.post("/api/buffer/validate", json={})
        assert response.status_code == 403
        assert response.get_json()["error"]["message"] == "Missing X-Poster-Request header."
        assert not fake.calls

    def test_cross_origin_post_is_refused(self, client: Any) -> None:
        response = post(client, "/api/buffer/validate", {}, headers={"Origin": "https://evil.test"})
        assert response.status_code == 403

    def test_same_origin_post_is_accepted(self, client: Any) -> None:
        assert post(client, "/api/buffer/validate", {}, headers={"Origin": "http://localhost"}).status_code == 200

    def test_non_json_body(self, client: Any) -> None:
        response = client.post("/api/buffer/schedule", data="x", headers=API_HEADERS)
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
        fake.fail("buffer.PosterAccount", exc=RuntimeError("secret internals"))
        response = post(client, "/api/buffer/validate", {})
        assert response.status_code == 500
        assert response.get_json() == {"error": {"message": "Unexpected server error. Check the server log.", "kind": "server"}}

    def test_upload_over_the_body_limit(self, blob_client: Any) -> None:
        big = io.BytesIO(b"\xff\xd8\xff" + b"0" * (22 * 1024 * 1024))
        response = blob_client.post("/api/media/upload", data={"file": (big, "big.jpg")}, headers=API_HEADERS)
        assert response.status_code == 413
        assert "larger than the server allows" in response.get_json()["error"]["message"]


# =============================================================================
# Ollama
# =============================================================================

def generate_body(**overrides: Any) -> dict[str, Any]:
    return {"model": "gemma4:31b", "tone": "friendly", "extraInstructions": "", "descMin": 10, "descMax": 2200,
            "image": b64(image_bytes()), "fileName": "beach.jpg", **overrides}


class TestOllamaGenerate:
    def test_normalised_content(self, client: Any, fake: Any) -> None:
        response = post(client, "/api/ollama/generate", generate_body())
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
        assert chat.op == "ollama.chat" and chat.url == "https://ollama.test/api/chat"
        assert chat.kwargs["headers"]["Authorization"] == "Bearer test-ollama-key"
        assert chat.json["format"] == "json" and chat.json["stream"] is False
        user = chat.json["messages"][1]
        assert user["images"] and "warm, conversational" in user["content"]

    def test_openai_compatible_endpoint(self, make_app: Any, fake: Any) -> None:
        client = make_app(OLLAMA_ENDPOINT="https://ollama.test/v1/chat/completions").test_client()
        assert post(client, "/api/ollama/generate", generate_body()).status_code == 200
        body = fake.calls[0].json
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_tolerates_fences_think_tags_aliases_and_inline_tags(self, client: Any, fake: Any) -> None:
        fake.reply('<think>hmm</think>```json\n{"caption": "Coffee time is the best time of day #coffee #latte", '
                   '"tags": "cafe, morning", "score": "73", "categories": ["food"],}\n```')
        data = post(client, "/api/ollama/generate", generate_body()).get_json()
        content = data["content"]
        assert content["description"] == "Coffee time is the best time of day"
        assert content["hashtags"][:4] == ["#cafe", "#morning", "#coffee", "#latte"]
        assert len(content["hashtags"]) == 10
        assert content["category"] == "Food" and content["engagement_score"] == 73
        assert "No alternative caption was returned." in data["issues"]

    def test_repairs_malformed_reply_once(self, client: Any, fake: Any) -> None:
        fake.reply("Sorry, here you go: not json", json.dumps({"description": "Fixed caption for the image", "hashtags": []}))
        data = post(client, "/api/ollama/generate", generate_body()).get_json()
        assert data["repaired"] is True
        assert data["content"]["description"] == "Fixed caption for the image"
        second = fake.calls[1].json["messages"]
        assert second[-2] == {"role": "assistant", "content": "Sorry, here you go: not json"}
        assert "ONLY one JSON object" in second[-1]["content"]

    def test_gives_up_after_one_repair(self, client: Any, fake: Any) -> None:
        fake.reply("nope", "still nope")
        response = post(client, "/api/ollama/generate", generate_body())
        assert response.status_code == 502
        assert response.get_json()["error"]["kind"] == "parse"
        assert fake.ops() == ["ollama.chat", "ollama.chat"]

    def test_auth_failure_is_not_retried(self, client: Any, fake: Any) -> None:
        fake.fail("ollama.chat", 401, {"error": "unauthorized"})
        response = post(client, "/api/ollama/generate", generate_body())
        assert response.status_code == 502
        error = response.get_json()["error"]
        assert error["kind"] == "auth" and error["status"] == 401 and error["service"] == "Ollama"
        assert fake.ops() == ["ollama.chat"]

    def test_server_error_is_retried(self, client: Any, fake: Any, waits: list[float]) -> None:
        fake.fail("ollama.chat", 503, times=2)
        data = post(client, "/api/ollama/generate", generate_body()).get_json()
        assert [r["attempt"] for r in data["retries"]] == [1, 2]
        assert waits == [1, 2]

    def test_retries_are_reported_on_failure(self, client: Any, fake: Any, waits: list[float]) -> None:
        fake.fail("ollama.chat", 500, times=4)
        error = post(client, "/api/ollama/generate", generate_body()).get_json()["error"]
        assert error["kind"] == "server" and len(error["retries"]) == 3
        assert waits == [1, 2, 4]

    @pytest.mark.parametrize("overrides", [
        {"image": ""}, {"image": "!!!"}, {"image": b64(b"GIF89a....")}, {"tone": "angry"}, {"descMin": 50, "descMax": 40},
        {"model": "bad model!"}, {"extraInstructions": "x" * 501}, {"descMax": "long"},
    ])
    def test_validation(self, client: Any, fake: Any, overrides: dict[str, Any]) -> None:
        response = post(client, "/api/ollama/generate", generate_body(**overrides))
        assert response.status_code == 400
        assert response.get_json()["error"]["kind"] == "validation"
        assert not fake.calls

    def test_data_url_image_is_accepted(self, client: Any) -> None:
        body = generate_body(image="data:image/png;base64," + b64(image_bytes("PNG")))
        assert post(client, "/api/ollama/generate", body).status_code == 200

    def test_not_configured(self, make_app: Any) -> None:
        client = make_app(OLLAMA_ENDPOINT="https://ollama.com", OLLAMA_API_KEY="").test_client()
        response = post(client, "/api/ollama/generate", generate_body())
        assert response.status_code == 503
        assert response.get_json()["error"]["kind"] == "config"


class TestOllamaTest:
    def test_model_listed(self, client: Any) -> None:
        data = post(client, "/api/ollama/test", {"model": "gemma4:31b"}).get_json()
        assert data["modelAvailable"] is True and "llava:13b" in data["models"] and data["host"] == "ollama.test"

    def test_model_missing(self, client: Any) -> None:
        assert post(client, "/api/ollama/test", {"model": "mistral"}).get_json()["modelAvailable"] is False

    def test_no_tags_endpoint(self, client: Any, fake: Any) -> None:
        fake.fail("ollama.tags", 404)
        data = post(client, "/api/ollama/test", {}).get_json()
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
# Buffer
# =============================================================================

class TestBufferAccount:
    def test_validate(self, client: Any) -> None:
        account = post(client, "/api/buffer/validate", {}).get_json()["account"]
        assert account == {"id": "acc1", "email": "owner@example.test", "organizations": [{"id": "org1", "name": "Org One"}]}

    def test_connect_lists_channels(self, client: Any, fake: Any) -> None:
        data = post(client, "/api/buffer/connect", {}).get_json()
        assert [c["id"] for c in data["channels"]] == ["ch1", "ch2", "ch3"]
        first = data["channels"][0]
        assert first["name"] == "Poster Studio" and first["handle"] == "poster.studio" and first["usable"] is True
        assert data["channels"][2]["usable"] is False and data["channels"][2]["isDisconnected"] is True
        assert data["tokenId"] == hashlib.sha256(b"test-buffer-token").hexdigest()[:12]
        assert fake.calls[0].kwargs["headers"]["Authorization"] == "Bearer test-buffer-token"

    def test_graphql_auth_error(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterAccount", 200, {"errors": [{"message": "bad token", "extensions": {"code": "UNAUTHENTICATED"}}]})
        error = post(client, "/api/buffer/validate", {}).get_json()["error"]
        assert error["kind"] == "auth" and error["status"] == 401

    def test_graphql_rate_limit_is_retried(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterAccount", 200, {"errors": [{"message": "slow down", "extensions": {"code": "RATE_LIMIT_EXCEEDED"}}]})
        assert len(post(client, "/api/buffer/validate", {}).get_json()["retries"]) == 1

    def test_not_configured(self, make_app: Any) -> None:
        response = post(make_app(BUFFER_ACCESS_TOKEN="").test_client(), "/api/buffer/connect", {})
        assert response.status_code == 503 and response.get_json()["error"]["kind"] == "config"


def schedule_body(**overrides: Any) -> dict[str, Any]:
    return {"text": "Hello world #test", "channelIds": ["ch1", "ch2"], "dueAt": future(), "mediaUrl": None, **overrides}


class TestBufferSchedule:
    def test_one_post_per_channel(self, client: Any, fake: Any) -> None:
        body = schedule_body(mediaUrl="https://cdn.example.com/a.jpg")
        data = post(client, "/api/buffer/schedule", body).get_json()
        assert [p["channelId"] for p in data["posts"]] == ["ch1", "ch2"] and data["failures"] == []
        assert all(p["status"] == "scheduled" and p["postId"] for p in data["posts"])
        created = fake.calls[0].json["variables"]["input"]
        assert created["mode"] == "customScheduled" and created["schedulingType"] == "automatic"
        assert created["assets"] == [{"image": {"url": "https://cdn.example.com/a.jpg"}}]
        assert created["dueAt"] == body["dueAt"]

    def test_partial_failure(self, client: Any, fake: Any) -> None:
        data = post(client, "/api/buffer/schedule", schedule_body(channelIds=["ch1", "chX"])).get_json()
        assert len(data["posts"]) == 1
        assert data["failures"] == [{"channelId": "chX", "message": "Unknown channel", "uncertain": False}]

    def test_every_channel_failing(self, client: Any) -> None:
        response = post(client, "/api/buffer/schedule", schedule_body(channelIds=["chX"]))
        assert response.status_code == 502
        assert response.get_json()["error"]["message"] == "Buffer did not schedule the post. chX: Unknown channel"

    def test_server_error_is_never_retried(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterCreatePost", 500)
        data = post(client, "/api/buffer/schedule", schedule_body()).get_json()
        assert len(data["posts"]) == 1 and data["failures"][0]["channelId"] == "ch1"
        assert fake.ops("buffer.PosterCreatePost") == ["buffer.PosterCreatePost"] * 2

    def test_rate_limit_is_retried(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterCreatePost", 429)
        data = post(client, "/api/buffer/schedule", schedule_body(channelIds=["ch1"])).get_json()
        assert len(data["posts"]) == 1 and len(data["retries"]) == 1

    def test_auth_failure_stops_the_run(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterCreatePost", 401)
        response = post(client, "/api/buffer/schedule", schedule_body())
        assert response.status_code == 502 and response.get_json()["error"]["kind"] == "auth"
        assert not fake.buffer.posts

    @pytest.mark.parametrize("overrides", [
        {"text": "  "}, {"text": "x" * 10_001}, {"channelIds": []}, {"channelIds": ["a b"]}, {"channelIds": [f"c{i}" for i in range(26)]},
        {"dueAt": "2020-01-01T00:00:00Z"}, {"dueAt": "2030-01-01T00:00:00"}, {"dueAt": "tomorrow"}, {"dueAt": future(800)},
        {"mediaUrl": "http://cdn.example.com/a.jpg"}, {"mediaUrl": "https://localhost/a.jpg"}, {"mediaUrl": 5},
    ])
    def test_validation(self, client: Any, fake: Any, overrides: dict[str, Any]) -> None:
        response = post(client, "/api/buffer/schedule", schedule_body(**overrides))
        assert response.status_code == 400
        assert not fake.calls


@pytest.fixture
def seeded(fake: Any) -> dict[str, str]:
    """Three upcoming posts and one published post on ch1."""
    ids = {}
    for name, days, status in (("later", 3, "scheduled"), ("soon", 1, "scheduled"), ("middle", 2, "needs_approval"),
                               ("done", -1, "sent")):
        ids[name] = fake.buffer.add_post("ch1", f"{name} post", future(days), status=status,
                                         image="https://img.example.com/x.jpg")["id"]
    return ids


class TestBufferPosts:
    def test_upcoming_soonest_first(self, client: Any, seeded: dict[str, str]) -> None:
        data = post(client, "/api/buffer/posts", {"filter": "upcoming", "organizationIds": ["org1"]}).get_json()
        assert [p["text"] for p in data["posts"]] == ["soon post", "middle post", "later post"]
        assert data["next"] is None
        first = data["posts"][0]
        assert first["channelName"] == "Poster Studio" and first["service"] == "instagram" and first["published"] is False
        assert first["thumbnail"] == "https://img.example.com/x.jpg"

    def test_published(self, client: Any, seeded: dict[str, str]) -> None:
        posts = post(client, "/api/buffer/posts", {"filter": "published", "organizationIds": ["org1"]}).get_json()["posts"]
        assert [p["text"] for p in posts] == ["done post"]
        assert posts[0]["published"] is True and posts[0]["externalLink"].startswith("https://social.test/")

    def test_all_newest_first_and_org_lookup(self, client: Any, fake: Any, seeded: dict[str, str]) -> None:
        posts = post(client, "/api/buffer/posts", {}).get_json()["posts"]
        assert [p["text"] for p in posts] == ["later post", "middle post", "soon post", "done post"]
        assert fake.ops()[0] == "buffer.PosterAccount"

    def test_pagination(self, client: Any, fake: Any) -> None:
        for i in range(35):
            fake.buffer.add_post("ch1", f"post {i:02d}", future(1 + i / 100))
        first = post(client, "/api/buffer/posts", {"filter": "upcoming", "organizationIds": ["org1"]}).get_json()
        assert len(first["posts"]) == 30 and first["next"] == {"org1": "30"}
        second = post(client, "/api/buffer/posts", {"filter": "upcoming", "organizationIds": ["org1"],
                                                    "after": first["next"]}).get_json()
        assert len(second["posts"]) == 5 and second["next"] is None

    def test_stored_thumbnails_replace_served_images(self, blob_client: Any, fake: Any) -> None:
        fake.buffer.add_post("ch1", "mine", future(), image="https://poster.test/api/media/image/sea-0a1b2c3d4e5f.jpg")
        posts = post(blob_client, "/api/buffer/posts", {"filter": "all", "organizationIds": ["org1"]}).get_json()["posts"]
        assert posts[0]["thumbnail"] == f"https://{BLOB_PUBLIC_HOST}/poster/sea-0a1b2c3d4e5f.thumb.avif"

    @pytest.mark.parametrize("body", [{"filter": "drafts"}, {"after": {"org1": ""}}, {"after": []},
                                      {"organizationIds": "org1"}])
    def test_validation(self, client: Any, body: dict[str, Any]) -> None:
        assert post(client, "/api/buffer/posts", body).status_code == 400


class TestBufferEditDelete:
    def test_edit_text_and_time(self, client: Any, fake: Any, seeded: dict[str, str]) -> None:
        due = future(5)
        data = post(client, "/api/buffer/posts/edit", {"postIds": [seeded["soon"]], "text": "new text", "dueAt": due}).get_json()
        assert data["failures"] == [] and data["posts"][0]["text"] == "new text"
        edit = next(c for c in fake.calls if c.op == "buffer.PosterEditPost").json["variables"]["input"]
        assert edit == {"id": seeded["soon"], "text": "new text", "dueAt": due, "mode": "customScheduled"}
        assert fake.ops() == ["buffer.PosterPostStatus", "buffer.PosterEditPost"]

    def test_edit_refuses_published_and_reports_missing(self, client: Any, seeded: dict[str, str]) -> None:
        data = post(client, "/api/buffer/posts/edit", {"postIds": [seeded["done"], "gone1"], "text": "x"}).get_json()
        assert data["posts"] == []
        assert [(f["postId"], f["reason"]) for f in data["failures"]] == [(seeded["done"], "published"), ("gone1", "missing")]

    def test_edit_needs_a_change(self, client: Any, seeded: dict[str, str]) -> None:
        assert post(client, "/api/buffer/posts/edit", {"postIds": [seeded["soon"]]}).status_code == 400

    def test_delete(self, client: Any, fake: Any, seeded: dict[str, str]) -> None:
        data = post(client, "/api/buffer/posts/delete", {"postIds": [seeded["soon"], "gone1", seeded["done"]]}).get_json()
        assert data["deleted"] == [seeded["soon"], "gone1"]
        assert data["failures"][0]["reason"] == "published"
        assert seeded["soon"] not in fake.buffer.posts

    @pytest.mark.parametrize("ids", [[], "post1", ["bad id"], [f"p{i}" for i in range(26)]])
    def test_post_id_validation(self, client: Any, ids: Any) -> None:
        assert post(client, "/api/buffer/posts/delete", {"postIds": ids}).status_code == 400


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
