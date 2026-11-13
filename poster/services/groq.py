"""Groq, Poster's original caption provider. It now lives with the other text AI providers in
poster/services/ai/; this module keeps the names older code imports."""

from __future__ import annotations

from poster.services.ai.providers.groq_provider import GroqProvider
from poster.services.ai.text_provider import REPAIR_PROMPT, SYSTEM_PROMPT, GenerationOptions, build_prompt

# Callers consume text AI through TextProviderFactory; GroqService remains for code written before it.
GroqService = GroqProvider

__all__ = ["REPAIR_PROMPT", "SYSTEM_PROMPT", "GenerationOptions", "GroqService", "build_prompt"]
