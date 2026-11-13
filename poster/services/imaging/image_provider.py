"""ImageAIProvider: a written description in, an image out (generate_image); or a photo and a description of a
change in, the changed copy out (edit_image).

The base class owns what providers share: checking that a reply is an image Poster can use, the result's
shape, the public description and retry rules. An edit is billed per call and never cached, so it is retried
only when the provider refused it outright (HTTP 429/503), never after a timeout or another server error.
"""

from __future__ import annotations

import base64
import binascii
import io
import logging
import secrets
from abc import abstractmethod
from typing import Any, ClassVar

from PIL import Image

from poster.config import API, LIMITS, Config
from poster.errors import ServiceError, truncate
from poster.http import HttpClient
from poster.imaging import sniff_image
from poster.retry import RetryCallback, browser_timeout, retry_request, worst_case_seconds
from poster.services import IntegrationProvider, Values, clean_secret

log = logging.getLogger("poster")


def edit_retryable(error: ServiceError) -> bool:
    """Unlike a generation, an edit may have been billed after a timeout or 5xx: only refusals that guarantee
    no image was made are retried."""
    return error.kind == "rate_limit" or error.status in (429, 503)


def new_seed() -> int:
    """A new seed per call, so the same description gives a new image every time (retries reuse it)."""
    return secrets.randbelow(2**31)


def decode_b64(value: Any) -> bytes:
    """Base64 from a provider's reply (an optional data: prefix allowed), or b"" when it is not."""
    if not isinstance(value, str):
        return b""
    try:
        return base64.b64decode(value.partition(",")[2] if value.startswith("data:") else value, validate=True)
    except (binascii.Error, ValueError):
        return b""


def image_size(data: bytes) -> tuple[int, int]:
    """Width and height from the image's header, or (0, 0)."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            return int(width), int(height)
    except (OSError, ValueError, Image.DecompressionBombError):
        return 0, 0


class ImageAIProvider(IntegrationProvider):
    """An image generator. Subclasses implement generate_image(), edit_image() and test_connection()."""

    unconfigured = "Image generation is not configured on the server"
    # Whether edit_image() works; the dashboard offers "Edit with AI" only then.
    supports_edit: ClassVar[bool] = True

    def __init__(self, http: HttpClient, config: Config, values: Values | None = None) -> None:
        """``values`` are the provider's settings; without them, its defaults and the environment's."""
        self.http = http
        self.values: Values = values if values is not None else {**self.defaults(), **self.from_env(config)}
        self.api_key = clean_secret(self.values.get("api_key", ""))
        self.timeout = config.pollinations_timeout
        self.budget = config.request_budget
        self.problem = self.check_config()

    # -- configuration ------------------------------------------------------------------------------------------------

    def check_config(self) -> str | None:
        return self.check_values({**self.values, "api_key": self.api_key})

    @property
    def host(self) -> str:
        return ""

    @property
    def model(self) -> str:
        return self.values.get("model", "")

    @property
    def edit_model(self) -> str:
        return self.values.get("edit_model") or self.model

    def public_info(self) -> dict[str, Any]:
        return {
            "configured": self.configured, "problem": self.problem, "provider": self.name, "label": self.label,
            "host": self.host, "aspects": {name: list(size) for name, size in API["image_gen"]["aspects"].items()},
            "model": self.model, "editModel": self.edit_model, "canEdit": self.supports_edit,
            "maxPromptChars": LIMITS["max_prompt_chars"],
            "requestTimeout": browser_timeout(worst_case_seconds(self.timeout) + 10, self.budget),
        }

    # -- the provider's own API -----------------------------------------------------------------------------------------

    @abstractmethod
    def generate_image(self, prompt: str, aspect: str, on_retry: RetryCallback) -> dict[str, Any]:
        """One new image as ``{"image": Base64, "mimeType", "width", "height", "seed"}``."""

    def edit_image(self, image: bytes, prompt: str, on_retry: RetryCallback) -> dict[str, Any]:
        """A copy of ``image`` changed as ``prompt`` describes, as ``{"image": Base64, "mimeType", "model", "seed"}``."""
        raise ServiceError(f"{self.label} cannot edit images. Choose another image provider in Credentials & Integrations.",
                           kind="config", service=self.service)

    @abstractmethod
    def test_connection(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Checks the key with a call that makes no image (nothing is billed): ``{"detail": str}``."""

    # The names the routes used before providers could be switched.
    def generate(self, prompt: str, aspect: str, on_retry: RetryCallback) -> dict[str, Any]:
        self.require_configured()
        return self.generate_image(prompt, aspect, on_retry)

    def edit(self, image: bytes, prompt: str, on_retry: RetryCallback) -> dict[str, Any]:
        self.require_configured()
        return self.edit_image(image, prompt, on_retry)

    def test(self, on_retry: RetryCallback) -> dict[str, Any]:
        self.require_configured()
        return {**self.test_connection(on_retry), "provider": self.name, "label": self.label, "host": self.host}

    # -- helpers --------------------------------------------------------------------------------------------------------

    def check_image(self, data: bytes, details: str) -> str:
        """The type of an image the provider returned; ``details`` describes the reply when it is not one."""
        mimetype = sniff_image(data)
        if not mimetype:
            raise ServiceError(f"{self.label} did not return an image.", kind="parse", service=self.service,
                               details=truncate(details, 300))
        if len(data) > LIMITS["max_image_bytes"]:
            raise ServiceError("The generated image is larger than 20 MB.", kind="parse", service=self.service)
        return mimetype

    def result(self, data: bytes, details: str, *, seed: int, model: str = "", size: tuple[int, int] | None = None) -> dict[str, Any]:
        mimetype = self.check_image(data, details)
        width, height = size or image_size(data)
        result: dict[str, Any] = {"image": base64.b64encode(data).decode("ascii"), "mimeType": mimetype, "width": width,
                                  "height": height, "seed": seed}
        if model:
            result["model"] = model
        return result

    def call_json(self, method: str, url: str, on_retry: RetryCallback, *, edit: bool = False, timeout: int | None = None,
                  **kwargs: Any) -> tuple[Any, str]:
        """One JSON request with this provider's retry rule (stricter for edits). Returns (JSON, raw text)."""
        _, data, text = retry_request(
            lambda: self.http.request(method, url, service=self.service, timeout=timeout or self.timeout, **kwargs),
            should_retry=edit_retryable if edit else None, on_retry=on_retry,
        )
        return data, text

    def call_bytes(self, method: str, url: str, on_retry: RetryCallback, *, edit: bool = False, **kwargs: Any) -> bytes:
        return retry_request(
            lambda: self.http.request_bytes(method, url, service=self.service, timeout=self.timeout, **kwargs),
            should_retry=edit_retryable if edit else None, on_retry=on_retry,
        )
