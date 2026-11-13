"""The text AI provider registry, and TextProviderFactory, which hands out the provider chosen in Credentials &
Integrations (or by the environment) with its current settings.

To add a provider: write a TextAIProvider subclass in providers/ and add it to TEXT_AI_PROVIDERS. The dashboard
then offers it, renders its fields, stores them encrypted, and caption generation uses it once it is active.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from poster.errors import ServiceError
from poster.http import HttpClient
from poster.services import Values
from poster.services.ai.providers.anthropic_provider import AnthropicProvider
from poster.services.ai.providers.azure_openai_provider import AzureOpenAIProvider
from poster.services.ai.providers.gemini_provider import GeminiProvider
from poster.services.ai.providers.groq_provider import GroqProvider
from poster.services.ai.providers.openai_provider import OpenAIProvider
from poster.services.ai.text_provider import TextAIProvider

if TYPE_CHECKING:
    from poster.services.settings_service import SettingsService

PROVIDER_TYPE = "text_ai"
DEFAULT_PROVIDER = "groq"

TEXT_AI_PROVIDERS: dict[str, type[TextAIProvider]] = {
    "groq": GroqProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "azure": AzureOpenAIProvider,
}


class TextProviderFactory:
    """Builds text AI providers from the settings. The active one is rebuilt only when the settings change."""

    def __init__(self, settings: SettingsService, http: HttpClient) -> None:
        self.settings = settings
        self.http = http

    def get_active_provider(self) -> TextAIProvider:
        name = self.settings.active_name(PROVIDER_TYPE)
        return self.settings.cached((PROVIDER_TYPE, name), lambda: self.build(name))

    def build(self, name: str, values: Values | None = None) -> TextAIProvider:
        """Provider ``name`` with ``values`` (default: its current settings), for example to test unsaved settings."""
        cls = TEXT_AI_PROVIDERS.get(name)
        if cls is None:
            raise ServiceError(f'Unknown text AI provider "{name}".', kind="validation", service="Settings")
        return cls(self.http, self.settings.config, self.settings.values(PROVIDER_TYPE, name) if values is None else values)
