"""Model reply -> clean post content: JSON extraction, hashtags, captions, scores."""

from __future__ import annotations

import itertools
import json
import re
import unicodedata
from typing import Any

from poster.config import LIMITS
from poster.errors import ServiceError, truncate

# Combining marks (Unicode category M): vowel signs and viramas in Bengali, Hindi, Tamil and many other
# scripts. Python's \w leaves them out, which turned #বাংলাদেশ into #বলদশ, so every pattern adds them back.
MARKS = "".join(chr(c) for c in itertools.chain(range(0x300, 0x1E950), range(0xE0100, 0xE01F0))
                if unicodedata.category(chr(c))[0] == "M")

_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE_RE = re.compile(r"```(?:json)?", re.I)
# Zero-width non-joiner and joiner: inside a word they choose a conjunct's shape (র\u200d্য versus র্য), so hashtags keep them.
JOINERS = "\u200c\u200d"
_NON_TAG_RE = re.compile(rf"[^\w{MARKS}{JOINERS}]")
_TRAILING_TAGS_RE = re.compile(rf"(?:\s*#[\w{MARKS}{JOINERS}]+)+\s*$")
_WORD_RE = re.compile(rf"(?:[^\W_]|[{MARKS}]){{4,}}")
STOP_WORDS = frozenset(
    "this that with from your have what when into just like more than they them their there here about every some "
    "these those will been were make made feel feels still only very over under where while which would could should".split()
)
FALLBACK_TAGS = ("#instagood", "#photooftheday", "#contentcreator", "#dailyinspiration", "#socialmedia",
                 "#picoftheday", "#creative", "#explorepage", "#goodvibes", "#instadaily")


def normalize_hashtag(raw: Any) -> str | None:
    """Canonical ``#tag`` (letters, combining marks, digits, underscore) or None."""
    tag = _NON_TAG_RE.sub("", unicodedata.normalize("NFC", str(raw or "")).strip().lstrip("#")).strip(JOINERS)
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
    words = [w for w in _WORD_RE.findall(text.lower()) if w not in STOP_WORDS]
    for candidate in [f"#{w}" for w in words] + list(FALLBACK_TAGS):
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
    category = re.sub(r"\s+", " ", category).strip()[: LIMITS["max_category_chars"]] if isinstance(category, str) else ""
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
