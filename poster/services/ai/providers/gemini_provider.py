"""Google Gemini: generateContent in the Gemini API, asking for a JSON reply."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlparse

from poster.config import API
from poster.retry import RetryCallback
from poster.services import Field
from poster.services.ai.text_provider import SYSTEM_PROMPT, Message, TextAIProvider, image_type


def model_path(model: str) -> str:
    """``models/<model>``, whether or not the name already starts with it."""
    return f"models/{quote(model.removeprefix('models/'), safe='-._')}"


class GeminiProvider(TextAIProvider):
    name = "gemini"
    label = "Gemini"
    service = "Gemini"
    docs_url = "https://aistudio.google.com/apikey"
    default_model_name = "gemini-2.5-flash"
    fields = (
        Field("api_key", "API key", "secret", required=True, env="GEMINI_API_KEY", placeholder="AIza…"),
        Field("default_model", "Model", env="GEMINI_MODEL", default="gemini-2.5-flash"),
        Field("available_models", "Available models", "list", env="GEMINI_MODELS",
              help="Comma-separated: the only models the dashboard may choose. Empty allows any."),
        Field("endpoint", "Base URL", "url", env="GEMINI_BASE_URL", default=API["text_ai"]["gemini_url"]),
    )

    def base_url(self) -> str:
        return (self.values.get("endpoint") or API["text_ai"]["gemini_url"]).rstrip("/")

    @property
    def host(self) -> str:
        return urlparse(self.base_url()).netloc

    def headers(self) -> dict[str, str]:
        # The key travels in a header, never in a URL that could end up in a log.
        return {"Content-Type": "application/json", "Accept": "application/json", "x-goog-api-key": self.api_key}

    @staticmethod
    def wire_contents(messages: list[Message]) -> list[dict[str, Any]]:
        contents = []
        for message in messages:
            parts: list[dict[str, Any]] = [{"text": message.text}]
            if message.image:
                parts.append({"inline_data": {"mime_type": image_type(message.image), "data": message.image}})
            contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})
        return contents

    def complete(self, messages: list[Message], model: str, on_retry: RetryCallback) -> str:
        body = {"system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]}, "contents": self.wire_contents(messages),
                "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": API["text_ai"]["max_tokens"]}}
        data, text = self.post_json(f"{self.base_url()}/{model_path(model)}:generateContent", body, self.headers(), on_retry)
        candidates = data.get("candidates")
        first = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
        content = first.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        reply = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)) if isinstance(parts, list) else ""
        if not reply.strip():
            feedback = data.get("promptFeedback")
            blocked = feedback.get("blockReason") if isinstance(feedback, dict) else None
            raise self.empty_reply({"message": f"blocked: {blocked}"} if blocked else data, text)
        return reply

    def fetch_models(self, on_retry: RetryCallback) -> list[str]:
        data = self.get_json(f"{self.base_url()}/models", self.headers(), on_retry, params={"pageSize": 200})
        items = data.get("models")
        models = []
        for item in items if isinstance(items, list) else []:
            methods = item.get("supportedGenerationMethods") if isinstance(item, dict) else None
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            name = str(item.get("name") or "").removeprefix("models/") if isinstance(item, dict) else ""
            if name:
                models.append(name)
        return models
