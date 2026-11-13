"""The image AI provider registry, and ImageProviderFactory, which hands out the provider chosen in Credentials &
Integrations (or by the environment) with its current settings.

To add a provider: write an ImageAIProvider subclass in providers/ and add it to IMAGE_AI_PROVIDERS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from poster.errors import ServiceError
from poster.http import HttpClient
from poster.services import Values
from poster.services.imaging.image_provider import ImageAIProvider
from poster.services.imaging.providers.flux_provider import FluxProvider
from poster.services.imaging.providers.gemini_image_provider import GeminiImageProvider
from poster.services.imaging.providers.openai_images_provider import OpenAIImagesProvider
from poster.services.imaging.providers.pollinations_provider import PollinationsProvider
from poster.services.imaging.providers.stability_provider import StabilityProvider

if TYPE_CHECKING:
    from poster.services.settings_service import SettingsService

PROVIDER_TYPE = "image_ai"
DEFAULT_PROVIDER = "pollinations"

IMAGE_AI_PROVIDERS: dict[str, type[ImageAIProvider]] = {
    "pollinations": PollinationsProvider,
    "openai": OpenAIImagesProvider,
    "stability": StabilityProvider,
    "flux": FluxProvider,
    "gemini": GeminiImageProvider,
}


class ImageProviderFactory:
    """Builds image AI providers from the settings. The active one is rebuilt only when the settings change."""

    def __init__(self, settings: SettingsService, http: HttpClient) -> None:
        self.settings = settings
        self.http = http

    def get_active_provider(self) -> ImageAIProvider:
        name = self.settings.active_name(PROVIDER_TYPE)
        return self.settings.cached((PROVIDER_TYPE, name), lambda: self.build(name))

    def build(self, name: str, values: Values | None = None) -> ImageAIProvider:
        """Provider ``name`` with ``values`` (default: its current settings), for example to test unsaved settings."""
        cls = IMAGE_AI_PROVIDERS.get(name)
        if cls is None:
            raise ServiceError(f'Unknown image AI provider "{name}".', kind="validation", service="Settings")
        return cls(self.http, self.settings.config, self.settings.values(PROVIDER_TYPE, name) if values is None else values)
