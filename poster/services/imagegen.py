"""Pollinations, Poster's original image provider. It now lives with the other image AI providers in
poster/services/imaging/; this module keeps the names older code imports."""

from __future__ import annotations

from poster.services.imaging.image_provider import edit_retryable
from poster.services.imaging.providers.pollinations_provider import PollinationsProvider

# Callers consume image AI through ImageProviderFactory; ImageGenService remains for code written before it.
ImageGenService = PollinationsProvider
_edit_retryable = edit_retryable

__all__ = ["ImageGenService"]
