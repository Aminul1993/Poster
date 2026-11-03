"""Validation of what the browser sends. Every parser raises ServiceError(kind="validation")."""

from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from flask import request

from poster.activity import stamp
from poster.config import API, LANGUAGES, LIMITS, TONES, is_local_host
from poster.errors import ServiceError, truncate
from poster.imaging import sniff_image
from poster.services.groq import GenerationOptions

_MODEL_RE = re.compile(r"^[\w.\-/:]{1,100}$")
_ID_RE = re.compile(r"^[\w.\-:]{1,100}$")  # channel, post and log entry IDs, and request keys
_CATEGORY_RE = re.compile(r"^[a-z]{1,20}$")


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
        raise ServiceError("The image URL must start with https:// so Facebook and Instagram can fetch it.", kind="validation")
    if is_local_host(url.hostname or ""):
        raise ServiceError("Facebook and Instagram cannot reach local addresses. Use a publicly hosted image URL.",
                           kind="validation")
    return value.strip()


def parse_post_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError("The post text is empty.", kind="validation")
    if len(value) > LIMITS["max_text_chars"]:
        raise ServiceError(f"The post text is longer than {LIMITS['max_text_chars']} characters.", kind="validation")
    return value


def _id_list(value: Any, *, empty: str, too_many: str, invalid: str) -> list[str]:
    """A non-empty, de-duplicated list of at most max_channels IDs."""
    if not isinstance(value, list) or not value:
        raise ServiceError(empty, kind="validation")
    if len(value) > LIMITS["max_channels"]:
        raise ServiceError(too_many, kind="validation")
    if not all(isinstance(item, str) and _ID_RE.fullmatch(item) for item in value):
        raise ServiceError(invalid, kind="validation")
    return list(dict.fromkeys(value))


def parse_post_ids(value: Any) -> list[str]:
    return _id_list(value, empty='"postIds" must list at least one post ID.',
                    too_many=f"At most {LIMITS['max_channels']} posts per request.",
                    invalid="Post IDs contain only letters, numbers and . - _ : characters.")


def parse_channel_ids(value: Any) -> list[str]:
    return _id_list(value, empty="Choose at least one channel.",
                    too_many=f"At most {LIMITS['max_channels']} channels per post.",
                    invalid="Channel IDs contain only letters, numbers and . - _ : characters.")


def parse_request_key(value: Any) -> str | None:
    """The browser's ID for one schedule request: sent again, the same posts are returned, not added twice."""
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ServiceError('"requestKey" contains only letters, numbers and . - _ : characters.', kind="validation")
    return value


def parse_cursor(value: Any) -> int | None:
    """The previous page's "next", or None for the first page."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.isdigit() or len(value) > 9:
        raise ServiceError('"after" must be the "next" value of the previous page.', kind="validation")
    return int(value)


def parse_post_filter(value: Any) -> str:
    status_filter = value or "all"
    if status_filter not in API["social"]["post_filters"]:
        raise ServiceError(f'"filter" must be one of: {", ".join(API["social"]["post_filters"])}.', kind="validation")
    return str(status_filter)


def parse_activity_filter(value: Any) -> str:
    name = value or "all"
    if name not in API["activity"]["filters"]:
        raise ServiceError(f'"filter" must be one of: {", ".join(API["activity"]["filters"])}.', kind="validation")
    return str(name)


def parse_activity_entries(value: Any) -> list[dict[str, Any]]:
    """Activity log entries the dashboard recorded, as stored. Long text is shortened, and a time in the
    future (a device clock running fast) becomes now, so the entry cannot stay on top of the log."""
    rules = API["activity"]
    if not isinstance(value, list) or not value:
        raise ServiceError('"entries" must list at least one log entry.', kind="validation")
    if len(value) > rules["max_batch"]:
        raise ServiceError(f"At most {rules['max_batch']} log entries per request.", kind="validation")
    now = datetime.now(UTC)
    entries = []
    for item in value:
        if not isinstance(item, dict):
            raise ServiceError("Each log entry must be a JSON object.", kind="validation")
        entry_id, level, category, message, details = (item.get(key) for key in ("id", "level", "category", "message", "details"))
        if not isinstance(entry_id, str) or not _ID_RE.fullmatch(entry_id):
            raise ServiceError("Log entry IDs contain only letters, numbers and . - _ : characters.", kind="validation")
        if level not in rules["levels"]:
            raise ServiceError(f'Log entry "level" must be one of: {", ".join(rules["levels"])}.', kind="validation")
        if not isinstance(category, str) or not _CATEGORY_RE.fullmatch(category):
            raise ServiceError('Log entry "category" must be a short lowercase word.', kind="validation")
        if not isinstance(message, str) or not message.strip():
            raise ServiceError("A log entry has no message.", kind="validation")
        if details is not None and not isinstance(details, str):
            raise ServiceError('Log entry "details" must be text.', kind="validation")
        entries.append({"id": entry_id, "ts": stamp(min(_entry_time(item.get("ts")), now)), "level": level, "category": category,
                        "message": truncate(message, rules["max_message_chars"]),
                        "details": truncate(details, rules["max_details_chars"]) if details else None})
    return entries


def _entry_time(value: Any) -> datetime:
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        raise ServiceError('Log entry "ts" must be an ISO 8601 timestamp with a timezone.', kind="validation")
    return when
