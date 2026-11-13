"""Anthropic: Claude models through the Messages API."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from poster.config import API
from poster.retry import RetryCallback
from poster.services import Field
from poster.services.ai.text_provider import SYSTEM_PROMPT, Message, TextAIProvider, image_type


class AnthropicProvider(TextAIProvider):
    name = "anthropic"
    label = "Anthropic"
    service = "Anthropic"
    docs_url = "https://console.anthropic.com/settings/keys"
    default_model_name = "claude-sonnet-5-5"
    fields = (
        Field("api_key", "API key", "secret", required=True, env="ANTHROPIC_API_KEY", placeholder="sk-ant-…"),
        Field("default_model", "Model", env="ANTHROPIC_MODEL", default="claude-sonnet-5-5"),
        Field("available_models", "Available models", "list", env="ANTHROPIC_MODELS",
              help="Comma-separated: the only models the dashboard may choose. Empty allows any."),
        Field("endpoint", "Base URL", "url", env="ANTHROPIC_BASE_URL", default=API["text_ai"]["anthropic_url"]),
    )

    def base_url(self) -> str:
        return (self.values.get("endpoint") or API["text_ai"]["anthropic_url"]).rstrip("/")

    @property
    def host(self) -> str:
        return urlparse(self.base_url()).netloc

    def headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json", "x-api-key": self.api_key,
                "anthropic-version": API["text_ai"]["anthropic_version"]}

    @staticmethod
    def wire_messages(messages: list[Message]) -> list[dict[str, Any]]:
        wire = []
        for message in messages:
            content: list[dict[str, Any]] = []
            if message.image:
                content.append({"type": "image", "source": {"type": "base64", "media_type": image_type(message.image),
                                                            "data": message.image}})
            content.append({"type": "text", "text": message.text})
            wire.append({"role": message.role, "content": content})
        return wire

    def complete(self, messages: list[Message], model: str, on_retry: RetryCallback) -> str:
        body = {"model": model, "max_tokens": API["text_ai"]["max_tokens"], "system": SYSTEM_PROMPT,
                "messages": self.wire_messages(messages)}
        data, text = self.post_json(f"{self.base_url()}/messages", body, self.headers(), on_retry)
        blocks = data.get("content")
        reply = "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text") \
            if isinstance(blocks, list) else ""
        if not reply.strip():
            raise self.empty_reply(data, text)
        return reply

    def fetch_models(self, on_retry: RetryCallback) -> list[str]:
        data = self.get_json(f"{self.base_url()}/models", self.headers(), on_retry, params={"limit": 100})
        items = data.get("data")
        models = [str(m.get("id") or "") for m in items if isinstance(m, dict)] if isinstance(items, list) else []
        return [m for m in models if m]
