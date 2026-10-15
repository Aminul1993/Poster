"""Pollinations: a written description in, an image out, as an alternative to uploading one."""

from __future__ import annotations

import base64
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


class ImageGenService(ConfiguredService):
    """Text-to-image through Pollinations, as an alternative to uploading a photo."""

    service = "Pollinations"
    unconfigured = "Image generation is not configured on the server"
    setup_hint = "Get a free key at enter.pollinations.ai, set it in the environment or .env file and restart Poster."

    def __init__(self, http: HttpClient, config: Config) -> None:
        self.http = http
        self.api_key = "" if is_placeholder(config.pollinations_api_key) else config.pollinations_api_key
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
        mimetype = sniff_image(data)
        if not mimetype:
            raise ServiceError("Pollinations did not return an image.", kind="parse", service=self.service,
                               details=truncate(data[:300].decode("utf-8", "replace"), 300))
        if len(data) > LIMITS["max_image_bytes"]:
            raise ServiceError("The generated image is larger than 20 MB.", kind="parse", service=self.service)
        log.info("Generated a %dx%d image (%d bytes)", width, height, len(data))
        return {"image": base64.b64encode(data).decode("ascii"), "mimeType": mimetype,
                "width": width, "height": height, "seed": seed}
