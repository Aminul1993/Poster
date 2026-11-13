"""Flux: Black Forest Labs' API. A request starts a task; Poster polls it until the image is ready, then downloads it.
FLUX.1 Kontext models edit images."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

from poster import retry
from poster.config import API
from poster.errors import ServiceError, truncate
from poster.retry import RetryCallback, remaining_budget
from poster.services import Field
from poster.services.imaging.image_provider import ImageAIProvider, new_seed

log = logging.getLogger("poster")

# FLUX.1 models take a width and height in multiples of 32; Kontext and Ultra take an aspect ratio.
SIZES = {"square": (1024, 1024), "portrait": (1024, 1280), "landscape": (1344, 768), "story": (768, 1344)}
ASPECT_RATIOS = {"square": "1:1", "portrait": "4:5", "landscape": "16:9", "story": "9:16"}
WORKING = frozenset({"Pending", "Queued", "Processing"})
# Hosts Black Forest Labs answers polls from: the global API and its regions (api.eu1.bfl.ai, ...).
BFL_API_HOST = re.compile(r"api(\.[a-z0-9-]+)?\.bfl\.ai")


class FluxProvider(ImageAIProvider):
    name = "flux"
    label = "Flux"
    service = "Flux"
    docs_url = "https://dashboard.bfl.ai"
    fields = (
        Field("api_key", "API key", "secret", required=True, env="BFL_API_KEY"),
        Field("model", "Model", env="BFL_MODEL", default="flux-pro-1.1", help="flux-pro-1.1, flux-pro-1.1-ultra, flux-kontext-pro …"),
        Field("edit_model", "Edit model", env="BFL_EDIT_MODEL", default="flux-kontext-pro", help="A FLUX.1 Kontext model."),
        Field("endpoint", "Base URL", "url", env="BFL_BASE_URL", default=API["image_ai"]["bfl_url"],
              help="The global endpoint, or a regional one such as https://api.eu.bfl.ai."),
    )

    def base_url(self) -> str:
        return (self.values.get("endpoint") or API["image_ai"]["bfl_url"]).rstrip("/")

    @property
    def host(self) -> str:
        return self.base_url().split("//", 1)[-1].split("/", 1)[0]

    @property
    def model(self) -> str:
        return self.values.get("model") or "flux-pro-1.1"

    @property
    def edit_model(self) -> str:
        return self.values.get("edit_model") or "flux-kontext-pro"

    def headers(self) -> dict[str, str]:
        return {"x-key": self.api_key, "Accept": "application/json", "Content-Type": "application/json"}

    @staticmethod
    def shape(model: str, aspect: str) -> dict[str, Any]:
        if "kontext" in model or "ultra" in model:
            return {"aspect_ratio": ASPECT_RATIOS[aspect]}
        width, height = SIZES[aspect]
        return {"width": width, "height": height}

    def run(self, model: str, body: dict[str, Any], on_retry: RetryCallback) -> bytes:
        """Starts a task, waits for it, and downloads the image. The start is billed, so it is retried only when
        refused outright; polling is free and retried as usual."""
        payload, text = self.call_json("POST", f"{self.base_url()}/v1/{model}", on_retry, edit=True, headers=self.headers(),
                                       json=body, allow_redirects=False)
        task = payload if isinstance(payload, dict) else {}
        polling_url = task.get("polling_url") or (f"{self.base_url()}/v1/get_result?id={task['id']}" if task.get("id") else "")
        if not isinstance(polling_url, str) or not polling_url.startswith("https://"):
            raise ServiceError("Flux did not start the task.", kind="parse", service=self.service, details=truncate(text, 300))
        poll_host = self.poll_host(polling_url)
        if poll_host is None:
            raise ServiceError("Flux asked to be polled at an unexpected address.", kind="parse", service=self.service)
        sample = self.wait(polling_url, on_retry)
        return self.call_bytes("GET", sample, on_retry)

    def poll_host(self, url: str) -> str | None:
        """The host of a polling URL when the key may go there: the configured API host, or one of Black Forest
        Labs' API hosts. The URL is parsed as the HTTP client (urllib3) parses it, and refused when it holds
        characters that parsers read differently."""
        if "\\" in url or "@" in url:
            return None
        try:
            parsed = parse_url(url)
        except LocationParseError:
            return None
        host = (parsed.host or "").lower()
        if parsed.scheme != "https" or parsed.auth or not host:
            return None
        return host if host == self.host.lower() or BFL_API_HOST.fullmatch(host) else None

    def wait(self, polling_url: str, on_retry: RetryCallback) -> str:
        """Polls the task until its image is ready and returns the image's (signed, short-lived) URL."""
        interval = float(API["image_ai"]["poll_seconds"])
        for _ in range(max(1, int(self.timeout / interval))):
            # Never redirected: requests would carry the x-key header along to another host.
            payload, text = self.call_json("GET", polling_url, on_retry, timeout=API["image_ai"]["test_timeout"],
                                           headers=self.headers(), allow_redirects=False)
            state = payload if isinstance(payload, dict) else {}
            status = str(state.get("status") or "")
            if status == "Ready":
                result = state.get("result")
                sample = result.get("sample") if isinstance(result, dict) else None
                if isinstance(sample, str) and sample.startswith("https://"):
                    return sample
                raise ServiceError("Flux finished without an image.", kind="parse", service=self.service,
                                   details=truncate(text, 300))
            if "Moderated" in status:
                raise ServiceError(f"Flux refused the request ({status}). Describe the image differently.", kind="client",
                                   service=self.service)
            if status not in WORKING:
                raise ServiceError(f"Flux could not make the image ({status or 'unknown status'}).", kind="server",
                                   service=self.service, details=truncate(text, 300))
            left = remaining_budget()
            if left is not None and left < interval + retry.MIN_ATTEMPT_SECONDS:
                break
            retry._sleep(interval)
        raise ServiceError("Flux did not finish the image in time.", kind="timeout", service=self.service,
                           hint="Flux may be busy. Try again in a moment.")

    def generate_image(self, prompt: str, aspect: str, on_retry: RetryCallback) -> dict[str, Any]:
        seed = new_seed()
        body = {"prompt": prompt, "seed": seed, "output_format": "png", **self.shape(self.model, aspect)}
        data = self.run(self.model, body, on_retry)
        return self.result(data, data[:300].decode("utf-8", "replace"), seed=seed, model=self.model)

    def edit_image(self, image: bytes, prompt: str, on_retry: RetryCallback) -> dict[str, Any]:
        seed = new_seed()
        body = {"prompt": prompt, "input_image": base64.b64encode(image).decode("ascii"), "seed": seed, "output_format": "png"}
        data = self.run(self.edit_model, body, on_retry)
        return self.result(data, data[:300].decode("utf-8", "replace"), seed=seed, model=self.edit_model)

    def test_connection(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Reads the key's credits (nothing is generated or billed)."""
        payload, _ = self.call_json("GET", f"{self.base_url()}/v1/credits", on_retry, timeout=API["image_ai"]["test_timeout"],
                                    headers=self.headers(), allow_redirects=False)
        credits = payload.get("credits") if isinstance(payload, dict) else None
        return {"detail": f"The key works{f' ({credits} credits left)' if isinstance(credits, (int, float)) else ''}."}
