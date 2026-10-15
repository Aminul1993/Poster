"""Ollama: one image in; a caption, an alternative caption, hashtags, a category and an engagement score out."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from poster.config import API, TONES, Config, check_url, is_placeholder
from poster.content import extract_json_object, normalize_content
from poster.errors import ServiceError, extract_error_detail, truncate
from poster.http import HttpClient
from poster.retry import RetryCallback, browser_timeout, retry_request, worst_case_seconds
from poster.services import ConfiguredService

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


class OllamaService(ConfiguredService):
    """Vision copywriting via Ollama's /api/chat (or an OpenAI-compatible endpoint)."""

    service = "Ollama"
    unconfigured = "Ollama is not configured on the server"

    def __init__(self, http: HttpClient, config: Config) -> None:
        self.http = http
        self.endpoint = config.ollama_endpoint
        self.api_key = "" if is_placeholder(config.ollama_api_key) else config.ollama_api_key
        self.default_model = config.model
        # Models the browser may choose; empty allows any.
        self.allowed_models = config.ollama_models
        self.timeout = config.ollama_timeout
        self.budget = config.request_budget
        self.problem = self._check_config()

    def _check_config(self) -> str | None:
        problem = check_url(self.endpoint, "OLLAMA_ENDPOINT", https_only=False)
        if problem:
            return problem
        host = (urlparse(self.endpoint).hostname or "").lower()
        if (host == "ollama.com" or host.endswith(".ollama.com")) and not self.api_key:
            return "OLLAMA_API_KEY is not set (required for Ollama Cloud)."
        return None

    def resolve(self) -> tuple[str, str, str]:
        """(chat URL, style "native" | "openai", base URL) for the configured endpoint."""
        url = urlparse(self.endpoint)
        path = url.path.rstrip("/")
        origin = f"{url.scheme}://{url.netloc}"
        native, openai = API["ollama"]["native_chat_path"], API["ollama"]["openai_chat_path"]
        if path.endswith(native):
            return f"{origin}{path}", "native", f"{origin}{path[: -len(native)]}"
        if path.endswith(openai):
            return f"{origin}{path}", "openai", f"{origin}{path[: -len(openai)]}"
        return f"{origin}{path}{native}", "native", f"{origin}{path}"

    def request_timeout(self) -> int:
        """Worst case for one generation (retries plus a repair turn), for the browser's own timeout."""
        return browser_timeout(worst_case_seconds(self.timeout) * 2 + 15, self.budget)

    def public_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"configured": self.configured, "problem": self.problem, "model": self.default_model,
                                "models": list(self.allowed_models), "timeout": self.timeout,
                                "requestTimeout": self.request_timeout()}
        if not check_url(self.endpoint, "OLLAMA_ENDPOINT", https_only=False):
            _, style, _ = self.resolve()
            info.update(host=urlparse(self.endpoint).netloc, style=style)
        return info

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
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
            "- Describe only what is visible. Do not invent brand names, places or people.",
        ]
        if opts.extra:
            lines.append(f"- Context from the account owner: {opts.extra}")
        return "\n".join(lines)

    def build_messages(self, opts: GenerationOptions, image_b64: str, style: str) -> list[dict[str, Any]]:
        prompt = self.build_prompt(opts)
        if style == "native":
            return [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt, "images": [image_b64]}]
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": prompt},
                                             {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]}]

    def chat(self, messages: list[dict[str, Any]], opts: GenerationOptions, on_retry: RetryCallback) -> str:
        """One chat request (with retries); returns the reply text."""
        url, style, _ = self.resolve()
        body: dict[str, Any]
        if style == "native":
            body = {"model": opts.model, "messages": messages, "stream": False, "format": "json", "options": {"temperature": 0.7}}
        else:
            body = {"model": opts.model, "messages": messages, "stream": False, "temperature": 0.7,
                    "response_format": {"type": "json_object"}}
        _, data, text = retry_request(
            lambda: self.http.request("POST", url, service=self.service, timeout=self.timeout, headers=self.headers(), json=body),
            on_retry=on_retry,
        )
        if not isinstance(data, dict):
            raise ServiceError("Ollama returned a response that is not JSON.", kind="parse", service=self.service,
                               details=truncate(text, 300))
        if style == "native":
            message = data.get("message")
            reply = message.get("content") if isinstance(message, dict) else None
        else:
            choices = data.get("choices")
            first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            message = first.get("message")
            reply = message.get("content") if isinstance(message, dict) else None
        if not isinstance(reply, str) or not reply.strip():
            reason = extract_error_detail(data)
            raise ServiceError(f"Ollama returned an empty reply{f' ({reason})' if reason else ''}.", kind="parse",
                               service=self.service, details=truncate(text, 300))
        return reply

    def generate(self, image_b64: str, opts: GenerationOptions, on_retry: RetryCallback) -> dict[str, Any]:
        """Analyses one image. Malformed JSON gets one automatic repair turn."""
        self.require_configured()
        _, style, _ = self.resolve()
        messages = self.build_messages(opts, image_b64, style)
        raw = self.chat(messages, opts, on_retry)
        repaired = False
        try:
            content, issues = normalize_content(extract_json_object(raw), opts.desc_max)
        except ServiceError as error:
            if error.kind != "parse":
                raise
            log.warning("Malformed Ollama reply; requesting a repair: %s", truncate(raw, 200))
            repair = [*messages, {"role": "assistant", "content": raw}, {"role": "user", "content": REPAIR_PROMPT}]
            raw = self.chat(repair, opts, on_retry)
            content, issues = normalize_content(extract_json_object(raw), opts.desc_max)
            repaired = True
        return {"content": content, "issues": issues, "model": opts.model, "repaired": repaired}

    def test(self, model: str, on_retry: RetryCallback) -> dict[str, Any]:
        """Lists the endpoint's models and checks that ``model`` is among them."""
        self.require_configured()
        _, _, base = self.resolve()
        host = urlparse(base).netloc
        try:
            _, data, _ = retry_request(
                lambda: self.http.request("GET", f"{base}{API['ollama']['tags_path']}", service=self.service,
                                          timeout=API["ollama"]["test_timeout"], headers=self.headers()),
                on_retry=on_retry,
            )
        except ServiceError as error:
            if error.kind == "not_found":
                return {"models": [], "modelAvailable": None, "host": host}
            raise
        models = []
        if isinstance(data, dict) and isinstance(data.get("models"), list):
            models = [str(m.get("name") or m.get("model") or "") for m in data["models"] if isinstance(m, dict)]
            models = [m for m in models if m]
        wanted = model.lower()
        available = None
        if models:
            available = any(n.lower() == wanted or n.lower().startswith(f"{wanted}-") or n.lower() == f"{wanted}:latest"
                            or n.lower().split(":")[0] == wanted for n in models)
        return {"models": models, "modelAvailable": available, "host": host}
