"""Clients for the upstream services: Ollama, Buffer, the media host and Pollinations."""

from __future__ import annotations

from typing import ClassVar

from poster.errors import ServiceError


class ConfiguredService:
    """Base for a client that may be unconfigured: ``problem`` says why it cannot be used, or is None."""

    service: ClassVar[str] = ""
    # Start of the error shown when the service is used without being configured.
    unconfigured: ClassVar[str] = ""
    setup_hint: ClassVar[str] = "Set it in the environment or .env file and restart Poster."
    problem: str | None = None

    @property
    def configured(self) -> bool:
        return self.problem is None

    def require_configured(self) -> None:
        if self.problem:
            raise ServiceError(f"{self.unconfigured}: {self.problem}", kind="config", service=self.service,
                               hint=self.setup_hint)
