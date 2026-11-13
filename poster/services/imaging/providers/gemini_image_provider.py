"""Gemini Image: Gemini's image models through generateContent, asked for an image reply. A photo sent along
with the description is edited."""

from __future__ import annotations

import base64
from typing import Any

from poster.config import API
from poster.errors import ServiceError, extract_error_detail, truncate
from poster.imaging import sniff_image
from poster.retry import RetryCallback
from poster.services import Field
from poster.services.ai.providers.gemini_provider import model_path
from poster.services.imaging.image_provider import ImageAIProvider, decode_b64, new_seed

ASPECT_RATIOS = {"square": "1:1", "portrait": "4:5", "landscape": "16:9", "story": "9:16"}


class GeminiImageProvider(ImageAIProvider):
    name = "gemini"
    label = "Gemini Image"
    service = "Gemini"
    docs_url = "https://aistudio.google.com/apikey"
    fields = (
        Field("api_key", "API key", "secret", required=True, env="GEMINI_API_KEY", placeholder="AIza…"),
        Field("model", "Model", env="GEMINI_IMAGE_MODEL", default="gemini-2.5-flash-image"),
        Field("endpoint", "Base URL", "url", env="GEMINI_BASE_URL", default=API["image_ai"]["gemini_url"]),
    )

    def base_url(self) -> str:
        return (self.values.get("endpoint") or API["image_ai"]["gemini_url"]).rstrip("/")

    @property
    def host(self) -> str:
        return self.base_url().split("//", 1)[-1].split("/", 1)[0]

    @property
    def model(self) -> str:
        return self.values.get("model") or "gemini-2.5-flash-image"

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json", "x-goog-api-key": self.api_key}

    def ask(self, parts: list[dict[str, Any]], aspect: str | None, on_retry: RetryCallback, *, edit: bool) -> bytes:
        config: dict[str, Any] = {"responseModalities": ["IMAGE"]}
        if aspect:
            config["imageConfig"] = {"aspectRatio": ASPECT_RATIOS[aspect]}
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": config}
        payload, text = self.call_json("POST", f"{self.base_url()}/{model_path(self.model)}:generateContent", on_retry,
                                       edit=edit, headers=self.headers(), json=body)
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        first = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
        content = first.get("content")
        for part in content.get("parts", []) if isinstance(content, dict) else []:
            inline = part.get("inlineData") or part.get("inline_data") if isinstance(part, dict) else None
            if isinstance(inline, dict) and inline.get("data"):
                return decode_b64(inline["data"])
        reason = extract_error_detail(payload) or str(first.get("finishReason") or "")
        raise ServiceError(f"Gemini did not return an image{f' ({reason})' if reason else ''}.", kind="parse",
                           service=self.service, details=truncate(text, 300))

    def generate_image(self, prompt: str, aspect: str, on_retry: RetryCallback) -> dict[str, Any]:
        data = self.ask([{"text": prompt}], aspect, on_retry, edit=False)
        return self.result(data, "", seed=new_seed(), model=self.model)

    def edit_image(self, image: bytes, prompt: str, on_retry: RetryCallback) -> dict[str, Any]:
        source = {"inline_data": {"mime_type": sniff_image(image) or "image/jpeg", "data": base64.b64encode(image).decode("ascii")}}
        data = self.ask([source, {"text": prompt}], None, on_retry, edit=True)
        return self.result(data, "", seed=new_seed(), model=self.model)

    def test_connection(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Reads the model's description (nothing is generated or billed)."""
        self.call_json("GET", f"{self.base_url()}/{model_path(self.model)}", on_retry, timeout=API["image_ai"]["test_timeout"],
                       headers=self.headers())
        return {"detail": f"The key works and {self.model} is available."}
