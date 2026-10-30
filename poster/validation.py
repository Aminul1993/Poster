"""Validation of what the browser sends. Every parser raises ServiceError(kind="validation")."""

from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from flask import request

from poster.config import API, LANGUAGES, LIMITS, TONES, is_local_host
from poster.errors import ServiceError
from poster.imaging import sniff_image
from poster.services.ollama import GenerationOptions

_MODEL_RE = re.compile(r"^[\w.\-/:]{1,100}$")
_ID_RE = re.compile(r"^[\w.\-:]{1,100}$")  # Buffer channel, post and organization IDs


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


def parse_generation_options(body: dict[str, Any], *, default_model: str,
                             allowed_models: tuple[str, ...] = ()) -> GenerationOptions:
    """The browser's model and copywriting preferences. ``allowed_models`` (when set) limits the model."""
    model = body.get("model") or default_model
    if not isinstance(model, str) or not _MODEL_RE.fullmatch(model.strip()):
        raise ServiceError("Model names contain only letters, numbers and . - _ / : characters.", kind="validation")
    model = model.strip()
    if allowed_models and model not in allowed_models:
        raise ServiceError(f'The model "{model}" is not available on this server. Choose one of: {", ".join(allowed_models)}.',
                           kind="validation")
    tone = body.get("tone") or "friendly"
    if tone not in TONES:
        raise ServiceError(f'Unknown tone "{tone}".', kind="validation")
    language = body.get("language") or "en"
    if language not in LANGUAGES:
        raise ServiceError(f'Unknown caption language "{language}".', kind="validation")
    extra = body.get("extraInstructions") or ""
    if not isinstance(extra, str) or len(extra) > LIMITS["max_extra_chars"]:
        raise ServiceError(f"Extra instructions must be text of at most {LIMITS['max_extra_chars']} characters.", kind="validation")
    desc_min = int_field(body, "descMin", LIMITS["default_caption_min"], LIMITS["min_caption_chars"], LIMITS["max_caption_min"])
    desc_max = int_field(body, "descMax", LIMITS["default_caption_max"], LIMITS["min_caption_max"], LIMITS["max_caption_chars"])
    if desc_min >= desc_max:
        raise ServiceError("descMax must be greater than descMin.", kind="validation")
    return GenerationOptions(model=model, tone=tone, extra=extra.strip(), desc_min=desc_min, desc_max=desc_max, language=language)


def parse_prompt(value: Any) -> str:
    if not isinstance(value, str):
        raise ServiceError('"prompt" must be text describing the image.', kind="validation")
    prompt = re.sub(r"\s+", " ", value).strip()
    if len(prompt) < LIMITS["min_prompt_chars"]:
        raise ServiceError("Describe the image in a few words.", kind="validation")
    if len(prompt) > LIMITS["max_prompt_chars"]:
        raise ServiceError(f"The description must be at most {LIMITS['max_prompt_chars']} characters.", kind="validation")
    return prompt


def parse_aspect(value: Any) -> str:
    aspect = value or "square"
    if not isinstance(aspect, str) or aspect not in API["image_gen"]["aspects"]:
        raise ServiceError(f'"aspect" must be one of: {", ".join(API["image_gen"]["aspects"])}.', kind="validation")
    return aspect


def parse_image_b64(value: Any) -> str:
    """Validates a Base64 image (JPEG/PNG/WEBP, size-limited) and returns clean Base64."""
    if not isinstance(value, str) or not value:
        raise ServiceError('"image" must be a Base64-encoded image.', kind="validation")
    if value.startswith("data:"):
        value = value.partition(",")[2]
    value = re.sub(r"\s+", "", value)
    if len(value) > LIMITS["max_analysis_bytes"] * 4 // 3 + 4:
        raise ServiceError("The image is too large. Send a downscaled copy of it.", kind="validation")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError('"image" is not valid Base64.', kind="validation") from exc
    if not sniff_image(raw):
        raise ServiceError("The image must be a JPG, PNG or WEBP file.", kind="validation")
    return str(value)


def parse_due_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ServiceError('"dueAt" must be an ISO 8601 timestamp.', kind="validation")
    try:
        due = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ServiceError('"dueAt" is not a valid ISO 8601 timestamp.', kind="validation") from exc
    if due.tzinfo is None:
        raise ServiceError('"dueAt" must include a timezone (for example 2026-10-08T13:30:00Z).', kind="validation")
    now = datetime.now(UTC)
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


def parse_post_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError("The post text is empty.", kind="validation")
    if len(value) > LIMITS["max_text_chars"]:
        raise ServiceError(f"The post text is longer than {LIMITS['max_text_chars']} characters.", kind="validation")
    return value


def _id_list(value: Any, *, empty: str, too_many: str, invalid: str) -> list[str]:
    """A non-empty, de-duplicated list of at most max_channels Buffer IDs."""
    if not isinstance(value, list) or not value:
        raise ServiceError(empty, kind="validation")
    if len(value) > LIMITS["max_channels"]:
        raise ServiceError(too_many, kind="validation")
    if not all(isinstance(item, str) and _ID_RE.fullmatch(item) for item in value):
        raise ServiceError(invalid, kind="validation")
    return list(dict.fromkeys(value))


def parse_post_ids(value: Any) -> list[str]:
    return _id_list(value, empty='"postIds" must list at least one Buffer post ID.',
                    too_many=f"At most {LIMITS['max_channels']} posts per request.",
                    invalid="Post IDs contain only letters, numbers and . - _ : characters.")


def parse_channel_ids(value: Any) -> list[str]:
    return _id_list(value, empty="Choose at least one Buffer channel.",
                    too_many=f"At most {LIMITS['max_channels']} channels per post.",
                    invalid="Channel IDs contain only letters, numbers and . - _ : characters.")


def parse_organization_ids(value: Any) -> list[str]:
    """Organization IDs the browser knows from connecting; empty means the server looks them up."""
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > 10 or not all(isinstance(v, str) and _ID_RE.fullmatch(v) for v in value):
        raise ServiceError('"organizationIds" must be a short list of Buffer organization IDs.', kind="validation")
    return list(dict.fromkeys(value))


def parse_cursors(value: Any) -> dict[str, str] | None:
    """The previous page's "next": a cursor per organization, or None for the first page."""
    if value is None:
        return None
    if not isinstance(value, dict) or not value or len(value) > 10 or not all(
            isinstance(k, str) and _ID_RE.fullmatch(k) and isinstance(v, str) and 0 < len(v) <= 500 for k, v in value.items()):
        raise ServiceError('"after" must be the "next" value of the previous page.', kind="validation")
    return value


def parse_post_filter(value: Any) -> str:
    status_filter = value or "all"
    if status_filter not in API["buffer"]["post_filters"]:
        raise ServiceError(f'"filter" must be one of: {", ".join(API["buffer"]["post_filters"])}.', kind="validation")
    return str(status_filter)
