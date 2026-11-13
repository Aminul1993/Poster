"""Groq: OpenAI-compatible /chat/completions with vision models. Poster's original text provider, still configured
by GROQ_API_KEY, GROQ_ENDPOINT, MODEL (or GROQ_MODEL) and GROQ_MODELS when nothing is saved."""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlparse

from poster.config import API, Config, check_url
from poster.services import Field, Values
from poster.services.ai.text_provider import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    label = "Groq"
    service = "Groq"
    docs_url = "https://console.groq.com/keys"
    default_model_name = "qwen/qwen3.8-27b"
    temperature: ClassVar[float | None] = 0.7
    fields = (
        Field("api_key", "API key", "secret", required=True, env="GROQ_API_KEY", placeholder="gsk_…",
              help="console.groq.com → API Keys."),
        Field("default_model", "Default model", env="MODEL", default="qwen/qwen3.8-27b",
              help="A model that accepts images (console.groq.com/docs/vision)."),
        Field("available_models", "Available models", "list", env="GROQ_MODELS",
              help="Comma-separated: the only models the dashboard may choose. Empty allows any."),
        Field("endpoint", "Endpoint", "url", env="GROQ_ENDPOINT", default="https://api.groq.com/openai/v1",
              help="OpenAI-compatible base URL, or the full …/chat/completions URL."),
    )

    @classmethod
    def from_env(cls, config: Config) -> Values:
        values = {"api_key": config.groq_api_key, "endpoint": config.groq_endpoint, "default_model": config.model,
                  "available_models": ",".join(config.groq_models)}
        return {key: value for key, value in values.items() if value}

    def check_config(self) -> str | None:
        endpoint = self.values.get("endpoint") or self.defaults()["endpoint"]
        problem = check_url(endpoint, "GROQ_ENDPOINT", https_only=True)
        if problem:
            return problem
        if not self.api_key:
            return "GROQ_API_KEY is not set (console.groq.com -> API Keys)."
        return super().check_config()

    def base_url(self) -> str:
        """The API base URL (e.g. https://api.groq.com/openai/v1), also when a full chat URL is configured."""
        url = urlparse(self.values.get("endpoint") or self.defaults()["endpoint"])
        path = url.path.rstrip("/").removesuffix(API["groq"]["chat_path"])
        return f"{url.scheme}://{url.netloc}{path}"

    def chat_url(self, model: str) -> str:
        return f"{self.base_url()}{API['groq']['chat_path']}"

    def models_url(self) -> str:
        return f"{self.base_url()}{API['groq']['models_path']}"

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
