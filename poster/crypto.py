"""Encryption of the credentials saved in Credentials & Integrations, and masking of secrets shown to the browser.

Credentials are stored as Fernet tokens (AES-128-CBC with an HMAC-SHA256 tag) under SETTINGS_ENCRYPTION_KEY,
never in plain text. The variable may list several keys, comma-separated: the first encrypts, every one
decrypts, so a key can be rotated without losing what was saved (see tools/migrate_settings.py --rotate).
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from poster.errors import ServiceError

SERVICE = "Settings"
KEY_HINT = ('Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            "and set it as SETTINGS_ENCRYPTION_KEY.")


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def mask_secret(value: str) -> str:
    """A secret as the browser may see it: its first and last four characters around a run of asterisks
    (``sk-p********************************1234``). A shorter secret shows only its last four (from 12
    characters), or nothing at all, so no more than a sixth of it is ever shown."""
    if not value:
        return ""
    if len(value) < 12:
        return "*" * 8
    if len(value) < 24:
        return f"{'*' * (len(value) - 4)}{value[-4:]}"
    return f"{value[:4]}{'*' * min(len(value) - 8, 32)}{value[-4:]}"


def is_masked(value: str) -> bool:
    """Whether the browser sent back a masked value (unchanged) instead of a new secret."""
    return "*" * 8 in value


class SecretBox:
    """Encrypts and decrypts JSON objects under SETTINGS_ENCRYPTION_KEY. ``problem`` says why it cannot, or is None."""

    def __init__(self, keys: str) -> None:
        self._fernet: MultiFernet | None = None
        self.problem: str | None = None
        candidates = [key.strip() for key in keys.split(",") if key.strip()]
        if not candidates:
            self.problem = "SETTINGS_ENCRYPTION_KEY is not set, so credentials cannot be saved in the database."
            return
        try:
            self._fernet = MultiFernet([Fernet(key.encode("ascii")) for key in candidates])
        except (ValueError, UnicodeEncodeError):
            self.problem = "SETTINGS_ENCRYPTION_KEY is not a valid Fernet key (32 url-safe Base64-encoded bytes)."

    @property
    def configured(self) -> bool:
        return self.problem is None

    def require(self) -> MultiFernet:
        if self._fernet is None:
            raise ServiceError(self.problem or "Encryption is not configured.", kind="config", service=SERVICE, hint=KEY_HINT)
        return self._fernet

    def encrypt(self, values: dict[str, Any]) -> str:
        return self.require().encrypt(json.dumps(values, sort_keys=True).encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> dict[str, Any]:
        """The object ``token`` holds. A token no key opens (the key changed) is a configuration error."""
        try:
            data = json.loads(self.require().decrypt(token.encode("ascii")))
        except (InvalidToken, UnicodeEncodeError, ValueError) as exc:
            raise ServiceError("Saved credentials could not be decrypted: SETTINGS_ENCRYPTION_KEY does not match the key "
                               "they were saved with.", kind="config", service=SERVICE,
                               hint="Add the previous key after the current one (comma-separated), or save the credentials "
                                    "again.") from exc
        if not isinstance(data, dict):
            raise ServiceError("Saved credentials are not an object.", kind="parse", service=SERVICE)
        return data

    def rotate(self, token: str) -> str:
        """``token`` encrypted again under the first key."""
        return self.require().rotate(token.encode("ascii")).decode("ascii")
