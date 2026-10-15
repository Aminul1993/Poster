"""Configuration: environment variables (or a .env file), plus every constant the
services share: API paths, GraphQL documents, retry rules and limits."""

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
    """Empty values and template placeholders such as ``YOUR_OLLAMA_API_KEY``."""
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

    ollama_endpoint: str = "https://ollama.com"
    ollama_api_key: str = ""
    model: str = "gemma4:31b"
    # Models the browser may ask for; empty allows any.
    ollama_models: tuple[str, ...] = ()
    buffer_access_token: str = ""
    buffer_api_url: str = "https://api.buffer.com"
    media_upload_endpoint: str = ""
    media_upload_preset: str = ""
    blob_read_write_token: str = ""
    blob_store_id: str = ""
    blob_api_url: str = "https://vercel.com/api/blob"
    public_base_url: str = ""
    pollinations_api_key: str = ""
    ollama_timeout: int = 120
    buffer_timeout: int = 30
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
        model = env.text("OLLAMA_MODEL") or env.text("MODEL", "gemma4:31b")
        allowed = env.items("OLLAMA_MODELS")
        return cls(
            ollama_endpoint=env.text("OLLAMA_ENDPOINT", "https://ollama.com"),
            ollama_api_key=env.text("OLLAMA_API_KEY"),
            model=model,
            ollama_models=(model, *(m for m in allowed if m != model)) if allowed else (),
            buffer_access_token=env.text("BUFFER_ACCESS_TOKEN"),
            buffer_api_url=env.text("BUFFER_API_URL", "https://api.buffer.com"),
            media_upload_endpoint=env.text("MEDIA_UPLOAD_ENDPOINT"),
            media_upload_preset=env.text("MEDIA_UPLOAD_PRESET"),
            blob_read_write_token=env.text("BLOB_READ_WRITE_TOKEN"),
            blob_store_id=env.text("BLOB_STORE_ID"),
            blob_api_url=env.text("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob"),
            public_base_url=env.text("PUBLIC_BASE_URL"),
            pollinations_api_key=env.text("POLLINATIONS_API_KEY"),
            ollama_timeout=env.number("OLLAMA_TIMEOUT", 120, 10, 600),
            buffer_timeout=env.number("BUFFER_TIMEOUT", 30, 5, 120),
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
            "post_status": "query PosterPostStatus($input: PostInput!) { post(input: $input) { id status } }",
            "posts": (
                "query PosterPosts($input: PostsInput!, $first: Int, $after: String) { posts(input: $input, first: $first, after: $after) "
                "{ edges { node { ...PosterPostFields } } pageInfo { hasNextPage endCursor } } }"
            ),
            "edit_post": (
                "mutation PosterEditPost($input: EditPostInput!) { editPost(input: $input) { __typename "
                "... on PostActionSuccess { post { ...PosterPostFields } } ... on MutationError { message } } }"
            ),
            "delete_post": (
                "mutation PosterDeletePost($input: DeletePostInput!) { deletePost(input: $input) { __typename "
                "... on DeletePostSuccess { id } ... on MutationError { message } } }"
            ),
        },
        # What the posts list and editPost read about each post.
        "post_fields": (
            "fragment PosterPostFields on Post { id status dueAt sentAt createdAt text channelId channelService externalLink "
            "channel { name displayName } assets { thumbnail source } error { message } }"
        ),
        # Post statuses that mean the post is out of reach: it was published or is being published now.
        "published_statuses": frozenset({"sent", "sending"}),
        # The posts list's filters (None: every status) and their order by publish time.
        "post_filters": {"all": None, "upcoming": ["scheduled", "needs_approval", "sending"], "published": ["sent"]},
        "post_sort": {"all": "desc", "upcoming": "asc", "published": "desc"},
        # Posts per page. Buffer allows few requests (100 per 15 minutes on the free plan), so pages are generous.
        "posts_page_size": 30,
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
        # Buffer and the networks it posts to do not take AVIF, so /api/media/image/<name>.jpg converts the copy back.
        "served_quality": 90,
        # The stored thumbnail, also AVIF. At this size a faster speed costs almost nothing in file size.
        "thumbnail_size": (400, 400),
        "thumbnail_quality": 60,
        "thumbnail_speed": 6,
    },
    "image_gen": {
        "url": "https://gen.pollinations.ai/image",
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
