"""Model reply -> post content, including hashtags in scripts with combining marks."""

from __future__ import annotations

import unicodedata

import pytest

from poster.content import extract_json_object, normalize_content, normalize_hashtag, normalize_hashtag_list, repair_json, top_up_hashtags
from poster.errors import ServiceError


@pytest.mark.parametrize(("raw", "expected"), [
    ("#বাংলাদেশ", "#বাংলাদেশ"),  # Bengali vowel signs
    ("हिन्दी", "#हिन्दी"),  # Devanagari virama
    ("#தமிழ்", "#தமிழ்"),  # Tamil
    ("#র\u200d্যালি", "#র\u200d্যালি"),  # zero-width joiner picks the ra-phala form
    ("#\u200cনাম\u200d", "#নাম"),  # joiners at the edges are dropped
    ("#café", "#café"),
    ("café", "#café"),  # decomposed accent, normalised to NFC
    ("#hello-world!", "#helloworld"),
    ("##snake_case", "#snake_case"),
    ("2026", None),
    ("!!!", None),
    ("#" + "a" * 81, None),
])
def test_normalize_hashtag(raw: str, expected: str | None) -> None:
    assert normalize_hashtag(raw) == expected


def test_hashtag_list_dedupes_case_insensitively() -> None:
    assert normalize_hashtag_list(["#Travel", "travel", "#বাংলা", "বাংলা", "#Sea"]) == ["#Travel", "#বাংলা", "#Sea"]


def test_top_up_uses_words_from_any_script() -> None:
    tags = top_up_hashtags(["#ঢাকা"], "সুন্দর সকাল ঢাকায় with friends")
    expected = ["#ঢাকা", "#সুন্দর", "#সকাল", "#ঢাকায়", "#friends"]
    assert tags[:5] == [unicodedata.normalize("NFC", tag) for tag in expected]
    assert len(tags) == 10


def test_trailing_hashtags_in_any_script_are_split_off() -> None:
    content, _ = normalize_content({"description": "সুন্দর সকাল ঢাকায় #বাংলাদেশ #ঢাকা", "hashtags": []}, 2200)
    assert content["description"] == "সুন্দর সকাল ঢাকায়"
    assert content["hashtags"][:2] == ["#বাংলাদেশ", "#ঢাকা"]


def test_caption_that_is_only_hashtags_is_kept() -> None:
    content, _ = normalize_content({"description": "#one #two", "hashtags": ["x"]}, 2200)
    assert content["description"] == "#one #two"


@pytest.mark.parametrize(("score", "expected"), [(0.5, 50), (87, 87), ("73%", 73), (0, 1), (250, 100), (True, 50), (None, 50)])
def test_engagement_score(score: object, expected: int) -> None:
    content, _ = normalize_content({"description": "A caption", "engagement_score": score}, 2200)
    assert content["engagement_score"] == expected


def test_caption_is_cut_to_the_maximum() -> None:
    content, _ = normalize_content({"description": "x" * 50}, 20)
    assert content["description"] == "x" * 20


def test_alternative_becomes_the_caption_when_alone() -> None:
    content, issues = normalize_content({"alternative_caption": "Only this one"}, 2200)
    assert content["description"] == "Only this one" and content["alternative_description"] == ""
    assert "No alternative caption was returned." in issues


def test_missing_caption_is_a_parse_error() -> None:
    with pytest.raises(ServiceError) as caught:
        normalize_content({"hashtags": ["#a"]}, 2200)
    assert caught.value.kind == "parse"


def test_repair_and_extract() -> None:
    assert repair_json('{“a”: [1, 2,],}') == '{"a": [1, 2]}'
    assert extract_json_object("<think>{}</think> sure: ```json\n{\"a\": 1,}\n```") == {"a": 1}
    with pytest.raises(ServiceError):
        extract_json_object("[1, 2]")
