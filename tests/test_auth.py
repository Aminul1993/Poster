"""HTTP Basic authentication, and the refusal to run open on a public address."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from tests.conftest import BLOB_ENV, post

PUBLIC = "https://poster.example.com"


def basic(password: str, user: str = "me") -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


class TestWithoutPassword:
    def test_local_requests_are_served(self, client: Any) -> None:
        assert client.get("/").status_code == 200
        assert post(client, "/api/buffer/validate", {}).status_code == 200

    @pytest.mark.parametrize("path", ["/", "/api/config"])
    def test_public_address_is_refused(self, client: Any, path: str) -> None:
        response = client.get(path, base_url=PUBLIC)
        assert response.status_code == 503
        assert "POSTER_PASSWORD" in response.get_data(as_text=True)

    def test_refusal_is_json_for_the_api(self, client: Any) -> None:
        error = client.get("/api/config", base_url=PUBLIC).get_json()["error"]
        assert error["kind"] == "config"

    def test_dns_rebinding_host_is_refused(self, client: Any, fake: Any) -> None:
        response = post(client, "/api/buffer/schedule", {}, base_url="http://attacker.example:5000")
        assert response.status_code == 503
        assert not fake.calls

    def test_health_and_assets_stay_public(self, client: Any) -> None:
        assert client.get("/api/health", base_url=PUBLIC).status_code == 200
        assert client.get("/assets/any/js/main.js", base_url=PUBLIC).status_code == 200

    def test_buffer_can_fetch_post_images(self, make_app: Any) -> None:
        client = make_app(**BLOB_ENV).test_client()
        assert client.get("/api/media/image/missing-image.jpg", base_url=PUBLIC).status_code == 404


class TestWithPassword:
    @pytest.fixture
    def locked(self, make_app: Any) -> Any:
        return make_app(POSTER_PASSWORD="correct horse battery staple").test_client()

    @pytest.mark.parametrize("base_url", ["http://localhost", PUBLIC])
    def test_requires_credentials_everywhere(self, locked: Any, base_url: str) -> None:
        response = locked.get("/", base_url=base_url)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"].startswith('Basic realm="Poster"')

    def test_api_refusal_is_json(self, locked: Any, fake: Any) -> None:
        response = post(locked, "/api/buffer/validate", {}, headers=basic("wrong"))
        assert response.status_code == 401
        assert response.get_json()["error"]["kind"] == "auth"
        assert not fake.calls

    def test_correct_password_any_user(self, locked: Any) -> None:
        assert locked.get("/", base_url=PUBLIC, headers=basic("correct horse battery staple", "anyone")).status_code == 200
        response = post(locked, "/api/buffer/validate", {}, headers=basic("correct horse battery staple"))
        assert response.status_code == 200

    def test_other_schemes_are_refused(self, locked: Any) -> None:
        assert locked.get("/", headers={"Authorization": "Bearer correct horse battery staple"}).status_code == 401

    def test_public_paths_need_no_password(self, locked: Any) -> None:
        assert locked.get("/api/health").status_code == 200
        assert locked.get("/assets/any/css/poster.css").status_code == 200

    def test_csrf_check_still_applies_after_login(self, locked: Any) -> None:
        response = locked.post("/api/buffer/validate", json={}, headers=basic("correct horse battery staple"))
        assert response.status_code == 403
