"""Server behaviour added with the poster package: shared rules, model allowlist, request IDs,
versioned assets, the time budget and uncertain scheduling failures."""

from __future__ import annotations

import logging
import re
from typing import Any

import pytest
import requests

from poster.config import LANGUAGES, LIMITS, TONES
from poster.content import FALLBACK_TAGS
from tests.conftest import BLOB_ENV, post
from tests.fakes import image_bytes
from tests.test_routes import b64, future, generate_body, page_config, schedule_body, upload


class TestSharedRules:
    def test_rules_reach_the_browser(self, client: Any) -> None:
        rules = page_config(client.get("/").get_data(as_text=True))["rules"]
        assert rules["tones"] == TONES
        assert rules["languages"] == {key: value["label"] for key, value in LANGUAGES.items()}
        assert rules["hashtags"]["min"] == LIMITS["min_hashtags"] and rules["hashtags"]["max"] == LIMITS["max_hashtags"]
        assert rules["hashtags"]["fallback"] == list(FALLBACK_TAGS) and "would" in rules["hashtags"]["stopWords"]
        assert rules["maxTextChars"] == LIMITS["max_text_chars"]
        assert rules["caption"] == {"minLow": 1, "minHigh": 500, "maxLow": 20, "maxHigh": 10000, "defaultMin": 10, "defaultMax": 2200}
        assert rules["postFilters"]["upcoming"] == ["scheduled", "needs_approval", "sending"]
        assert rules["retry"]["delaysMs"] == [1000, 2000, 4000]


class TestModelAllowlist:
    @pytest.fixture
    def restricted(self, make_app: Any) -> Any:
        return make_app(OLLAMA_MODELS="llava:13b").test_client()

    def test_other_models_are_refused(self, restricted: Any, fake: Any) -> None:
        response = post(restricted, "/api/ollama/generate", generate_body(model="expensive-model:405b"))
        assert response.status_code == 400
        assert "gemma4:31b, llava:13b" in response.get_json()["error"]["message"]
        assert not fake.calls

    def test_listed_models_are_accepted(self, restricted: Any) -> None:
        assert post(restricted, "/api/ollama/generate", generate_body(model="llava:13b")).status_code == 200
        assert restricted.get("/api/config").get_json()["ollama"]["models"] == ["gemma4:31b", "llava:13b"]

    def test_any_model_without_a_list(self, client: Any) -> None:
        assert post(client, "/api/ollama/generate", generate_body(model="anything:7b")).status_code == 200


class TestObservability:
    def test_request_id_is_echoed_or_made(self, client: Any) -> None:
        assert client.get("/api/health", headers={"X-Request-Id": "abc-123"}).headers["X-Request-Id"] == "abc-123"
        made = client.get("/api/health", headers={"X-Request-Id": "bad id"}).headers["X-Request-Id"]
        assert re.fullmatch(r"[0-9a-f]{12}", made)

    def test_api_requests_and_upstream_calls_are_logged(self, client: Any, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="poster"):
            post(client, "/api/buffer/validate", {}, headers={"X-Request-Id": "req-1"})
        assert re.search(r"Buffer POST api\.buffer\.test -> 200 in \d+\.\d\ds", caplog.text)
        assert re.search(r"POST /api/buffer/validate -> 200 in \d+\.\d\ds \[req-1\]", caplog.text)

    def test_file_names_cannot_forge_log_lines(self, client: Any, fake: Any, caplog: pytest.LogCaptureFixture) -> None:
        fake.fail("ollama.chat", 503)
        with caplog.at_level(logging.WARNING, logger="poster"):
            post(client, "/api/ollama/generate", generate_body(fileName="a.jpg\nCRITICAL forged"))
        assert "a.jpg CRITICAL forged" in caplog.text and "\nCRITICAL forged" not in caplog.text


class TestAssets:
    def test_page_links_versioned_assets(self, client: Any) -> None:
        html = client.get("/").get_data(as_text=True)
        version = re.search(r'/assets/([0-9a-f]{12})/js/main\.js', html).group(1)
        assert f'/assets/{version}/css/poster.css' in html and 'type="module"' in html

    def test_current_version_is_immutable(self, client: Any) -> None:
        html = client.get("/").get_data(as_text=True)
        script = re.search(r'src="(/assets/[^"]+/js/main\.js)"', html).group(1)
        response = client.get(script)
        assert response.status_code == 200 and response.mimetype == "text/javascript"
        assert "immutable" in response.headers["Cache-Control"]
        stale = client.get("/assets/000000000000/js/main.js")
        assert stale.status_code == 200 and stale.headers["Cache-Control"] == "no-cache"

    def test_csp_allows_own_scripts_only(self, client: Any) -> None:
        csp = client.get("/").headers["Content-Security-Policy"]
        assert re.search(r"script-src 'self' 'nonce-[^']+';", csp)

    @pytest.mark.parametrize("path", ["/assets/x/../app.py", "/assets/x/%2e%2e/app.py", "/assets/x/missing.js"])
    def test_no_path_traversal(self, client: Any, path: str) -> None:
        assert client.get(path).status_code == 404


class TestMediaImageUrls:
    def test_query_strings_redirect_to_the_one_url(self, blob_client: Any, fake: Any) -> None:
        name = upload(blob_client, image_bytes("PNG", (40, 30))).get_json()["name"]
        response = blob_client.get(f"/api/media/image/{name}.jpg?cachebust=1")
        assert response.status_code == 308 and response.headers["Location"].endswith(f"/api/media/image/{name}.jpg")
        assert fake.ops("blob.get") == []


class TestTimeBudget:
    def test_retries_stop_at_the_budget(self, make_app: Any, fake: Any) -> None:
        client = make_app(POSTER_REQUEST_BUDGET="8").test_client()  # room for the 1 s and 2 s retries, not the 4 s one
        fake.fail("ollama.chat", 503, times=4)
        error = post(client, "/api/ollama/generate", generate_body()).get_json()["error"]
        assert error["kind"] == "server" and len(error["retries"]) == 2
        assert fake.ops() == ["ollama.chat"] * 3

    def test_browser_timeouts_follow_the_budget(self, make_app: Any) -> None:
        config = make_app(VERCEL="1").test_client().get("/api/config").get_json()
        assert config["ollama"]["requestTimeout"] == 295
        assert config["buffer"]["requestTimeout"] == 30 * 4 + 7 + 10
        assert config["limits"]["maxUploadBytes"] == 4 * 1024 * 1024


class TestUncertainScheduling:
    def test_a_lost_answer_is_uncertain(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterCreatePost", exc=requests.ReadTimeout("lost"), after=True)
        data = post(client, "/api/buffer/schedule", schedule_body()).get_json()
        assert [p["channelId"] for p in data["posts"]] == ["ch2"]
        assert data["failures"][0]["channelId"] == "ch1" and data["failures"][0]["uncertain"] is True
        assert len(fake.buffer.posts) == 2  # Buffer did create it

    def test_a_refusal_is_certain(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterCreatePost", 400)
        failure = post(client, "/api/buffer/schedule", schedule_body()).get_json()["failures"][0]
        assert failure["uncertain"] is False

    def test_every_channel_lost_raises_an_uncertain_error(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterCreatePost", 502, times=2)
        error = post(client, "/api/buffer/schedule", schedule_body()).get_json()["error"]
        assert error["kind"] == "client" and error["uncertain"] is True

    def test_lost_connection_on_the_first_channel(self, client: Any, fake: Any) -> None:
        fake.fail("buffer.PosterCreatePost", exc=requests.ConnectionError("reset"), after=True)
        error = post(client, "/api/buffer/schedule", schedule_body()).get_json()["error"]
        assert error["kind"] == "network" and error["uncertain"] is True

    def test_validation_failures_are_never_uncertain(self, client: Any) -> None:
        error = post(client, "/api/buffer/schedule", schedule_body(text="")).get_json()["error"]
        assert error["uncertain"] is False


def test_data_url_with_whitespace(client: Any) -> None:
    body = generate_body(image="data:image/jpeg;base64,\n" + b64(image_bytes()))
    assert post(client, "/api/ollama/generate", body).status_code == 200


def test_due_at_in_another_timezone(client: Any, fake: Any) -> None:
    due = future(1).replace("Z", "+05:30")
    post(client, "/api/buffer/schedule", schedule_body(dueAt=due, channelIds=["ch1"]))
    created = fake.calls[0].json["variables"]["input"]["dueAt"]
    assert created.endswith("Z") and created != due


def test_blob_store_id_from_token(make_app: Any) -> None:
    config = make_app(**BLOB_ENV).test_client().get("/api/config").get_json()
    assert config["media"] == {**config["media"], "configured": True, "host": "Vercel Blob"}
