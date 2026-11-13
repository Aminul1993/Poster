"""OpenAI Images: /images/generations and /images/edits (GPT Image models, or DALL·E)."""

from __future__ import annotations

from typing import Any

from poster.config import API
from poster.errors import ServiceError
from poster.imaging import IMAGE_EXTENSIONS, sniff_image
from poster.retry import RetryCallback
from poster.services import Field
from poster.services.imaging.image_provider import ImageAIProvider, decode_b64, new_seed

# The sizes each model family renders, per shape.
GPT_IMAGE_SIZES = {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024", "story": "1024x1536"}
DALLE_SIZES = {"square": "1024x1024", "portrait": "1024x1792", "landscape": "1792x1024", "story": "1024x1792"}


class OpenAIImagesProvider(ImageAIProvider):
    name = "openai"
    label = "OpenAI Images"
    service = "OpenAI"
    docs_url = "https://platform.openai.com/api-keys"
    fields = (
        Field("api_key", "API key", "secret", required=True, env="OPENAI_API_KEY", placeholder="sk-…"),
        Field("organization_id", "Organization ID", env="OPENAI_ORG_ID", placeholder="org-…"),
        Field("model", "Model", env="OPENAI_IMAGE_MODEL", default="gpt-image-1", help="gpt-image-1, or dall-e-3."),
        Field("endpoint", "Base URL", "url", env="OPENAI_BASE_URL", default=API["image_ai"]["openai_url"]),
    )

    def base_url(self) -> str:
        return (self.values.get("endpoint") or API["image_ai"]["openai_url"]).rstrip("/")

    @property
    def host(self) -> str:
        return self.base_url().split("//", 1)[-1].split("/", 1)[0]

    @property
    def model(self) -> str:
        return self.values.get("model") or "gpt-image-1"

    @property
    def dalle(self) -> bool:
        return self.model.startswith("dall-e")

    def headers(self, json: bool = True) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if json:
            headers["Content-Type"] = "application/json"
        if self.values.get("organization_id"):
            headers["OpenAI-Organization"] = self.values["organization_id"]
        return headers

    def image_from(self, payload: Any, text: str, on_retry: RetryCallback) -> bytes:
        """The first image of a reply: Base64 (GPT Image models), or a URL to download (DALL·E without b64_json)."""
        items = payload.get("data") if isinstance(payload, dict) else None
        first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        if first.get("b64_json"):
            return decode_b64(first["b64_json"])
        url = first.get("url")
        if isinstance(url, str) and url.startswith("https://"):
            return self.call_bytes("GET", url, on_retry)
        return b""

    def generate_image(self, prompt: str, aspect: str, on_retry: RetryCallback) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "prompt": prompt, "n": 1,
                                "size": (DALLE_SIZES if self.dalle else GPT_IMAGE_SIZES)[aspect]}
        if self.dalle:
            body["response_format"] = "b64_json"  # GPT Image models always answer in Base64 and refuse the option
        payload, text = self.call_json("POST", f"{self.base_url()}/images/generations", on_retry, headers=self.headers(),
                                       json=body)
        return self.result(self.image_from(payload, text, on_retry), text[:300], seed=new_seed(), model=self.model)

    def edit_image(self, image: bytes, prompt: str, on_retry: RetryCallback) -> dict[str, Any]:
        if self.dalle:
            raise ServiceError(f"{self.model} cannot edit images; choose gpt-image-1.", kind="config", service=self.service)
        mimetype = sniff_image(image) or "image/jpeg"
        files = {"image": (f"source{IMAGE_EXTENSIONS.get(mimetype, '.jpg')}", image, mimetype)}
        payload, text = self.call_json("POST", f"{self.base_url()}/images/edits", on_retry, edit=True,
                                       headers=self.headers(json=False), files=files,
                                       data={"model": self.model, "prompt": prompt, "n": "1"})
        return self.result(self.image_from(payload, text, on_retry), text[:300], seed=new_seed(), model=self.model)

    def test_connection(self, on_retry: RetryCallback) -> dict[str, Any]:
        payload, _ = self.call_json("GET", f"{self.base_url()}/models", on_retry, timeout=API["image_ai"]["test_timeout"],
                                    headers=self.headers())
        items = payload.get("data") if isinstance(payload, dict) else None
        models = {str(m.get("id")) for m in items if isinstance(m, dict)} if isinstance(items, list) else set()
        if models and self.model not in models:
            raise ServiceError(f'The key works, but this account cannot use "{self.model}".', kind="validation",
                               service=self.service)
        return {"detail": f"The key works and the account can use {self.model}."}
