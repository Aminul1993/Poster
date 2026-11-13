"""The app's services as the routes reach them (app.extensions["poster"]), and run(), which runs a service call within
the request's time budget. Shared by poster/web.py and the blueprints in poster/routes/."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from flask import current_app, jsonify

from poster.activity import ActivityLog
from poster.config import Config
from poster.errors import ServiceError
from poster.http import HttpClient
from poster.retry import RetryLog, time_budget
from poster.services.ai import TextAIProvider, TextProviderFactory
from poster.services.imaging import ImageAIProvider, ImageProviderFactory
from poster.services.media import MediaService
from poster.services.meta import MetaService
from poster.services.settings_service import SettingsService
from poster.services.social import SocialService


@dataclass(frozen=True)
class Services:
    """The app's configuration and service clients, kept in app.extensions["poster"].

    The integrations' clients are not fixed at start-up: ``groq`` (the active text AI provider), ``image_gen``
    (the active image AI provider), ``media`` and ``meta`` are built from the current settings (see
    SettingsService) and rebuilt when Credentials & Integrations changes them, without a restart.
    """

    config: Config
    http: HttpClient
    settings: SettingsService
    text_ai: TextProviderFactory
    image_ai: ImageProviderFactory
    social: SocialService
    activity: ActivityLog
    # Content hash of static/, so asset URLs change whenever a file does and can be cached forever.
    asset_version: str

    @property
    def groq(self) -> TextAIProvider:
        """The active text AI provider (named after Groq, the only one before providers could be switched)."""
        return self.text_ai.get_active_provider()

    @property
    def image_gen(self) -> ImageAIProvider:
        """The active image AI provider."""
        return self.image_ai.get_active_provider()

    @property
    def media(self) -> MediaService:
        return self.settings.cached(("media",), lambda: MediaService(self.http, self.settings.effective_config()))

    def meta(self) -> MetaService:
        return self.settings.cached(("meta",), lambda: MetaService(self.http, self.settings.effective_config()))

    def problems(self) -> dict[str, str | None]:
        """Each integration's active provider's configuration problem (None when it is ready). For storage and Meta,
        a problem with the provider's own fields comes first: it names the field to fix in the dashboard."""
        def own(provider_type: str) -> str | None:
            name = self.settings.active_name(provider_type)
            return self.settings.provider(provider_type, name).check_values(self.settings.values(provider_type, name))

        return {"text_ai": self.groq.problem, "image_ai": self.image_gen.problem,
                "storage": own("storage") or self.media.problem, "meta": own("meta") or self.meta().problem}


def services() -> Services:
    return cast(Services, current_app.extensions["poster"])


def run(label: str, action: Callable[[RetryLog], dict[str, Any]]) -> Any:
    """Runs a service action within the request's time budget and attaches its retry log to the response or error."""
    retries = RetryLog(label)
    try:
        with time_budget(services().config.request_budget):
            result = action(retries)
    except ServiceError as error:
        error.retries = list(retries)
        raise
    return jsonify({**result, "retries": list(retries)})
