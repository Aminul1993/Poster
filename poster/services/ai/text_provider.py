"""TextAIProvider: one image in; a caption, an alternative caption, hashtags, a category and an engagement score out.

The base class owns everything the providers share: the prompt, the JSON reply's normalisation, the one
automatic repair turn, the connection test and the public description. A provider implements two calls in
its API's own shape: complete() (a conversation in, the reply text out) and fetch_models().
"""

from __future__ import annotations

import base64
import binascii
import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urlparse

from poster.config import API, LANGUAGES, TONES, Config
from poster.content import extract_json_object, normalize_content
from poster.errors import ServiceError, extract_error_detail, truncate
from poster.http import HttpClient
from poster.imaging import sniff_image
from poster.retry import RetryCallback, browser_timeout, retry_request, worst_case_seconds
from poster.services import IntegrationProvider, Values, clean_secret, split_list

log = logging.getLogger("poster")

SYSTEM_PROMPT = (
    "You are an expert social media strategist and copywriter. You write engaging, authentic captions and choose "
    "hashtags people actually search for. You always answer with one valid JSON object and nothing else: no markdown, "
    "no commentary."
)
REPAIR_PROMPT = (
    'Your previous reply could not be parsed. Reply again with ONLY one JSON object with the keys "description", '
    '"alternative_description", "hashtags", "category" and "engagement_score". No markdown, no code fences, no commentary.'
)


@dataclass(frozen=True)
class GenerationOptions:
    """Per-request settings chosen in the browser."""

    model: str
    tone: str
    extra: str
    desc_min: int
    desc_max: int
    language: str = "en"


@dataclass(frozen=True)
class Message:
    """One turn of a conversation, before a provider puts it in its API's shape. ``image`` is Base64 (JPEG,
    PNG or WEBP)."""

    role: str  # user | assistant
    text: str
    image: str | None = None


def build_prompt(opts: GenerationOptions) -> str:
    low = opts.desc_min
    high = max(low + 20, min(opts.desc_max, 300))
    lines = [
        "Analyze the uploaded image and generate:",
        "1. Engaging social media caption",
        "2. Alternative caption",
        "3. 10-15 hashtags",
        "4. Content category",
        "5. Estimated engagement score",
        "",
        "Return ONLY JSON in exactly this shape:",
        '{"description": "...", "alternative_description": "...", "hashtags": ["#tag1"], "category": "...", "engagement_score": 87}',
        "",
        "Rules:",
        f'- "description": an engaging caption of {low}-{high} characters, without hashtags.',
        f'- "alternative_description": a different take on the caption, {low}-{high} characters, without hashtags.',
        '- "hashtags": 10 to 15 unique, relevant hashtags, each starting with # and containing no spaces.',
        '- "category": a short content category such as Travel, Food, Fashion, Lifestyle, Nature, Pets, Fitness, Technology, Product or Art.',
        '- "engagement_score": an integer from 1 to 100 estimating how well the post will perform.',
        f"- Tone of voice: {TONES.get(opts.tone, TONES['friendly'])}.",
        f'- Language: write "description" and "alternative_description" in {LANGUAGES.get(opts.language, LANGUAGES["en"])["prompt"]}. '
        "Hashtags may use that script or English.",
        "- Describe only what is visible. Do not invent brand names, places or people.",
    ]
    if opts.extra:
        lines.append(f"- Context from the account owner: {opts.extra}")
    return "\n".join(lines)


def image_type(image_b64: str) -> str:
    """The MIME type of a Base64 image (APIs that take raw Base64 want it declared exactly)."""
    try:
        head = base64.b64decode(image_b64[:64] + "=" * (-len(image_b64[:64]) % 4))
    except (binascii.Error, ValueError):
        head = b""
    return sniff_image(head) or "image/jpeg"


class TextAIProvider(IntegrationProvider):
    """A vision model that writes the post. Subclasses implement complete() and fetch_models()."""

    unconfigured = "Text AI is not configured on the server"
    # The model used when none is chosen; the provider's settings may name another.
    default_model_name: ClassVar[str] = ""
    # Sampling temperature, or None to leave the API's default (some models accept only that).
    temperature: ClassVar[float | None] = None

    def __init__(self, http: HttpClient, config: Config, values: Values | None = None) -> None:
        """``values`` are the provider's settings; without them, its defaults and the environment's."""
        self.http = http
        self.values: Values = values if values is not None else {**self.defaults(), **self.from_env(config)}
        self.api_key = clean_secret(self.values.get("api_key", ""))
        self.default_model = self.values.get("default_model") or self.default_model_name
        allowed = split_list(self.values.get("available_models", ""))
        # Models the browser may choose; empty allows any. The default model is always allowed.
        self.allowed_models = (self.default_model, *(m for m in allowed if m != self.default_model)) if allowed else ()
        self.timeout = config.groq_timeout
        self.budget = config.request_budget
        self.problem = self.check_config()

    # -- configuration ------------------------------------------------------------------------------------------------

    def check_config(self) -> str | None:
        return self.check_values({**self.values, "api_key": self.api_key})

    @property
    def host(self) -> str:
        """The API's host name, shown in the dashboard."""
        return ""

    def request_timeout(self) -> int:
        """Worst case for one generation (retries plus a repair turn), for the browser's own timeout."""
        return browser_timeout(worst_case_seconds(self.timeout) * 2 + 15, self.budget)

    def public_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"configured": self.configured, "problem": self.problem, "provider": self.name,
                                "label": self.label, "model": self.default_model, "models": list(self.allowed_models),
                                "timeout": self.timeout, "requestTimeout": self.request_timeout()}
        if self.host:
            info["host"] = self.host
        return info

    # -- the provider's own API -----------------------------------------------------------------------------------------

    @abstractmethod
    def complete(self, messages: list[Message], model: str, on_retry: RetryCallback) -> str:
        """Sends the conversation (after SYSTEM_PROMPT) and returns the reply's text."""

    @abstractmethod
    def fetch_models(self, on_retry: RetryCallback) -> list[str]:
        """The models the account can use. A ServiceError with kind not_found means the API has no list."""

    # -- the interface the rest of Poster uses --------------------------------------------------------------------------

    def generate_social_post(self, image_b64: str, opts: GenerationOptions, on_retry: RetryCallback) -> dict[str, Any]:
        """Analyses one image and returns the whole post. Malformed JSON gets one automatic repair turn."""
        self.require_configured()
        messages = [Message("user", build_prompt(opts), image_b64)]
        raw = self.reply(messages, opts.model, on_retry)
        repaired = False
        try:
            content, issues = normalize_content(extract_json_object(raw), opts.desc_max)
        except ServiceError as error:
            if error.kind != "parse":
                raise
            log.warning("Malformed %s reply; requesting a repair: %s", self.label, truncate(raw, 200))
            messages = [*messages, Message("assistant", raw), Message("user", REPAIR_PROMPT)]
            raw = self.reply(messages, opts.model, on_retry)
            content, issues = normalize_content(extract_json_object(raw), opts.desc_max)
            repaired = True
        return {"content": content, "issues": issues, "model": opts.model, "repaired": repaired, "provider": self.name}

    def generate_caption(self, image_b64: str, opts: GenerationOptions, on_retry: RetryCallback) -> dict[str, str]:
        """The caption and the alternative caption alone."""
        content = self.generate_social_post(image_b64, opts, on_retry)["content"]
        return {"description": content["description"], "alternative_description": content["alternative_description"]}

    def generate_hashtags(self, image_b64: str, opts: GenerationOptions, on_retry: RetryCallback) -> list[str]:
        """The hashtags alone."""
        return list(self.generate_social_post(image_b64, opts, on_retry)["content"]["hashtags"])

    def list_models(self, on_retry: RetryCallback) -> list[str]:
        """The account's models, or [] when the API does not list them."""
        self.require_configured()
        try:
            return self.fetch_models(on_retry)
        except ServiceError as error:
            if error.kind == "not_found":
                return []
            raise

    def test_connection(self, model: str, on_retry: RetryCallback) -> dict[str, Any]:
        """Lists the account's models (which checks the key) and whether ``model`` is among them."""
        models = self.list_models(on_retry)
        available = model.lower() in {m.lower() for m in models} if models else None
        return {"models": models, "modelAvailable": available, "host": self.host, "provider": self.name, "label": self.label}

    # The names the routes used before providers could be switched.
    def generate(self, image_b64: str, opts: GenerationOptions, on_retry: RetryCallback) -> dict[str, Any]:
        return self.generate_social_post(image_b64, opts, on_retry)

    def test(self, model: str, on_retry: RetryCallback) -> dict[str, Any]:
        self.require_configured()
        return self.test_connection(model, on_retry)

    # -- helpers --------------------------------------------------------------------------------------------------------

    def reply(self, messages: list[Message], model: str, on_retry: RetryCallback) -> str:
        reply = self.complete(messages, model, on_retry)
        if not reply.strip():
            raise ServiceError(f"{self.label} returned an empty reply.", kind="parse", service=self.service)
        return reply

    def post_json(self, url: str, body: dict[str, Any], headers: dict[str, str], on_retry: RetryCallback,
                  timeout: int | None = None) -> tuple[dict[str, Any], str]:
        """POSTs JSON (with retries) and returns the JSON object it answers with, and the raw text."""
        _, data, text = retry_request(
            lambda: self.http.request("POST", url, service=self.service, timeout=timeout or self.timeout, headers=headers,
                                      json=body),
            on_retry=on_retry,
        )
        if not isinstance(data, dict):
            raise ServiceError(f"{self.label} returned a response that is not JSON.", kind="parse", service=self.service,
                               details=truncate(text, 300))
        return data, text

    def get_json(self, url: str, headers: dict[str, str], on_retry: RetryCallback,
                 params: dict[str, Any] | None = None) -> dict[str, Any]:
        _, data, text = retry_request(
            lambda: self.http.request("GET", url, service=self.service, timeout=API["text_ai"]["test_timeout"], headers=headers,
                                      params=params),
            on_retry=on_retry,
        )
        if not isinstance(data, dict):
            raise ServiceError(f"{self.label} returned a model list that is not JSON.", kind="parse", service=self.service,
                               details=truncate(text, 300))
        return data

    def empty_reply(self, data: Any, text: str) -> ServiceError:
        reason = extract_error_detail(data)
        return ServiceError(f"{self.label} returned an empty reply{f' ({reason})' if reason else ''}.", kind="parse",
                            service=self.service, details=truncate(text, 300))


class OpenAICompatibleProvider(TextAIProvider):
    """Providers that speak OpenAI's /chat/completions (Groq, OpenAI, Azure OpenAI)."""

    @abstractmethod
    def chat_url(self, model: str) -> str: ...

    @abstractmethod
    def headers(self) -> dict[str, str]: ...

    def models_url(self) -> str:
        return ""

    @property
    def host(self) -> str:
        return urlparse(self.chat_url(self.default_model)).netloc

    def body(self, messages: list[Message], model: str) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "messages": self.wire_messages(messages), "stream": False}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        body["response_format"] = {"type": "json_object"}
        return body

    @staticmethod
    def wire_messages(messages: list[Message]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for message in messages:
            if message.image:
                content: Any = [{"type": "text", "text": message.text},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{message.image}"}}]
            else:
                content = message.text
            wire.append({"role": message.role, "content": content})
        return wire

    def complete(self, messages: list[Message], model: str, on_retry: RetryCallback) -> str:
        data, text = self.post_json(self.chat_url(model), self.body(messages, model), self.headers(), on_retry)
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message = first.get("message")
        reply = message.get("content") if isinstance(message, dict) else None
        if not isinstance(reply, str) or not reply.strip():
            raise self.empty_reply(data, text)
        return reply

    def fetch_models(self, on_retry: RetryCallback) -> list[str]:
        url = self.models_url()
        if not url:
            raise ServiceError(f"{self.label} does not list models.", kind="not_found", service=self.service)
        data = self.get_json(url, self.headers(), on_retry)
        items = data.get("data")
        models = [str(m.get("id") or "") for m in items if isinstance(m, dict)] if isinstance(items, list) else []
        return [m for m in models if m]
