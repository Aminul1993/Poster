"""Pollinations: a written description in, an image out, as an alternative to uploading one;
or an uploaded image and a description of a change in, the updated image out."""

from __future__ import annotations

import base64
import binascii
import logging
import re
import secrets
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from poster.config import API, LIMITS, Config, is_placeholder
from poster.errors import ServiceError, truncate
from poster.http import HttpClient
from poster.imaging import sniff_image
from poster.retry import RetryCallback, browser_timeout, retry_request, worst_case_seconds
from poster.services import ConfiguredService

log = logging.getLogger("poster")


def _edit_retryable(error: ServiceError) -> bool:
    """Unlike a generation URL, an edit is not cached: a timeout or 5xx may still have been
    billed. Only refusals that guarantee no image was made are retried."""
    return error.kind == "rate_limit" or error.status in (429, 503)


class ImageGenService(ConfiguredService):
    """Text-to-image and image editing through Pollinations, as an alternative to uploading a photo."""

    service = "Pollinations"
    unconfigured = "Image generation is not configured on the server"
    setup_hint = "Get a free key at enter.pollinations.ai, set it in the environment or .env file and restart Poster."

    def __init__(self, http: HttpClient, config: Config) -> None:
        self.http = http
        self.api_key = "" if is_placeholder(config.pollinations_api_key) else config.pollinations_api_key
        self.edit_model = config.pollinations_edit_model
        self.timeout = config.pollinations_timeout
        self.budget = config.request_budget
        self.problem = self._check_config()

    def _check_config(self) -> str | None:
        if not self.api_key:
            return "POLLINATIONS_API_KEY is not set."
        if re.search(r"\s", self.api_key):
            return "POLLINATIONS_API_KEY contains whitespace."
        return None

    def public_info(self) -> dict[str, Any]:
        return {
            "configured": self.configured, "problem": self.problem, "host": urlparse(API["image_gen"]["url"]).netloc,
            "aspects": {name: list(size) for name, size in API["image_gen"]["aspects"].items()},
            "editModel": self.edit_model,
            "maxPromptChars": LIMITS["max_prompt_chars"],
            "requestTimeout": browser_timeout(worst_case_seconds(self.timeout) + 10, self.budget),
        }

    def headers(self) -> dict[str, str]:
        # The API key goes in a Bearer header; without it Pollinations answers 401.
        return {"Authorization": f"Bearer {self.api_key}"}

    @staticmethod
    def build_url(prompt: str, width: int, height: int, seed: int) -> str:
        """Pollinations takes the prompt in the URL path and the options in the query string."""
        params = urlencode({"width": width, "height": height, "seed": seed, "nologo": "true"})
        # Encode every special character, "/" included, so the prompt stays one path segment.
        return f"{API['image_gen']['url']}/{quote(prompt, safe='')}?{params}"

    def generate(self, prompt: str, aspect: str, on_retry: RetryCallback) -> dict[str, Any]:
        """Generates one image and returns it as Base64."""
        self.require_configured()
        width, height = API["image_gen"]["aspects"][aspect]
        # Pollinations caches by URL and the browser rejects identical files, so every call
        # gets a new random seed. Retries reuse the URL, so they ask for the same image.
        seed = secrets.randbelow(2**31)
        url = self.build_url(prompt, width, height, seed)
        data = retry_request(
            lambda: self.http.request_bytes("GET", url, service=self.service, timeout=self.timeout, headers=self.headers()),
            on_retry=on_retry,
        )
        mimetype = self._check_image(data, data[:300].decode("utf-8", "replace"))
        log.info("Generated a %dx%d image (%d bytes)", width, height, len(data))
        return {"image": base64.b64encode(data).decode("ascii"), "mimeType": mimetype,
                "width": width, "height": height, "seed": seed}

    def edit(self, image: bytes, prompt: str, on_retry: RetryCallback) -> dict[str, Any]:
        """Changes ``image`` (JPEG, PNG or WEBP) as ``prompt`` describes and returns the new image as Base64."""
        self.require_configured()
        source = f"data:{sniff_image(image) or 'image/jpeg'};base64,{base64.b64encode(image).decode('ascii')}"
        # A new seed per call, as for generate(), so trying the same change again gives a new image.
        seed = secrets.randbelow(2**31)
        body = {"prompt": prompt, "image": [{"image_url": source}], "model": self.edit_model, "seed": seed,
                "response_format": "b64_json"}
        _, payload, text = retry_request(
            lambda: self.http.request("POST", API["image_gen"]["edit_url"], service=self.service, timeout=self.timeout,
                                      headers=self.headers(), json=body),
            should_retry=_edit_retryable, on_retry=on_retry,
        )
        items = payload.get("data") if isinstance(payload, dict) else None
        encoded = items[0].get("b64_json") if isinstance(items, list) and items and isinstance(items[0], dict) else None
        try:
            data = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else b""
        except (binascii.Error, ValueError):
            data = b""
        mimetype = self._check_image(data, text[:300])
        log.info("Edited an image with %s (%d bytes in, %d bytes out)", self.edit_model, len(image), len(data))
        return {"image": base64.b64encode(data).decode("ascii"), "mimeType": mimetype, "model": self.edit_model,
                "seed": seed}

    def _check_image(self, data: bytes, details: str) -> str:
        """The type of an image Pollinations returned; ``details`` describes the reply when it is not one."""
        mimetype = sniff_image(data)
        if not mimetype:
            raise ServiceError("Pollinations did not return an image.", kind="parse", service=self.service,
                               details=truncate(details, 300))
        if len(data) > LIMITS["max_image_bytes"]:
            raise ServiceError("The generated image is larger than 20 MB.", kind="parse", service=self.service)
        return mimetype
