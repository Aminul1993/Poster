"""Encryption of saved credentials (Fernet under SETTINGS_ENCRYPTION_KEY) and the masking of secrets."""

from __future__ import annotations

import pytest

from poster.crypto import SecretBox, generate_key, is_masked, mask_secret
from poster.errors import ServiceError


def test_round_trip() -> None:
    box = SecretBox(generate_key())
    token = box.encrypt({"api_key": "sk-secret-value", "default_model": "gpt-4.1-mini"})
    assert "sk-secret-value" not in token and "gpt-4.1-mini" not in token
    assert box.decrypt(token) == {"api_key": "sk-secret-value", "default_model": "gpt-4.1-mini"}


def test_every_encryption_is_different() -> None:
    box = SecretBox(generate_key())
    assert box.encrypt({"a": "b"}) != box.encrypt({"a": "b"})


def test_a_different_key_cannot_decrypt() -> None:
    token = SecretBox(generate_key()).encrypt({"api_key": "x"})
    with pytest.raises(ServiceError) as caught:
        SecretBox(generate_key()).decrypt(token)
    assert caught.value.kind == "config" and "does not match" in caught.value.message


def test_rotation_keeps_old_tokens_readable() -> None:
    old, new = generate_key(), generate_key()
    token = SecretBox(old).encrypt({"api_key": "x"})
    rotating = SecretBox(f"{new},{old}")
    assert rotating.decrypt(token) == {"api_key": "x"}
    rotated = rotating.rotate(token)
    assert SecretBox(new).decrypt(rotated) == {"api_key": "x"}
    with pytest.raises(ServiceError):
        SecretBox(old).decrypt(rotated)


@pytest.mark.parametrize(("keys", "problem"), [("", "is not set"), ("not-a-fernet-key", "not a valid Fernet key"),
                                               (" , ", "is not set")])
def test_unusable_keys(keys: str, problem: str) -> None:
    box = SecretBox(keys)
    assert not box.configured and problem in (box.problem or "")
    with pytest.raises(ServiceError) as caught:
        box.encrypt({"a": "b"})
    assert caught.value.kind == "config" and "SETTINGS_ENCRYPTION_KEY" in caught.value.hint


def test_tampered_token_is_refused() -> None:
    box = SecretBox(generate_key())
    token = box.encrypt({"api_key": "x"})
    with pytest.raises(ServiceError):
        box.decrypt(token[:-4] + "AAAA")


@pytest.mark.parametrize(("secret", "masked"), [
    ("", ""),
    ("short", "********"),
    ("test-meta-token", "***********oken"),
    ("sk-proj-0123456789abcdef1234", "sk-p********************1234"),
    ("EAAB" + "x" * 60 + "XYZ9", "EAAB" + "*" * 32 + "XYZ9"),
])
def test_masking(secret: str, masked: str) -> None:
    assert mask_secret(secret) == masked
    assert not masked or is_masked(masked)
    shown = len(masked) - masked.count("*")
    assert shown == 0 if len(secret) < 12 else shown <= max(4, len(secret) // 3)
