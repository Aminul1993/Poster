"""OpenAI: /chat/completions with a vision model (or any OpenAI-compatible API at another base URL)."""

from __future__ import annotations

from poster.config import API
from poster.services import Field
from poster.services.ai.text_provider import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    label = "OpenAI"
    service = "OpenAI"
    docs_url = "https://platform.openai.com/api-keys"
    default_model_name = "gpt-4.1-mini"
    fields = (
        Field("api_key", "API key", "secret", required=True, env="OPENAI_API_KEY", placeholder="sk-…"),
        Field("organization_id", "Organization ID", env="OPENAI_ORG_ID", placeholder="org-…",
              help="Optional: bills the requests to this organization."),
        Field("default_model", "Model", env="OPENAI_MODEL", default="gpt-4.1-mini", help="A model that accepts images."),
        Field("available_models", "Available models", "list", env="OPENAI_MODELS",
              help="Comma-separated: the only models the dashboard may choose. Empty allows any."),
        Field("endpoint", "Base URL", "url", env="OPENAI_BASE_URL", default=API["text_ai"]["openai_url"],
              help="Change only for an OpenAI-compatible proxy."),
    )

    def base_url(self) -> str:
        return (self.values.get("endpoint") or API["text_ai"]["openai_url"]).rstrip("/")

    def chat_url(self, model: str) -> str:
        return f"{self.base_url()}/chat/completions"

    def models_url(self) -> str:
        return f"{self.base_url()}/models"

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {self.api_key}"}
        if self.values.get("organization_id"):
            headers["OpenAI-Organization"] = self.values["organization_id"]
        return headers
