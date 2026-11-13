"""Clients for the upstream services: the text and image AI providers, Meta (Facebook and Instagram) and the
media host, plus what every integration shares: a configuration problem, and the fields it is configured with."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar

from poster.config import Config, check_url, is_placeholder
from poster.errors import ServiceError


class ConfiguredService:
    """Base for a client that may be unconfigured: ``problem`` says why it cannot be used, or is None."""

    service: ClassVar[str] = ""
    # Start of the error shown when the service is used without being configured.
    unconfigured: ClassVar[str] = ""
    setup_hint: ClassVar[str] = "Set it in Settings → Credentials & Integrations, or in the environment or .env file."
    problem: str | None = None

    @property
    def configured(self) -> bool:
        return self.problem is None

    def require_configured(self) -> None:
        if self.problem:
            raise ServiceError(f"{self.unconfigured}: {self.problem}", kind="config", service=self.service,
                               hint=self.setup_hint)


@dataclass(frozen=True)
class Field:
    """One setting of an integration, as Credentials & Integrations shows and stores it.

    ``kind`` is text, secret (encrypted, and only ever sent to the browser masked), url (https:// only, except
    for local addresses), list (comma-separated) or select (one of ``options``). ``env`` names the environment
    variable the field falls back to when nothing is saved.
    """

    key: str
    label: str
    kind: str = "text"
    required: bool = False
    env: str = ""
    default: str = ""
    placeholder: str = ""
    help: str = ""
    options: tuple[str, ...] = ()
    # For a url field: whether the provider's secrets are sent to it. Changing such an address then needs the
    # secrets entered again, so a saved key cannot be redirected to another host (see SettingsService.candidate).
    sends_secrets: bool = True

    @property
    def secret(self) -> bool:
        return self.kind == "secret"

    def public(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "kind": self.kind, "required": self.required, "env": self.env,
                "default": self.default, "placeholder": self.placeholder, "help": self.help, "options": list(self.options)}

    def problem(self, value: str) -> str | None:
        """Why ``value`` cannot be used for this field, or None. An empty value is a problem only when required."""
        if not value:
            return f"{self.label} is required." if self.required else None
        if self.kind == "secret" and re.search(r"\s", value):
            return f"{self.label} contains whitespace."
        if self.kind == "url":
            return check_url(value, self.label, https_only=True)
        if self.kind == "select" and self.options and value not in self.options:
            return f"{self.label} must be one of: {', '.join(self.options)}."
        return None


Values = dict[str, str]


class IntegrationProvider(ConfiguredService):
    """An integration Poster can be configured to use: a registered name, a label, and the fields that configure it.

    Adding a provider means subclassing a provider base (TextAIProvider, ImageAIProvider), declaring
    ``name``, ``label`` and ``fields``, and registering the class in its factory's registry. Credentials &
    Integrations renders the fields, stores them encrypted and passes them back as ``values``.
    """

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""
    fields: ClassVar[tuple[Field, ...]] = ()
    # Where the owner gets a key, shown next to the fields.
    docs_url: ClassVar[str] = ""

    @classmethod
    def field(cls, key: str) -> Field | None:
        return next((f for f in cls.fields if f.key == key), None)

    @classmethod
    def from_env(cls, config: Config) -> Values:
        """The settings the environment holds for this provider (Field.env), for installations configured before
        Credentials & Integrations, or configured on purpose by environment only."""
        return {f.key: config.env_value(f.env) for f in cls.fields if f.env and config.env_value(f.env)}

    @classmethod
    def defaults(cls) -> Values:
        return {f.key: f.default for f in cls.fields if f.default}

    @classmethod
    def check_values(cls, values: Values) -> str | None:
        """The first field problem in ``values``, or None."""
        for f in cls.fields:
            problem = f.problem(values.get(f.key, ""))
            if problem:
                return problem
        return None

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {"name": cls.name, "label": cls.label, "docsUrl": cls.docs_url, "fields": [f.public() for f in cls.fields]}


def clean_secret(value: str) -> str:
    """A secret as configured: empty for a template placeholder such as YOUR_GROQ_API_KEY."""
    return "" if is_placeholder(value) else value


def split_list(value: str) -> tuple[str, ...]:
    """A comma-separated setting as a de-duplicated tuple."""
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
