"""Configuration: environment variables (or a .env file), plus every constant the
services share: API paths, Graph API fields, retry rules and limits."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
APP_VERSION = "2.1.0"

log = logging.getLogger("poster")


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines from a .env file (comments, blank lines and ``export`` prefixes allowed)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path) -> None:
    """Loads a .env file into the process environment. Real environment variables win."""
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


def is_placeholder(value: str) -> bool:
    """Empty values and template placeholders such as ``YOUR_GROQ_API_KEY``."""
    return not value or value.upper().startswith("YOUR_")


def is_local_host(hostname: str) -> bool:
    host = (hostname or "").lower().strip("[]")
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.") or host.endswith(".localhost")  # noqa: S104 (a comparison, not a bind)


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


class _Env:
    """Typed reads from an environment mapping."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self.values = values

    def text(self, name: str, default: str = "") -> str:
        return (self.values.get(name) or default).strip()

    def number(self, name: str, default: int, low: int, high: int) -> int:
        raw = self.text(name)
        if not raw:
            return default
        try:
            return min(high, max(low, int(raw)))
        except ValueError:
            log.warning("%s=%r is not a whole number; using %s.", name, raw, default)
            return default

    def items(self, name: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.strip() for item in self.text(name).split(",") if item.strip()))


@dataclass(frozen=True)
class Config:
    """Server settings. Build it with Config.from_env(); every field has a working default."""

    groq_endpoint: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = ""
    model: str = "qwen/qwen3.8-27b"
    # Models the browser may ask for; empty allows any.
    groq_models: tuple[str, ...] = ()
    # A Meta system user token with access to the Facebook Pages and their Instagram accounts.
    meta_access_token: str = ""
    meta_graph_url: str = "https://graph.facebook.com"
    meta_graph_version: str = "v23.0"
    # With the app secret, every Graph API call carries an appsecret_proof.
    meta_app_secret: str = ""
    # Postgres URL for the post queue; without one (local runs only), a SQLite file next to app.py.
    database_url: str = ""
    # Bearer token the scheduler sends to /api/publish/due.
    cron_secret: str = ""
    media_upload_endpoint: str = ""
    media_upload_preset: str = ""
    blob_read_write_token: str = ""
    blob_store_id: str = ""
    blob_api_url: str = "https://vercel.com/api/blob"
    public_base_url: str = ""
    pollinations_api_key: str = ""
    # The model that changes an uploaded image as described (see API["image_gen"]["edit_url"]).
    pollinations_edit_model: str = "kontext"
    groq_timeout: int = 120
    meta_timeout: int = 30
    media_timeout: int = 120
    pollinations_timeout: int = 60
    mode: str = "auto"  # auto | demo | live
    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False
    # HTTP Basic auth password. Without it, only requests to a local address are served.
    password: str = ""
    # Seconds one request may spend on upstream calls, retries included; 0 means no limit.
    request_budget: int = 0
    on_vercel: bool = False
    max_upload_bytes: int = 20 * 1024 * 1024

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, *, env_file: Path | None = ENV_FILE) -> Config:
        """Settings from ``environ`` (default: the process environment) over the .env file."""
        if environ is None:
            if env_file is not None:
                load_env_file(env_file)
            values: Mapping[str, str] = os.environ
        else:
            values = {**(read_env_file(env_file) if env_file is not None else {}), **environ}
        env = _Env(values)
        on_vercel = bool(env.text("VERCEL"))
        model = env.text("GROQ_MODEL") or env.text("MODEL", "qwen/qwen3.8-27b")
        allowed = env.items("GROQ_MODELS")
        return cls(
            groq_endpoint=env.text("GROQ_ENDPOINT", "https://api.groq.com/openai/v1"),
            groq_api_key=env.text("GROQ_API_KEY"),
            model=model,
            groq_models=(model, *(m for m in allowed if m != model)) if allowed else (),
            meta_access_token=env.text("META_ACCESS_TOKEN"),
            meta_graph_url=env.text("META_GRAPH_URL", "https://graph.facebook.com"),
            meta_graph_version=env.text("META_GRAPH_VERSION", "v23.0"),
            meta_app_secret=env.text("META_APP_SECRET"),
            # Vercel's Supabase and Neon integrations set POSTGRES_URL (the pooled connection).
            database_url=env.text("DATABASE_URL") or env.text("POSTGRES_URL"),
            cron_secret=env.text("CRON_SECRET"),
            media_upload_endpoint=env.text("MEDIA_UPLOAD_ENDPOINT"),
            media_upload_preset=env.text("MEDIA_UPLOAD_PRESET"),
            blob_read_write_token=env.text("BLOB_READ_WRITE_TOKEN"),
            blob_store_id=env.text("BLOB_STORE_ID"),
            blob_api_url=env.text("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob"),
            public_base_url=env.text("PUBLIC_BASE_URL"),
            pollinations_api_key=env.text("POLLINATIONS_API_KEY"),
            pollinations_edit_model=env.text("POLLINATIONS_EDIT_MODEL", "kontext"),
            groq_timeout=env.number("GROQ_TIMEOUT", 120, 10, 600),
            meta_timeout=env.number("META_TIMEOUT", 30, 5, 120),
            media_timeout=env.number("MEDIA_TIMEOUT", 120, 10, 600),
            pollinations_timeout=env.number("POLLINATIONS_TIMEOUT", 60, 10, 600),
            mode=env.text("POSTER_MODE", "auto").lower(),
            host=env.text("POSTER_HOST", "127.0.0.1"),
            port=env.number("POSTER_PORT", 5000, 1, 65535),
            debug=env.text("FLASK_DEBUG", "0").lower() in ("1", "true", "yes"),
            password=env.text("POSTER_PASSWORD"),
            # Vercel stops a function at maxDuration (300 s in vercel.json); stop retrying well before that.
            request_budget=env.number("POSTER_REQUEST_BUDGET", 280 if on_vercel else 0, 0, 3600),
            on_vercel=on_vercel,
            # Vercel Functions reject request bodies over 4.5 MB, so there the browser shrinks larger uploads to fit.
            max_upload_bytes=(4 if on_vercel else 20) * 1024 * 1024,
        )


# Central API configuration: every path, Graph API field list and retry rule.
API: dict[str, Any] = {
    "groq": {
        # Relative to the base URL, e.g. https://api.groq.com/openai/v1.
        "chat_path": "/chat/completions",
        "models_path": "/models",
        "test_timeout": 20,
    },
    "meta": {
        # What /me/accounts reads about each Page: its own token, and the Instagram account linked to it.
        "account_fields": "id,name,tasks,access_token,instagram_business_account{id,username,name}",
        # Pages per /me/accounts request.
        "accounts_limit": 100,
        # Graph API error codes that mean "slow down": app, account and Page rate limits.
        "rate_limit_codes": frozenset({4, 9, 17, 32, 613}),
        # Recent posts read when looking for one whose publish answer was lost.
        "recent_posts": 25,
    },
    "social": {
        # Post statuses: scheduled (waiting), sending (being published), sent (published), error (given up on).
        # Posts out of reach: published, or being published now.
        "published_statuses": frozenset({"sent", "sending"}),
        # Posts that can still be edited or deleted. Editing a failed post puts it back in the queue.
        "editable_statuses": ("scheduled", "error"),
        # The posts list's filters (None: every status) and their order by publish time.
        "post_filters": {"all": None, "upcoming": ["scheduled", "sending"], "published": ["sent"]},
        "post_sort": {"all": "desc", "upcoming": "asc", "published": "desc"},
        "posts_page_size": 30,
        # The publisher: posts per run (a run must end well within Vercel's 300 s), tries per post, and the
        # minutes to wait before each new try.
        "publish_batch": 10,
        "max_attempts": 5,
        "retry_minutes": (2, 5, 15, 30),
        # Seconds of the time budget a post needs to be worth starting in this run.
        "min_publish_seconds": 60,
        # A post still "sending" this long after it was claimed was cut off; the next run checks whether it went out.
        "stale_minutes": 10,
        # Instagram's limits for a feed post (https://developers.facebook.com/docs/instagram-platform/content-publishing).
        "instagram_caption_chars": 2200,
        "instagram_hashtags": 30,
    },
    "activity": {
        "levels": ("info", "success", "warn", "error"),
        # The activity log's filters: entries at any of these levels or in any of these categories (None: every entry).
        "filters": {
            "all": None,
            "error": {"levels": ["error"]},
            "warn": {"levels": ["warn"], "categories": ["retry"]},
            "success": {"levels": ["success"]},
            "user": {"categories": ["user", "settings", "upload"]},
        },
        "page_size": 100,
        # Entries kept in the database; older ones are deleted as new ones arrive.
        "keep": 1000,
        # Entries per save request, and the longest message and details stored.
        "max_batch": 100,
        "max_message_chars": 2000,
        "max_details_chars": 1000,
    },
    "media": {
        "file_field": "file",
        "preset_field": "upload_preset",
        "url_paths": ("secure_url", "url", "data.url", "data.link", "link", "data.display_url", "image.url", "result.url"),
        # Vercel Blob REST API, as called by @vercel/blob's put().
        "blob_api_version": "12",
        "blob_folder": "poster",
        "blob_public_url": "https://{store_id}.public.blob.vercel-storage.com",
        # The stored copy: shrunk to fit avif_max_size, quality 0-100 (non-linear; 45-55 is very small with little
        # visible loss), speed 0 (slowest, smallest file) to 10. Speed 4 encodes about 3x faster than 2 for a
        # near-identical file.
        "avif_max_size": (1920, 1920),
        "avif_quality": 48,
        "avif_speed": 4,
        # Facebook and Instagram do not take AVIF, so /api/media/image/<name>.jpg converts the copy back.
        "served_quality": 90,
        # The stored thumbnail, also AVIF. At this size a faster speed costs almost nothing in file size.
        "thumbnail_size": (400, 400),
        "thumbnail_quality": 60,
        "thumbnail_speed": 6,
    },
    "image_gen": {
        "url": "https://gen.pollinations.ai/image",
        # OpenAI-compatible image editing: a source image and a description of the change in, a new image out.
        "edit_url": "https://gen.pollinations.ai/v1/images/edits",
        # Shapes offered in the browser, as (width, height) in pixels.
        "aspects": {"square": (1024, 1024), "portrait": (1024, 1280), "landscape": (1280, 720), "story": (720, 1280)},
    },
    "retry": {
        "max_retries": 3,
        "delays": (1, 2, 4),
        "retryable_status": frozenset({429, 500, 502, 503, 504}),
        "retryable_kinds": frozenset({"timeout", "network", "rate_limit"}),
    },
}

LIMITS: dict[str, int] = {
    "max_image_bytes": 20 * 1024 * 1024,
    "max_analysis_bytes": 8 * 1024 * 1024,
    # PNG and WEBP decode every pixel before shrinking (JPEG decodes at reduced size); 40 MP RGBA is about 160 MB.
    "max_decode_pixels": 40_000_000,
    "max_text_chars": 10_000,
    "max_channels": 25,
    "min_hashtags": 10,
    "max_hashtags": 15,
    "max_category_chars": 40,
    "max_extra_chars": 500,
    "min_caption_chars": 1,
    "max_caption_min": 500,
    "min_caption_max": 20,
    "max_caption_chars": 10_000,
    "default_caption_min": 10,
    "default_caption_max": 2200,
    "min_prompt_chars": 3,
    "max_prompt_chars": 1000,
}

TONES = {
    "friendly": "warm, conversational and approachable",
    "professional": "polished, credible and concise",
    "playful": "fun, witty and light-hearted; emojis welcome",
    "inspirational": "uplifting and motivating",
    "luxury": "elegant, refined and exclusive",
    "bold": "energetic, confident and attention-grabbing",
}

# Caption languages: the name shown in the dashboard, and how the prompt asks for it.
LANGUAGES = {
    "en": {"label": "English", "prompt": "English"},
    "bn": {"label": "Bengali", "prompt": "Bengali, in Bangla script"},
    "bn-en": {"label": "Bengali + English", "prompt": "Bengali in Bangla script, mixed naturally with English words and phrases"},
    "hi": {"label": "Hindi", "prompt": "Hindi, in Devanagari script"},
    "hi-en": {"label": "Hindi + English", "prompt": "Hindi in Devanagari script, mixed naturally with English words and phrases"},
    "ta": {"label": "Tamil", "prompt": "Tamil, in Tamil script"},
    "ta-en": {"label": "Tamil + English", "prompt": "Tamil in Tamil script, mixed naturally with English words and phrases"},
}
