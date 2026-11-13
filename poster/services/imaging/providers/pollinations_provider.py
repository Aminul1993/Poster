"""Pollinations: Poster's original image provider, still configured by POLLINATIONS_API_KEY and
POLLINATIONS_EDIT_MODEL when nothing is saved."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from poster.config import API, Config
from poster.errors import ServiceError, truncate
from poster.imaging import sniff_image
from poster.retry import RetryCallback
from poster.services import Field, Values
from poster.services.imaging.image_provider import ImageAIProvider, decode_b64, new_seed

log = logging.getLogger("poster")

MODELS_URL = "https://gen.pollinations.ai/image/models"


class PollinationsProvider(ImageAIProvider):
    """Text-to-image (the prompt in the URL) and OpenAI-compatible image editing (/v1/images/edits)."""

    name = "pollinations"
    label = "Pollinations"
    service = "Pollinations"
    docs_url = "https://enter.pollinations.ai"
    setup_hint = "Get a free key at enter.pollinations.ai, then save it in Settings → Credentials & Integrations."
    fields = (
        Field("api_key", "API key", "secret", required=True, env="POLLINATIONS_API_KEY", help="Free at enter.pollinations.ai."),
        Field("model", "Model", env="POLLINATIONS_MODEL", placeholder="flux",
              help="Optional: the generation model. Empty uses Pollinations' default."),
        Field("edit_model", "Edit model", env="POLLINATIONS_EDIT_MODEL", default="kontext",
              help="Any model whose supported endpoints in gen.pollinations.ai/image/models include /v1/images/edits."),
    )

    @classmethod
    def from_env(cls, config: Config) -> Values:
        values = {"api_key": config.pollinations_api_key, "edit_model": config.pollinations_edit_model}
        return {key: value for key, value in values.items() if value}

    def check_config(self) -> str | None:
        if not self.api_key:
            return "POLLINATIONS_API_KEY is not set."
        if re.search(r"\s", self.api_key):
            return "POLLINATIONS_API_KEY contains whitespace."
        return super().check_config()

    @property
    def host(self) -> str:
        return str(urlparse(API["image_gen"]["url"]).netloc)

    @property
    def edit_model(self) -> str:
        return self.values.get("edit_model") or "kontext"

    def headers(self) -> dict[str, str]:
        # The API key goes in a Bearer header; without it Pollinations answers 401.
        return {"Authorization": f"Bearer {self.api_key}"}

    def build_url(self, prompt: str, width: int, height: int, seed: int) -> str:
        """Pollinations takes the prompt in the URL path and the options in the query string."""
        query: dict[str, Any] = {"width": width, "height": height, "seed": seed, "nologo": "true"}
        if self.model:
            query["model"] = self.model
        # Encode every special character, "/" included, so the prompt stays one path segment.
        return f"{API['image_gen']['url']}/{quote(prompt, safe='')}?{urlencode(query)}"

    def generate_image(self, prompt: str, aspect: str, on_retry: RetryCallback) -> dict[str, Any]:
        width, height = API["image_gen"]["aspects"][aspect]
        # Pollinations caches by URL and the browser rejects identical files, so every call gets a new seed.
        # Retries reuse the URL, so they ask for the same image.
        seed = new_seed()
        data = self.call_bytes("GET", self.build_url(prompt, width, height, seed), on_retry, headers=self.headers())
        result = self.result(data, data[:300].decode("utf-8", "replace"), seed=seed, size=(width, height))
        log.info("Generated a %dx%d image (%d bytes)", width, height, len(data))
        return result

    def edit_image(self, image: bytes, prompt: str, on_retry: RetryCallback) -> dict[str, Any]:
        source = f"data:{sniff_image(image) or 'image/jpeg'};base64,{base64.b64encode(image).decode('ascii')}"
        seed = new_seed()
        body = {"prompt": prompt, "image": [{"image_url": source}], "model": self.edit_model, "seed": seed,
                "response_format": "b64_json"}
        payload, text = self.call_json("POST", API["image_gen"]["edit_url"], on_retry, edit=True, headers=self.headers(),
                                       json=body)
        items = payload.get("data") if isinstance(payload, dict) else None
        data = decode_b64(items[0].get("b64_json")) if isinstance(items, list) and items and isinstance(items[0], dict) else b""
        result = self.result(data, text[:300], seed=seed, model=self.edit_model)
        log.info("Edited an image with %s (%d bytes in, %d bytes out)", self.edit_model, len(image), len(data))
        return result

    def test_connection(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Reads the model list (nothing is generated or billed) and checks that the edit model can edit."""
        payload, text = self.call_json("GET", MODELS_URL, on_retry, timeout=API["image_ai"]["test_timeout"],
                                       headers=self.headers())
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ServiceError("Pollinations returned an unexpected model list.", kind="parse", service=self.service,
                               details=truncate(text, 300))
        editors = {str(m.get("name")) for m in items
                   if isinstance(m, dict) and "/v1/images/edits" in (m.get("supported_endpoints") or [])}
        if editors and self.edit_model not in editors:
            raise ServiceError(f'Pollinations is reachable, but "{self.edit_model}" cannot edit images. Choose one of: '
                               f"{', '.join(sorted(editors))}.", kind="validation", service=self.service)
        return {"detail": f"Pollinations is reachable and lists {len(items)} image models. It checks the key on the first "
                          "image."}
