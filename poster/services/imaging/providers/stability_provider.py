"""Stability AI: Stable Image Core / Ultra and Stable Diffusion 3.5 (text-to-image, and image-to-image for edits)."""

from __future__ import annotations

from typing import Any

from poster.config import API
from poster.errors import ServiceError
from poster.imaging import IMAGE_EXTENSIONS, sniff_image
from poster.retry import RetryCallback
from poster.services import Field
from poster.services.imaging.image_provider import ImageAIProvider, new_seed

ASPECT_RATIOS = {"square": "1:1", "portrait": "4:5", "landscape": "16:9", "story": "9:16"}
# Models with an endpoint of their own; any other name is a Stable Diffusion 3.5 model (/generate/sd3).
OWN_ENDPOINT = ("core", "ultra")


class StabilityProvider(ImageAIProvider):
    name = "stability"
    label = "Stability AI"
    service = "Stability AI"
    docs_url = "https://platform.stability.ai/account/keys"
    fields = (
        Field("api_key", "API key", "secret", required=True, env="STABILITY_API_KEY", placeholder="sk-…"),
        Field("model", "Model", env="STABILITY_MODEL", default="core", help="core, ultra, sd3.5-large, sd3.5-medium …"),
        Field("edit_model", "Edit model", env="STABILITY_EDIT_MODEL", default="sd3.5-large",
              help="A Stable Diffusion 3.5 model: edits are image-to-image."),
        Field("endpoint", "Base URL", "url", env="STABILITY_BASE_URL", default=API["image_ai"]["stability_url"]),
    )

    def base_url(self) -> str:
        return (self.values.get("endpoint") or API["image_ai"]["stability_url"]).rstrip("/")

    @property
    def host(self) -> str:
        return self.base_url().split("//", 1)[-1].split("/", 1)[0]

    @property
    def model(self) -> str:
        return self.values.get("model") or "core"

    @property
    def edit_model(self) -> str:
        return self.values.get("edit_model") or "sd3.5-large"

    def headers(self) -> dict[str, str]:
        # image/* asks for the image itself rather than Base64 in JSON.
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "image/*"}

    def generate_url(self, model: str) -> str:
        path = model if model in OWN_ENDPOINT else "sd3"
        return f"{self.base_url()}/v2beta/stable-image/generate/{path}"

    def generate_image(self, prompt: str, aspect: str, on_retry: RetryCallback) -> dict[str, Any]:
        seed = new_seed()
        form: dict[str, Any] = {"prompt": prompt, "aspect_ratio": ASPECT_RATIOS[aspect], "output_format": "png", "seed": str(seed)}
        if self.model not in OWN_ENDPOINT:
            form["model"] = self.model
        # The API takes multipart/form-data only; the empty file part makes requests send it as such.
        data = self.call_bytes("POST", self.generate_url(self.model), on_retry, headers=self.headers(), data=form,
                               files={"none": ("", b"")})
        return self.result(data, data[:300].decode("utf-8", "replace"), seed=seed, model=self.model)

    def edit_image(self, image: bytes, prompt: str, on_retry: RetryCallback) -> dict[str, Any]:
        if self.edit_model in OWN_ENDPOINT:
            raise ServiceError(f'Stability edits use a Stable Diffusion 3.5 model, not "{self.edit_model}".', kind="config",
                               service=self.service)
        seed = new_seed()
        mimetype = sniff_image(image) or "image/jpeg"
        form = {"prompt": prompt, "mode": "image-to-image", "strength": str(API["image_ai"]["edit_strength"]),
                "model": self.edit_model, "output_format": "png", "seed": str(seed)}
        data = self.call_bytes("POST", self.generate_url(self.edit_model), on_retry, edit=True, headers=self.headers(),
                               data=form, files={"image": (f"source{IMAGE_EXTENSIONS.get(mimetype, '.jpg')}", image, mimetype)})
        return self.result(data, data[:300].decode("utf-8", "replace"), seed=seed, model=self.edit_model)

    def test_connection(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Reads the key's account (nothing is generated or billed)."""
        self.call_json("GET", f"{self.base_url()}/v1/user/account", on_retry, timeout=API["image_ai"]["test_timeout"],
                       headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"})
        return {"detail": "The key works."}
