"""Poster: Flask backend for AI social media publishing.

This file does three jobs:

* serves the dashboard (``templates/index.html``),
* talks to Ollama: one image in; a caption, an alternative caption, hashtags,
  a category and an engagement score out,
* talks to Buffer's GraphQL API (token check, channels, scheduled posts) and,
  optionally, to a media host that turns uploads into public image URLs
  (Vercel Blob, or an upload endpoint such as a Cloudinary unsigned preset).

Credentials stay on the server, in environment variables or a ``.env`` file
next to this one, and the browser never sees them. The browser only calls this
server (same origin), so Ollama's and Buffer's CORS rules do not apply.

Run::

    pip install -r requirements.txt
    copy .env.example .env      (then fill in your keys)
    python app.py               -> http://127.0.0.1:5000
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlencode, urlparse

import requests
from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
APP_VERSION = "2.0.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("poster")


# =============================================================================
# Configuration
# =============================================================================

def load_env_file(path: Path) -> None:
    """Loads KEY=VALUE lines from a .env file. Real environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return min(high, max(low, int(raw)))
    except ValueError:
        log.warning("%s=%r is not a whole number; using %s.", name, raw, default)
        return default


def is_placeholder(value: str) -> bool:
    """Empty values and template placeholders such as ``YOUR_OLLAMA_API_KEY``."""
    return not value or value.upper().startswith("YOUR_")


CONFIG: dict[str, Any] = {
    "OLLAMA_ENDPOINT": _env("OLLAMA_ENDPOINT", "https://ollama.com"),
    "OLLAMA_API_KEY": _env("OLLAMA_API_KEY"),
    "BUFFER_ACCESS_TOKEN": _env("BUFFER_ACCESS_TOKEN"),
    "MODEL": _env("OLLAMA_MODEL") or _env("MODEL", "gemma4:31b"),
    # Optional extras.
    "BUFFER_API_URL": _env("BUFFER_API_URL", "https://api.buffer.com"),
    "MEDIA_UPLOAD_ENDPOINT": _env("MEDIA_UPLOAD_ENDPOINT"),
    "MEDIA_UPLOAD_PRESET": _env("MEDIA_UPLOAD_PRESET"),
    "BLOB_READ_WRITE_TOKEN": _env("BLOB_READ_WRITE_TOKEN"),
    "BLOB_STORE_ID": _env("BLOB_STORE_ID"),
    "BLOB_API_URL": _env("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob"),
    "OLLAMA_TIMEOUT": _env_int("OLLAMA_TIMEOUT", 120, 10, 600),
    "BUFFER_TIMEOUT": _env_int("BUFFER_TIMEOUT", 30, 5, 120),
    "MEDIA_TIMEOUT": _env_int("MEDIA_TIMEOUT", 120, 10, 600),
    "MODE": _env("POSTER_MODE", "auto").lower(),  # auto | demo | live
    "HOST": _env("POSTER_HOST", "127.0.0.1"),
    "PORT": _env_int("POSTER_PORT", 5000, 1, 65535),
    "DEBUG": _env("FLASK_DEBUG", "0").lower() in ("1", "true", "yes"),
}

# Central API configuration: every path, GraphQL document and retry rule.
API: dict[str, Any] = {
    "ollama": {
        "native_chat_path": "/api/chat",
        "openai_chat_path": "/v1/chat/completions",
        "tags_path": "/api/tags",
        "test_timeout": 20,
    },
    "buffer": {
        "queries": {
            "account": "query PosterAccount { account { id email organizations { id name } } }",
            "channels": (
                "query PosterChannels($organizationId: OrganizationId!) { channels(input: { organizationId: $organizationId }) "
                "{ id name displayName service timezone isDisconnected isLocked isQueuePaused } }"
            ),
            "create_post": (
                "mutation PosterCreatePost($input: CreatePostInput!) { createPost(input: $input) { __typename "
                "... on PostActionSuccess { post { id dueAt status } } ... on MutationError { message } } }"
            ),
        },
    },
    "media": {
        "file_field": "file",
        "preset_field": "upload_preset",
        "url_paths": ("secure_url", "url", "data.url", "data.link", "link", "data.display_url", "image.url", "result.url"),
        # Vercel Blob REST API, as called by @vercel/blob's put().
        "blob_api_version": "12",
        "blob_folder": "poster",
    },
    "retry": {
        "max_retries": 3,
        "delays": (1, 2, 4),
        "retryable_status": frozenset({429, 500, 502, 503, 504}),
        "retryable_kinds": frozenset({"timeout", "network", "rate_limit"}),
    },
}

LIMITS = {
    "max_image_bytes": 20 * 1024 * 1024,
    # Vercel Functions reject request bodies over 4.5 MB, so there the browser shrinks larger uploads to fit.
    "max_upload_bytes": (4 if _env("VERCEL") else 20) * 1024 * 1024,
    "max_analysis_bytes": 8 * 1024 * 1024,
    "max_text_chars": 10_000,
    "max_channels": 25,
    "min_hashtags": 10,
    "max_hashtags": 15,
    "max_extra_chars": 500,
    "max_caption_chars": 10_000,
}

TONES = {
    "friendly": "warm, conversational and approachable",
    "professional": "polished, credible and concise",
    "playful": "fun, witty and light-hearted; emojis welcome",
    "inspirational": "uplifting and motivating",
    "luxury": "elegant, refined and exclusive",
    "bold": "energetic, confident and attention-grabbing",
}


def is_local_host(hostname: str) -> bool:
    host = (hostname or "").lower().strip("[]")
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.") or host.endswith(".localhost")


def check_url(value: str, label: str, *, https_only: bool) -> str | None:
    """Returns a problem description for a configured URL, or None when usable."""
    try:
        url = urlparse(value)
    except ValueError:
        return f"{label} is not a valid URL."
    if url.scheme not in ("http", "https") or not url.netloc:
        return f"{label} must be a full http(s) URL."
    if https_only and url.scheme != "https" and not is_local_host(url.hostname or ""):
        return f"{label} must use https:// (http:// is only allowed for localhost)."
    if url.username or url.password:
        return f"{label} must not contain a username or password."
    return None


# =============================================================================
# Errors and retries
# =============================================================================

class ServiceError(Exception):
    """A classified failure, rendered to the browser as JSON.

    ``kind`` is one of: validation, config, auth, rate_limit, timeout, network,
    server, client, not_found, parse.
    """

    def __init__(self, message: str, *, kind: str = "server", status: int = 0, service: str = "",
                 hint: str = "", details: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status = status
        self.service = service
        self.hint = hint
        self.details = details
        self.retries: list[dict[str, Any]] = []

    @property
    def retryable(self) -> bool:
        return self.kind in API["retry"]["retryable_kinds"] or self.status in API["retry"]["retryable_status"]

    @property
    def http_status(self) -> int:
        return {"validation": 400, "config": 503, "rate_limit": 429, "timeout": 504}.get(self.kind, 502)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message, "kind": self.kind, "status": self.status, "service": self.service,
            "hint": self.hint, "details": self.details, "retries": self.retries,
        }


T = TypeVar("T")
RetryCallback = Callable[[int, int, ServiceError], None]


def retry_request(operation: Callable[[], T], *, should_retry: Callable[[ServiceError], bool] | None = None,
                  max_retries: int = API["retry"]["max_retries"], delays: tuple[int, ...] = API["retry"]["delays"],
                  on_retry: RetryCallback | None = None, sleep: Callable[[float], None] = time.sleep) -> T:
    """Runs ``operation`` and retries transient failures.

    Retries timeouts, temporary network failures and HTTP 429/500/502/503/504
    up to 3 times, waiting 1 s, 2 s and 4 s. Never retries 400/401/403 or
    validation errors.
    """
    check = should_retry or (lambda error: error.retryable)
    attempt = 0
    while True:
        try:
            return operation()
        except ServiceError as error:
            if attempt >= max_retries or not check(error):
                raise
            delay = delays[min(attempt, len(delays) - 1)]
            attempt += 1
            if on_retry is not None:  # RetryLog is a list, so an empty one is falsy
                on_retry(attempt, delay, error)
            sleep(delay)


class RetryLog(list):
    """Collects retry events so the browser can show them in its activity log."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    def __call__(self, attempt: int, delay: int, error: ServiceError) -> None:
        max_retries = API["retry"]["max_retries"]
        self.append({"attempt": attempt, "maxRetries": max_retries, "delayMs": delay * 1000, "message": error.message})
        log.warning("%s: retry %d/%d in %ds (%s)", self.label, attempt, max_retries, delay, error.message)


# =============================================================================
# HTTP client
# =============================================================================

def extract_error_detail(data: Any) -> str:
    """Pulls a readable message out of the common error body shapes."""
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(data.get("message"), str):
        return data["message"]
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict) and isinstance(first.get("message"), str):
            return first["message"]
    for key in ("detail", "error_description"):
        if isinstance(data.get(key), str):
            return data[key]
    return ""


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class HttpClient:
    """requests wrapper that turns every failure into a classified ServiceError."""

    def __init__(self) -> None:
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = f"Poster/{APP_VERSION}"
            self._local.session = session
        return session

    def request(self, method: str, url: str, *, service: str, timeout: int, **kwargs: Any) -> tuple[int, Any, str]:
        """Sends one request. Returns (status, parsed JSON or None, raw text)."""
        host = urlparse(url).netloc
        try:
            response = self._session().request(method, url, timeout=(min(10, timeout), timeout), **kwargs)
        except requests.Timeout as exc:
            raise ServiceError(f"{service} did not respond within {timeout} seconds.", kind="timeout", service=service,
                               hint="The service may be busy. Try again, or raise the timeout on the server.") from exc
        except requests.ConnectionError as exc:
            raise ServiceError(f"Network connection failed: could not reach {host}.", kind="network", service=service,
                               hint="Check the server's internet connection and the configured URL.") from exc
        except requests.RequestException as exc:
            raise ServiceError(f"The request to {service} failed ({exc.__class__.__name__}).", kind="network",
                               service=service) from exc
        text = response.text
        try:
            data = response.json() if text else None
        except ValueError:
            data = None
        if not response.ok:
            raise self._http_error(response, data, text, service)
        return response.status_code, data, text

    @staticmethod
    def _http_error(response: requests.Response, data: Any, text: str, service: str) -> ServiceError:
        status = response.status_code
        detail = extract_error_detail(data) or (text.strip() if text and len(text) <= 300 and "<html" not in text.lower() else "")
        said = f' {service} said: "{truncate(detail, 240)}"' if detail else ""
        common = {"status": status, "service": service, "details": detail}
        if status == 401:
            return ServiceError(f"Authentication failed. {service} rejected the API key (HTTP 401).{said}", kind="auth",
                                hint=f"Check the {service} key in the server configuration.", **common)
        if status == 403:
            return ServiceError(f"Access denied (HTTP 403): the {service} credentials lack permission for this action.{said}",
                                kind="auth", hint=f"Check the {service} key and its permissions.", **common)
        if status == 404:
            extra = " Check the endpoint URL and that the model exists." if service == "Ollama" else " Check the configured URL."
            return ServiceError(f"{service} endpoint not found (HTTP 404).{extra}{said}", kind="not_found", **common)
        if status == 408:
            return ServiceError(f"{service} timed out (HTTP 408).", kind="timeout", **common)
        if status == 402:
            return ServiceError(f"{service} requires a paid plan for this request (HTTP 402).{said}", kind="client", **common)
        if status == 413:
            return ServiceError(f"The request was too large for {service} (HTTP 413).{said}", kind="client", **common)
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            wait = f" Retry after {retry_after}s." if retry_after else ""
            return ServiceError(f"{service} rate limit reached (HTTP 429).{wait}{said}", kind="rate_limit",
                                hint="Too many requests. Wait a minute, or lower Parallel requests in Settings.", **common)
        if status >= 500:
            return ServiceError(f"{service} server error (HTTP {status}).{said}", kind="server",
                                hint="This is usually temporary. Try again shortly.", **common)
        return ServiceError(f"{service} rejected the request (HTTP {status}).{said}", kind="client", **common)


# =============================================================================
# Content parsing and normalisation (model reply -> clean post content)
# =============================================================================

_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE_RE = re.compile(r"```(?:json)?", re.I)
_TRAILING_TAGS_RE = re.compile(r"(?:\s*#\w+)+\s*$")
_WORD_RE = re.compile(r"[^\W_]{4,}")
_STOP_WORDS = frozenset(
    "this that with from your have what when into just like more than they them their there here about every some "
    "these those will been were make made feel feels still only very over under where while which would could should".split()
)
_FALLBACK_TAGS = ("#instagood", "#photooftheday", "#contentcreator", "#dailyinspiration", "#socialmedia",
                  "#picoftheday", "#creative", "#explorepage", "#goodvibes", "#instadaily")


def normalize_hashtag(raw: Any) -> str | None:
    """Canonical ``#tag`` (letters, digits, underscore) or None."""
    tag = re.sub(r"[^\w]", "", unicodedata.normalize("NFC", str(raw or "")).strip().lstrip("#"))
    if not tag or tag.isdigit() or len(tag) > 80:
        return None
    return f"#{tag}"


def normalize_hashtag_list(items: list[Any]) -> list[str]:
    """Normalises and de-duplicates (case-insensitively), keeping order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        tag = normalize_hashtag(item)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


def top_up_hashtags(tags: list[str], text: str) -> list[str]:
    """Adds hashtags derived from the text until the minimum count is reached."""
    result = list(tags)
    seen = {t.lower() for t in result}
    words = [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP_WORDS]
    for candidate in [f"#{w}" for w in words] + list(_FALLBACK_TAGS):
        if len(result) >= LIMITS["min_hashtags"]:
            break
        tag = normalize_hashtag(candidate)
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            result.append(tag)
    return result


def repair_json(text: str) -> str:
    """Fixes common model slips: smart quotes and trailing commas."""
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r",\s*([}\]])", r"\1", text)


def extract_json_object(raw: str) -> dict[str, Any]:
    """Finds and parses the JSON object in a model reply."""
    text = _FENCE_RE.sub("", _THINK_RE.sub("", raw)).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ServiceError("The AI reply was not valid JSON.", kind="parse", service="Ollama", details=truncate(text, 300))
    candidate = text[start:end + 1]
    for attempt in (candidate, repair_json(candidate)):
        try:
            data = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise ServiceError("The AI reply was not valid JSON.", kind="parse", service="Ollama", details=truncate(candidate, 300))


def normalize_content(raw: dict[str, Any], desc_max: int) -> tuple[dict[str, Any], list[str]]:
    """Validates the model's JSON and coerces it into the post-content shape.

    Tolerates key aliases, hashtags as a string, scores between 0 and 1 and
    hashtags appended to captions. Raises ServiceError(kind="parse") when no
    caption can be recovered.
    """
    data = {re.sub(r"[\s-]+", "_", str(k).lower()): v for k, v in raw.items()}

    def pick(*keys: str) -> Any:
        for key in keys:
            if data.get(key) is not None:
                return data[key]
        return None

    issues: list[str] = []
    inline_tags: list[str] = []

    def clean_caption(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        quotes = "\"'“”‘’"
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.replace("\r\n", "\n").split("\n")]
        text = "\n".join(lines).strip().strip(quotes).strip()
        match = _TRAILING_TAGS_RE.search(text)
        if match and match.start() > 0:
            inline_tags.extend(match.group(0).split())
            text = text[: match.start()].strip().strip(quotes).strip()
        return text[:desc_max]

    description = clean_caption(pick("description", "caption", "social_media_caption", "primary_caption", "main_caption"))
    alternative = clean_caption(pick("alternative_description", "alternative_caption", "alt_description",
                                     "alternate_caption", "alternative", "alt_caption"))
    if not description and alternative:
        description, alternative = alternative, ""
    if not description:
        raise ServiceError("The AI response did not include a caption.", kind="parse", service="Ollama",
                           details=truncate(json.dumps(raw, ensure_ascii=False), 400))
    if not alternative:
        issues.append("No alternative caption was returned.")

    raw_tags = pick("hashtags", "tags", "hash_tags")
    tag_list = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else re.split(r"[\s,;]+", str(raw_tags or ""))
    hashtags = normalize_hashtag_list(tag_list + inline_tags)
    if len(hashtags) > LIMITS["max_hashtags"]:
        issues.append(f"Trimmed hashtags from {len(hashtags)} to {LIMITS['max_hashtags']}.")
        hashtags = hashtags[: LIMITS["max_hashtags"]]

    category = pick("category", "content_category", "categories")
    if isinstance(category, list):
        category = category[0] if category else ""
    category = re.sub(r"\s+", " ", category).strip()[:40] if isinstance(category, str) else ""
    if category:
        category = category[0].upper() + category[1:]
    else:
        category = "General"
        issues.append('No category was returned; using "General".')

    if len(hashtags) < LIMITS["min_hashtags"]:
        before = len(hashtags)
        hashtags = top_up_hashtags(hashtags, f"{category} {description} {alternative}")
        if len(hashtags) > before:
            issues.append(f"Added {len(hashtags) - before} hashtags to reach {len(hashtags)}.")

    score_raw = pick("engagement_score", "engagement", "score", "estimated_engagement_score")
    score: float | None = None
    if isinstance(score_raw, (int, float)) and not isinstance(score_raw, bool):
        score = float(score_raw)
    elif isinstance(score_raw, str):
        try:
            score = float(re.sub(r"[^\d.]", "", score_raw))
        except ValueError:
            score = None
    if score is None or score != score:  # missing or NaN
        score = 50.0
        issues.append("No engagement score was returned; using 50.")
    elif 0 < score <= 1:
        score *= 100
    engagement = int(min(100, max(1, round(score))))

    content = {
        "description": description,
        "alternative_description": alternative,
        "hashtags": hashtags,
        "category": category,
        "engagement_score": engagement,
    }
    return content, issues


# =============================================================================
# Ollama
# =============================================================================

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


class OllamaService:
    """Vision copywriting via Ollama's /api/chat (or an OpenAI-compatible endpoint)."""

    service = "Ollama"

    def __init__(self, http: HttpClient, config: dict[str, Any]) -> None:
        self.http = http
        self.endpoint = config["OLLAMA_ENDPOINT"]
        self.api_key = "" if is_placeholder(config["OLLAMA_API_KEY"]) else config["OLLAMA_API_KEY"]
        self.default_model = config["MODEL"]
        self.timeout = config["OLLAMA_TIMEOUT"]
        self.problem = self._check_config()

    def _check_config(self) -> str | None:
        problem = check_url(self.endpoint, "OLLAMA_ENDPOINT", https_only=False)
        if problem:
            return problem
        host = (urlparse(self.endpoint).hostname or "").lower()
        if (host == "ollama.com" or host.endswith(".ollama.com")) and not self.api_key:
            return "OLLAMA_API_KEY is not set (required for Ollama Cloud)."
        return None

    @property
    def configured(self) -> bool:
        return self.problem is None

    def require_configured(self) -> None:
        if self.problem:
            raise ServiceError(f"Ollama is not configured on the server: {self.problem}", kind="config", service=self.service,
                               hint="Set it in the environment or .env file and restart Poster.")

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
        retry = API["retry"]
        return (self.timeout * (retry["max_retries"] + 1) + sum(retry["delays"])) * 2 + 15

    def public_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {"configured": self.configured, "problem": self.problem, "model": self.default_model,
                                "timeout": self.timeout, "requestTimeout": self.request_timeout()}
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
            repair = messages + [{"role": "assistant", "content": raw}, {"role": "user", "content": REPAIR_PROMPT}]
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


# =============================================================================
# Buffer
# =============================================================================

def _create_post_retryable(error: ServiceError) -> bool:
    """createPost is not idempotent: a timeout or 5xx may still have created the
    post. Only failures that guarantee nothing was created are retried."""
    return error.kind == "rate_limit" or error.status in (429, 503)


class BufferService:
    """Buffer GraphQL API with a personal access token."""

    service = "Buffer"

    def __init__(self, http: HttpClient, config: dict[str, Any]) -> None:
        self.http = http
        self.token = "" if is_placeholder(config["BUFFER_ACCESS_TOKEN"]) else config["BUFFER_ACCESS_TOKEN"]
        self.api_url = config["BUFFER_API_URL"]
        self.timeout = config["BUFFER_TIMEOUT"]
        self.problem = self._check_config()

    def _check_config(self) -> str | None:
        if not self.token:
            return "BUFFER_ACCESS_TOKEN is not set."
        if re.search(r"\s", self.token):
            return "BUFFER_ACCESS_TOKEN contains whitespace."
        return check_url(self.api_url, "BUFFER_API_URL", https_only=True)

    @property
    def configured(self) -> bool:
        return self.problem is None

    @property
    def token_id(self) -> str:
        """Short, non-reversible fingerprint so the browser can tell when the token changed."""
        return hashlib.sha256(self.token.encode()).hexdigest()[:12] if self.token else ""

    def require_configured(self) -> None:
        if self.problem:
            raise ServiceError(f"Buffer is not configured on the server: {self.problem}", kind="config", service=self.service,
                               hint="Set it in the environment or .env file and restart Poster.")

    def public_info(self) -> dict[str, Any]:
        retry = API["retry"]
        return {
            "configured": self.configured, "problem": self.problem, "tokenId": self.token_id,
            "host": urlparse(self.api_url).netloc, "timeout": self.timeout,
            "requestTimeout": self.timeout * (retry["max_retries"] + 1) + sum(retry["delays"]) + 10,
        }

    def graphql(self, query: str, variables: dict[str, Any], *, action: str, on_retry: RetryCallback,
                should_retry: Callable[[ServiceError], bool] | None = None) -> dict[str, Any]:
        """Runs a GraphQL document. GraphQL errors (which may arrive with HTTP
        200) are mapped inside the retried function, so rate limits and
        transient server errors are retried like HTTP failures."""
        self.require_configured()

        def execute() -> dict[str, Any]:
            _, data, text = self.http.request(
                "POST", self.api_url, service=self.service, timeout=self.timeout,
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "Authorization": f"Bearer {self.token}"},
                json={"query": query, "variables": variables},
            )
            if not isinstance(data, dict):
                raise ServiceError(f"Buffer returned an invalid response while trying to {action}.", kind="parse",
                                   service=self.service, details=truncate(text, 300))
            errors = data.get("errors")
            if isinstance(errors, list) and errors:
                raise self.map_graphql_error(errors[0], action)
            payload = data.get("data")
            if not isinstance(payload, dict):
                raise ServiceError(f"Buffer returned no data while trying to {action}.", kind="parse", service=self.service,
                                   details=truncate(text, 300))
            return payload

        return retry_request(execute, should_retry=should_retry, on_retry=on_retry)

    def map_graphql_error(self, error: Any, action: str) -> ServiceError:
        error = error if isinstance(error, dict) else {}
        extensions = error.get("extensions") if isinstance(error.get("extensions"), dict) else {}
        code = str(extensions.get("code") or "").upper()
        message = error.get("message") if isinstance(error.get("message"), str) and error.get("message") else "unknown error"
        if code in ("UNAUTHORIZED", "UNAUTHENTICATED"):
            return ServiceError(f"Authentication failed. Buffer rejected the access token ({message}).", kind="auth",
                                status=401, service=self.service,
                                hint="Create a personal API key in Buffer and set BUFFER_ACCESS_TOKEN on the server.")
        if code == "FORBIDDEN":
            return ServiceError(f"Buffer denied access while trying to {action}: {message}.", kind="auth", status=403,
                                service=self.service, hint="Check that the channel and organization belong to this account.")
        if code == "NOT_FOUND":
            return ServiceError(f"Buffer could not find what was needed to {action}: {message}. The channel or organization "
                                "may have been removed.", kind="not_found", service=self.service)
        if code == "RATE_LIMIT_EXCEEDED":
            return ServiceError(f"Buffer rate limit reached while trying to {action}.", kind="rate_limit", status=429,
                                service=self.service, hint="Wait a minute and try again.")
        if code == "UNEXPECTED":
            return ServiceError(f"Buffer had a temporary problem while trying to {action}: {message}.", kind="server",
                                status=500, service=self.service)
        return ServiceError(f"Buffer could not {action}: {message}.", kind="client", service=self.service)

    def validate_token(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Checks the token by reading the account and its organizations."""
        data = self.graphql(API["buffer"]["queries"]["account"], {}, action="verify the access token", on_retry=on_retry)
        account = data.get("account")
        if not isinstance(account, dict) or not isinstance(account.get("organizations"), list):
            raise ServiceError("Buffer returned unexpected account data.", kind="parse", service=self.service)
        return {
            "id": str(account.get("id") or ""),
            "email": str(account.get("email") or ""),
            "organizations": [{"id": str(o["id"]), "name": str(o.get("name") or "Organization")}
                              for o in account["organizations"] if isinstance(o, dict) and o.get("id")],
        }

    def connect(self, on_retry: RetryCallback) -> dict[str, Any]:
        """Validates the token and loads every channel of every organization."""
        account = self.validate_token(on_retry)
        if not account["organizations"]:
            raise ServiceError("This Buffer account has no organizations, so there are no channels to post to.",
                               kind="client", service=self.service)
        channels = []
        for organization in account["organizations"]:
            data = self.graphql(API["buffer"]["queries"]["channels"], {"organizationId": organization["id"]},
                                action="load channels", on_retry=on_retry)
            items = data.get("channels")
            if not isinstance(items, list):
                raise ServiceError("Buffer returned an unexpected channel list.", kind="parse", service=self.service)
            for channel in items:
                if not isinstance(channel, dict) or not channel.get("id"):
                    continue
                channels.append({
                    "id": str(channel["id"]),
                    "name": str(channel.get("displayName") or channel.get("name") or channel["id"]),
                    "handle": str(channel.get("name") or ""),
                    "service": str(channel.get("service") or ""),
                    "timezone": str(channel.get("timezone") or ""),
                    "organizationId": organization["id"],
                    "organizationName": organization["name"],
                    "isDisconnected": bool(channel.get("isDisconnected")),
                    "isLocked": bool(channel.get("isLocked")),
                    "isQueuePaused": bool(channel.get("isQueuePaused")),
                    "usable": not channel.get("isDisconnected") and not channel.get("isLocked"),
                })
        return {"account": account, "channels": channels}

    def schedule(self, text: str, channel_ids: list[str], due_at: datetime, media_url: str | None,
                 on_retry: RetryCallback) -> dict[str, Any]:
        """Creates one scheduled post per channel. Channels are independent:
        failures on some are reported while the others still go out."""
        self.require_configured()
        posts: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        due_iso = due_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for channel_id in channel_ids:
            post_input: dict[str, Any] = {
                "channelId": channel_id, "text": text, "schedulingType": "automatic",
                "mode": "customScheduled", "dueAt": due_iso, "aiAssisted": True,
            }
            if media_url:
                post_input["assets"] = [{"image": {"url": media_url}}]
            try:
                data = self.graphql(API["buffer"]["queries"]["create_post"], {"input": post_input}, action="create the post",
                                    on_retry=on_retry, should_retry=_create_post_retryable)
            except ServiceError as error:
                if error.kind in ("auth", "config", "network") and not posts:
                    raise
                failures.append({"channelId": channel_id, "message": error.message})
                continue
            result = data.get("createPost")
            post = result.get("post") if isinstance(result, dict) else None
            if isinstance(post, dict) and post.get("id"):
                posts.append({"channelId": channel_id, "postId": str(post["id"]), "dueAt": str(post.get("dueAt") or due_iso),
                              "status": str(post.get("status") or "scheduled")})
            else:
                reason = result.get("message") if isinstance(result, dict) and result.get("message") else (
                    f"Buffer returned {result.get('__typename') if isinstance(result, dict) else 'an unexpected response'}.")
                failures.append({"channelId": channel_id, "message": str(reason)})
        if not posts:
            summary = " · ".join(f"{f['channelId']}: {f['message']}" for f in failures)
            raise ServiceError(f"Buffer did not schedule the post. {summary}", kind="client", service=self.service)
        log.info("Scheduled post for %s on %d channel(s)", due_iso, len(posts))
        return {"posts": posts, "failures": failures}


# =============================================================================
# Media host (Buffer only accepts public image URLs)
# =============================================================================

class MediaService:
    """Uploads an image to a host that returns a public URL.

    Vercel Blob when BLOB_READ_WRITE_TOKEN is set, otherwise MEDIA_UPLOAD_ENDPOINT
    (e.g. a Cloudinary unsigned preset).
    """

    service = "Media host"

    def __init__(self, http: HttpClient, config: dict[str, Any]) -> None:
        self.http = http
        self.blob_token = config["BLOB_READ_WRITE_TOKEN"]
        token_parts = self.blob_token.split("_")  # vercel_blob_rw_<store id>_<secret>
        self.blob_store_id = config["BLOB_STORE_ID"].removeprefix("store_") or (token_parts[3] if len(token_parts) > 4 else "")
        self.blob_api_url = config["BLOB_API_URL"].rstrip("/")
        self.endpoint = config["MEDIA_UPLOAD_ENDPOINT"]
        self.preset = config["MEDIA_UPLOAD_PRESET"]
        self.timeout = config["MEDIA_TIMEOUT"]
        self.problem = self._check_config()

    @property
    def uses_blob(self) -> bool:
        return bool(self.blob_token)

    def _check_config(self) -> str | None:
        if self.uses_blob:
            if not self.blob_token.startswith("vercel_blob_rw_"):
                return "BLOB_READ_WRITE_TOKEN is not a Vercel Blob read-write token (vercel_blob_rw_...)."
            return check_url(self.blob_api_url, "VERCEL_BLOB_API_URL", https_only=True)
        if not self.endpoint:
            return "MEDIA_UPLOAD_ENDPOINT is not set."
        if self.preset and not re.fullmatch(r"[\w.\-]{1,100}", self.preset):
            return "MEDIA_UPLOAD_PRESET contains invalid characters."
        return check_url(self.endpoint, "MEDIA_UPLOAD_ENDPOINT", https_only=True)

    @property
    def configured(self) -> bool:
        return self.problem is None

    def public_info(self) -> dict[str, Any]:
        retry = API["retry"]
        host = "Vercel Blob" if self.uses_blob else urlparse(self.endpoint).netloc if self.endpoint else ""
        return {"configured": self.configured, "problem": self.problem, "host": host,
                "requestTimeout": self.timeout * (retry["max_retries"] + 1) + sum(retry["delays"]) + 10}

    def upload(self, filename: str, data: bytes, mimetype: str, on_retry: RetryCallback) -> str:
        if self.problem:
            raise ServiceError(f"No media host is configured on the server: {self.problem}", kind="config", service=self.service,
                               hint="Set BLOB_READ_WRITE_TOKEN (Vercel Blob) or MEDIA_UPLOAD_ENDPOINT, or paste a public image URL in the editor.")
        if self.uses_blob:
            method, url = "PUT", f"{self.blob_api_url}/?{urlencode({'pathname': API['media']['blob_folder'] + '/' + filename})}"
            options: dict[str, Any] = {"data": data, "headers": self.blob_headers(mimetype)}
        else:
            form = {API["media"]["preset_field"]: self.preset} if self.preset else {}
            method, url = "POST", self.endpoint
            options = {"files": {API["media"]["file_field"]: (filename, data, mimetype)}, "data": form}
        _, payload, text = retry_request(
            lambda: self.http.request(method, url, service=self.service, timeout=self.timeout, **options),
            on_retry=on_retry,
        )
        for path in API["media"]["url_paths"]:
            value: Any = payload
            for key in path.split("."):
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, str) and re.match(r"^https?://", value, re.I):
                if not value.startswith("https://"):
                    raise ServiceError("The media host returned an http:// URL. Buffer needs https:// image URLs.",
                                       kind="client", service=self.service)
                return value
        raise ServiceError("The media host accepted the upload but did not return a public URL.", kind="parse",
                           service=self.service, details=truncate(text, 300))

    def blob_headers(self, mimetype: str) -> dict[str, str]:
        """Headers for a Vercel Blob put. Public access, because Buffer fetches the image without credentials;
        a random suffix keeps every upload at its own URL."""
        headers = {
            "Authorization": f"Bearer {self.blob_token}",
            "x-api-version": API["media"]["blob_api_version"],
            "x-vercel-blob-access": "public",
            "x-content-type": mimetype,
            "x-add-random-suffix": "1",
        }
        if self.blob_store_id:
            headers["x-vercel-blob-store-id"] = self.blob_store_id
        return headers


# =============================================================================
# Request validation
# =============================================================================

_MODEL_RE = re.compile(r"^[\w.\-/:]{1,100}$")
_CHANNEL_RE = re.compile(r"^[\w.\-:]{1,100}$")


def sniff_image(data: bytes) -> str | None:
    """Real image type from magic bytes: image/jpeg, image/png, image/webp or None."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def json_body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ServiceError("The request body must be a JSON object.", kind="validation")
    return body


def int_field(body: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = body.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ServiceError(f'"{key}" must be a number.', kind="validation")
    if not low <= value <= high:
        raise ServiceError(f'"{key}" must be between {low} and {high}.', kind="validation")
    return int(value)


def parse_generation_options(body: dict[str, Any]) -> GenerationOptions:
    model = body.get("model") or ollama.default_model
    if not isinstance(model, str) or not _MODEL_RE.fullmatch(model.strip()):
        raise ServiceError("Model names contain only letters, numbers and . - _ / : characters.", kind="validation")
    tone = body.get("tone") or "friendly"
    if tone not in TONES:
        raise ServiceError(f'Unknown tone "{tone}".', kind="validation")
    extra = body.get("extraInstructions") or ""
    if not isinstance(extra, str) or len(extra) > LIMITS["max_extra_chars"]:
        raise ServiceError(f"Extra instructions must be text of at most {LIMITS['max_extra_chars']} characters.", kind="validation")
    desc_min = int_field(body, "descMin", 10, 1, 500)
    desc_max = int_field(body, "descMax", 2200, 20, LIMITS["max_caption_chars"])
    if desc_min >= desc_max:
        raise ServiceError("descMax must be greater than descMin.", kind="validation")
    return GenerationOptions(model=model.strip(), tone=tone, extra=extra.strip(), desc_min=desc_min, desc_max=desc_max)


def parse_image_b64(value: Any) -> str:
    """Validates a Base64 image (JPEG/PNG/WEBP, size-limited) and returns clean Base64."""
    if not isinstance(value, str) or not value:
        raise ServiceError('"image" must be a Base64-encoded image.', kind="validation")
    if value.startswith("data:"):
        value = value.partition(",")[2]
    value = re.sub(r"\s+", "", value)
    if len(value) > LIMITS["max_analysis_bytes"] * 4 // 3 + 4:
        raise ServiceError("The analysis image is too large. It should be a downscaled preview.", kind="validation")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError('"image" is not valid Base64.', kind="validation") from exc
    if not sniff_image(raw):
        raise ServiceError("The image must be a JPG, PNG or WEBP file.", kind="validation")
    return value


def parse_due_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ServiceError('"dueAt" must be an ISO 8601 timestamp.', kind="validation")
    try:
        due = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ServiceError('"dueAt" is not a valid ISO 8601 timestamp.', kind="validation") from exc
    if due.tzinfo is None:
        raise ServiceError('"dueAt" must include a timezone (for example 2026-10-08T13:30:00Z).', kind="validation")
    now = datetime.now(timezone.utc)
    if due <= now:
        raise ServiceError("The publish time must be in the future.", kind="validation")
    if due > now + timedelta(days=730):
        raise ServiceError("Schedule within the next two years.", kind="validation")
    return due


def parse_media_url(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ServiceError('"mediaUrl" must be a URL.', kind="validation")
    url = urlparse(value.strip())
    if url.scheme != "https" or not url.netloc:
        raise ServiceError("The image URL must start with https:// so Buffer can fetch it.", kind="validation")
    if is_local_host(url.hostname or ""):
        raise ServiceError("Buffer cannot reach local addresses. Use a publicly hosted image URL.", kind="validation")
    return value.strip()


def parse_channel_ids(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ServiceError("Choose at least one Buffer channel.", kind="validation")
    if len(value) > LIMITS["max_channels"]:
        raise ServiceError(f"At most {LIMITS['max_channels']} channels per post.", kind="validation")
    ids: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _CHANNEL_RE.fullmatch(item):
            raise ServiceError("Channel IDs contain only letters, numbers and . - _ : characters.", kind="validation")
        if item not in ids:
            ids.append(item)
    return ids


# =============================================================================
# Flask application
# =============================================================================

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = LIMITS["max_image_bytes"] + 1024 * 1024
app.json.sort_keys = False

http = HttpClient()
ollama = OllamaService(http, CONFIG)
buffer = BufferService(http, CONFIG)
media = MediaService(http, CONFIG)


def default_mode() -> str:
    if CONFIG["MODE"] in ("demo", "live"):
        return CONFIG["MODE"]
    return "live" if ollama.configured else "demo"


def server_config() -> dict[str, Any]:
    """Non-secret configuration for the browser."""
    return {
        "version": APP_VERSION,
        "mode": default_mode(),
        "ollama": ollama.public_info(),
        "buffer": buffer.public_info(),
        "media": media.public_info(),
        "limits": {"maxImageBytes": LIMITS["max_image_bytes"], "maxUploadBytes": LIMITS["max_upload_bytes"],
                   "maxChannels": LIMITS["max_channels"]},
    }


@app.before_request
def guard_api() -> Any:
    """Per-request CSP nonce, plus CSRF protection for state-changing API calls.

    The custom header forces a CORS preflight for cross-site requests, which
    this server never approves, so other websites cannot use your credentials.
    """
    g.csp_nonce = secrets.token_urlsafe(16)
    if request.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("X-Poster-Request") != "1":
            return jsonify(error={"message": "Missing X-Poster-Request header.", "kind": "validation"}), 403
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify(error={"message": "Cross-origin requests are not allowed.", "kind": "validation"}), 403
    return None


@app.after_request
def security_headers(response: Any) -> Any:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if response.mimetype == "text/html":
        nonce = getattr(g, "csp_nonce", "")
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(ServiceError)
def handle_service_error(error: ServiceError) -> Any:
    log.info("%s error (%s): %s", error.service or "Request", error.kind, error.message)
    return jsonify(error=error.to_dict()), error.http_status


@app.errorhandler(HTTPException)
def handle_http_exception(error: HTTPException) -> Any:
    if not request.path.startswith("/api/"):
        return error
    message = error.description
    if error.code == 413:
        message = f"The upload is larger than the server allows ({LIMITS['max_image_bytes'] // (1024 * 1024)} MB)."
    return jsonify(error={"message": message, "kind": "validation" if (error.code or 500) < 500 else "server"}), error.code


@app.errorhandler(Exception)
def handle_unexpected(error: Exception) -> Any:
    if isinstance(error, HTTPException):
        return handle_http_exception(error)
    log.exception("Unexpected error")
    if request.path.startswith("/api/"):
        return jsonify(error={"message": "Unexpected server error. Check the server log.", "kind": "server"}), 500
    return "Unexpected server error. Check the server log.", 500


def respond(result: dict[str, Any], retries: RetryLog) -> Any:
    return jsonify({**result, "retries": list(retries)})


def run(label: str, action: Callable[[RetryLog], dict[str, Any]]) -> Any:
    """Runs a service action and attaches its retry log to the response or error."""
    retries = RetryLog(label)
    try:
        result = action(retries)
    except ServiceError as error:
        error.retries = list(retries)
        raise
    return respond(result, retries)


@app.get("/")
def index() -> Any:
    return render_template("index.html", server_config=server_config(), csp_nonce=g.csp_nonce)


@app.get("/api/health")
def health() -> Any:
    return jsonify(status="ok", version=APP_VERSION, ollama=ollama.configured, buffer=buffer.configured, media=media.configured)


@app.get("/api/config")
def config_endpoint() -> Any:
    return jsonify(server_config())


@app.post("/api/ollama/test")
def ollama_test() -> Any:
    opts = parse_generation_options(json_body())
    return run("Ollama test", lambda retries: ollama.test(opts.model, retries))


@app.post("/api/ollama/generate")
def ollama_generate() -> Any:
    body = json_body()
    opts = parse_generation_options(body)
    image_b64 = parse_image_b64(body.get("image"))
    name = str(body.get("fileName") or "image")[:200]
    return run(f"Ollama generate ({name})", lambda retries: ollama.generate(image_b64, opts, retries))


@app.post("/api/buffer/validate")
def buffer_validate() -> Any:
    return run("Buffer validate", lambda retries: {"account": buffer.validate_token(retries)})


@app.post("/api/buffer/connect")
def buffer_connect() -> Any:
    return run("Buffer connect", lambda retries: {**buffer.connect(retries), "tokenId": buffer.token_id})


@app.post("/api/buffer/schedule")
def buffer_schedule() -> Any:
    body = json_body()
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ServiceError("The post text is empty.", kind="validation")
    if len(text) > LIMITS["max_text_chars"]:
        raise ServiceError(f"The post text is longer than {LIMITS['max_text_chars']} characters.", kind="validation")
    channel_ids = parse_channel_ids(body.get("channelIds"))
    due_at = parse_due_at(body.get("dueAt"))
    media_url = parse_media_url(body.get("mediaUrl"))
    return run("Buffer schedule", lambda retries: buffer.schedule(text, channel_ids, due_at, media_url, retries))


@app.post("/api/media/upload")
def media_upload() -> Any:
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ServiceError('Send the image as a multipart "file" field.', kind="validation")
    data = file.read(LIMITS["max_image_bytes"] + 1)
    if len(data) > LIMITS["max_image_bytes"]:
        raise ServiceError("The image is larger than 20 MB.", kind="validation")
    mimetype = sniff_image(data)
    if not mimetype:
        raise ServiceError("The file is not a JPG, PNG or WEBP image.", kind="validation")
    filename = re.sub(r"[^\w.\-]", "_", file.filename)[:120] or "image"
    return run("Media upload", lambda retries: {"url": media.upload(filename, data, mimetype, retries)})


def log_startup() -> None:
    for name, service in (("Ollama", ollama), ("Buffer", buffer), ("Media host", media)):
        if service.configured:
            log.info("%s: configured", name)
        else:
            log.warning("%s: not configured (%s)", name, service.problem)
    log.info("Default mode: %s", default_mode())


if __name__ == "__main__":
    log_startup()
    app.run(host=CONFIG["HOST"], port=CONFIG["PORT"], debug=CONFIG["DEBUG"], threaded=True)
