"""SettingsService: the settings of every integration (text AI, image AI, blob storage, Meta), from the database first
and the environment second, with encrypted storage, masking, status, audit logging and the import from the
environment.

Each integration type has providers (TEXT_AI_PROVIDERS, IMAGE_AI_PROVIDERS, STORAGE_PROVIDERS, META_PROVIDERS),
each declaring its fields. A provider's settings are resolved field by field:

1. what was saved in Credentials & Integrations (encrypted in poster_integration_settings),
2. else the environment variable the field names (the variables Poster always used: GROQ_API_KEY, ...),
3. else the field's default.

A row imported from the environment (source "env") lets the environment win until the settings are saved in the
dashboard, so rotating a key in the environment keeps working. A secret left blank when saving keeps the
current one, from wherever it comes.

The settings are read once per process and kept for API["settings"]["cache_seconds"]; saving here clears that
cache, so a change takes effect with the next request, without a restart.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from flask import has_request_context, request

from poster.activity import ActivityLog
from poster.config import API, Config
from poster.crypto import SecretBox, is_masked, mask_secret
from poster.errors import ServiceError, truncate
from poster.services import Field, IntegrationProvider, Values, split_list
from poster.services.ai.factory import TEXT_AI_PROVIDERS
from poster.services.imaging.factory import IMAGE_AI_PROVIDERS
from poster.store import PostStore, Row, iso

log = logging.getLogger("poster")

T = TypeVar("T")
SERVICE = "Settings"

# Row statuses: never tested (since the last change), the last test passed, the last test failed.
UNTESTED, CONNECTED, ERROR = "untested", "connected", "error"


# =============================================================================
# Blob storage and Meta: providers that configure MediaService and MetaService
# =============================================================================

class ConfigProvider(IntegrationProvider):
    """A provider whose settings become Config fields for an existing service (MediaService, MetaService)."""

    @classmethod
    def overrides(cls, values: Values, config: Config) -> dict[str, Any]:
        raise NotImplementedError


class VercelBlobStorage(ConfigProvider):
    name = "vercel_blob"
    label = "Vercel Blob"
    docs_url = "https://vercel.com/docs/vercel-blob"
    fields = (
        Field("access_token", "Access token", "secret", required=True, env="BLOB_READ_WRITE_TOKEN", placeholder="vercel_blob_rw_…",
              help="The public store's read-write token, from the store's page in the Vercel dashboard."),
        Field("store_id", "Store ID (bucket)", env="BLOB_STORE_ID", help="Optional: read from the token when empty."),
        Field("public_url", "Public URL", "url", env="PUBLIC_BASE_URL", placeholder="https://poster.example.com",
              help="This server's public https:// address: Facebook and Instagram fetch images from it. On Vercel, leave "
                   "it empty to use the address Poster is opened at.", sends_secrets=False),
    )

    @classmethod
    def from_env(cls, config: Config) -> Values:
        values = {"access_token": config.blob_read_write_token, "store_id": config.blob_store_id,
                  "public_url": config.public_base_url}
        return {key: value for key, value in values.items() if value}

    @classmethod
    def check_values(cls, values: Values) -> str | None:
        token = values.get("access_token", "")
        if token and not token.startswith("vercel_blob_rw_"):
            return "The access token is not a Vercel Blob read-write token (vercel_blob_rw_...)."
        return super().check_values(values)

    @classmethod
    def overrides(cls, values: Values, config: Config) -> dict[str, Any]:
        return {"blob_read_write_token": values.get("access_token", ""), "blob_store_id": values.get("store_id", ""),
                "public_base_url": values.get("public_url", ""), "media_upload_endpoint": "", "media_upload_preset": ""}


class UploadEndpointStorage(ConfigProvider):
    name = "upload_endpoint"
    label = "Upload endpoint"
    docs_url = "https://cloudinary.com/documentation/upload_images#unsigned_upload"
    fields = (
        Field("endpoint", "Upload URL", "url", required=True, env="MEDIA_UPLOAD_ENDPOINT",
              placeholder="https://api.cloudinary.com/v1_1/<cloud-name>/image/upload",
              help="An endpoint that takes a multipart upload and answers with the image's public URL."),
        Field("preset", "Upload preset (bucket)", env="MEDIA_UPLOAD_PRESET", help="For example a Cloudinary unsigned preset."),
    )

    @classmethod
    def from_env(cls, config: Config) -> Values:
        values = {"endpoint": config.media_upload_endpoint, "preset": config.media_upload_preset}
        return {key: value for key, value in values.items() if value}

    @classmethod
    def overrides(cls, values: Values, config: Config) -> dict[str, Any]:
        return {"media_upload_endpoint": values.get("endpoint", ""), "media_upload_preset": values.get("preset", ""),
                "blob_read_write_token": "", "blob_store_id": ""}


class MetaIntegration(ConfigProvider):
    name = "meta"
    label = "Facebook & Instagram"
    docs_url = "https://business.facebook.com/settings/system-users"
    fields = (
        Field("access_token", "Meta access token", "secret", required=True, env="META_ACCESS_TOKEN", placeholder="EAAB…",
              help="A system user token with pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic, "
                   "instagram_content_publish and business_management."),
        Field("app_secret", "Meta app secret", "secret", env="META_APP_SECRET",
              help="Optional: every Graph API call then carries an appsecret_proof."),
        Field("app_id", "App ID", env="META_APP_ID", help="Optional: the Meta app the token belongs to, for reference."),
        Field("graph_version", "Graph API version", env="META_GRAPH_VERSION", default="v23.0"),
        Field("page_ids", "Pages", "pages", env="META_PAGE_IDS",
              help="The Facebook Pages (and their Instagram accounts) Poster may post to. None chosen allows every Page."),
    )

    @classmethod
    def from_env(cls, config: Config) -> Values:
        values = {"access_token": config.meta_access_token, "app_secret": config.meta_app_secret, "app_id": config.meta_app_id,
                  "graph_version": config.meta_graph_version, "page_ids": ",".join(config.meta_page_ids)}
        return {key: value for key, value in values.items() if value}

    @classmethod
    def check_values(cls, values: Values) -> str | None:
        version = values.get("graph_version", "")
        if version and not re.fullmatch(r"v\d+\.\d+", version):
            return "Graph API version must look like v23.0."
        if not all(re.fullmatch(r"[\w.\-:]{1,100}", page) for page in split_list(values.get("page_ids", ""))):
            return "Page IDs contain only letters, numbers and . - _ : characters."
        return super().check_values(values)

    @classmethod
    def overrides(cls, values: Values, config: Config) -> dict[str, Any]:
        return {"meta_access_token": values.get("access_token", ""), "meta_app_secret": values.get("app_secret", ""),
                "meta_app_id": values.get("app_id", ""), "meta_graph_version": values.get("graph_version") or "v23.0",
                "meta_page_ids": split_list(values.get("page_ids", ""))}


STORAGE_PROVIDERS: dict[str, type[ConfigProvider]] = {"vercel_blob": VercelBlobStorage, "upload_endpoint": UploadEndpointStorage}
META_PROVIDERS: dict[str, type[ConfigProvider]] = {"meta": MetaIntegration}


@dataclass(frozen=True)
class IntegrationType:
    """One kind of integration: its providers, and which one the environment chooses when nothing is saved."""

    key: str
    label: str
    providers: Mapping[str, type[IntegrationProvider]]
    env_default: Callable[[Config], str]
    # Audit wording: "<noun> updated", "<label> provider changed from A to B".
    noun: str


def _env_choice(variable: str, fallback: str) -> Callable[[Config], str]:
    return lambda config: config.env_value(variable).lower() or fallback


def _storage_choice(config: Config) -> str:
    return "upload_endpoint" if config.media_upload_endpoint and not config.blob_read_write_token else "vercel_blob"


TYPES: dict[str, IntegrationType] = {
    "text_ai": IntegrationType("text_ai", "Text AI", TEXT_AI_PROVIDERS, _env_choice("TEXT_AI_PROVIDER", "groq"),
                               "Text AI credentials"),
    "image_ai": IntegrationType("image_ai", "Image AI", IMAGE_AI_PROVIDERS, _env_choice("IMAGE_AI_PROVIDER", "pollinations"),
                                "Image AI settings"),
    "storage": IntegrationType("storage", "Blob storage", STORAGE_PROVIDERS, _storage_choice, "Blob storage configuration"),
    "meta": IntegrationType("meta", "Facebook & Instagram", META_PROVIDERS, lambda _config: "meta", "Meta credentials"),
}


# =============================================================================
# Snapshots of the saved settings
# =============================================================================

@dataclass(frozen=True)
class Saved:
    """A provider's row, decrypted. ``problem`` says why its settings could not be read (they are then ignored)."""

    values: Values
    source: str
    active: bool
    status: str
    last_tested_at: str | None
    last_successful_connection: str | None
    error_message: str | None
    updated_at: str | None
    problem: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """The saved settings at one moment. ``problem`` says why the database could not be read."""

    rows: dict[tuple[str, str], Saved] = field(default_factory=dict)
    problem: str | None = None


def now_iso() -> str:
    return iso(datetime.now(UTC))


class SettingsService:
    """The integrations' settings: resolution, storage, status and audit. Thread-safe."""

    def __init__(self, config: Config, store: PostStore, activity: ActivityLog) -> None:
        self.config = config
        self.store = store
        self.activity = activity
        self.box = SecretBox(config.settings_encryption_key)
        self._lock = threading.RLock()
        self._snapshot: Snapshot | None = None
        self._expires = 0.0
        self._cache: dict[Any, Any] = {}
        self._cache_for: Snapshot | None = None
        self._imported = False

    # -- reading ------------------------------------------------------------------------------------------------------

    @property
    def editable_problem(self) -> str | None:
        """Why settings cannot be saved here, or None."""
        if not self.store.configured:
            return f"Saving credentials needs the database: {self.store.problem}"
        return self.box.problem

    def snapshot(self) -> Snapshot:
        """The saved settings, read again when the cached copy is older than API["settings"]["cache_seconds"]."""
        snapshot = self._snapshot
        if snapshot is not None and time.monotonic() < self._expires:
            return snapshot
        with self._lock:
            if self._snapshot is not None and time.monotonic() < self._expires:
                return self._snapshot
            self._snapshot = self._load(self._snapshot)
            self._expires = time.monotonic() + API["settings"]["cache_seconds"]
            return self._snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._expires = 0.0

    def _load(self, previous: Snapshot | None) -> Snapshot:
        if not self.store.configured:
            return Snapshot(problem=self.store.problem)
        try:
            if not self._imported:
                self._imported = True
                self.import_from_env()
            rows = self.store.settings_rows()
        except ServiceError as error:
            # The environment still works: keep the last settings read, or fall back to the environment alone.
            log.warning("Integration settings could not be read: %s", error.message)
            return previous or Snapshot(problem=error.message)
        return Snapshot(rows={(str(row["provider_type"]), str(row["provider_name"])): self._decrypt(row) for row in rows})

    def _decrypt(self, row: Row) -> Saved:
        values: Values = {}
        problem = None
        if row.get("encrypted_config"):
            try:
                values = {str(k): str(v) for k, v in self.box.decrypt(str(row["encrypted_config"])).items() if v is not None}
            except ServiceError as error:
                problem = error.message
                log.warning("%s (%s %s)", error.message, row["provider_type"], row["provider_name"])
        return Saved(values=values, source=str(row.get("source") or "admin"), active=bool(row.get("is_active")),
                     status=str(row.get("status") or UNTESTED), last_tested_at=row.get("last_tested_at"),
                     last_successful_connection=row.get("last_successful_connection"), error_message=row.get("error_message"),
                     updated_at=row.get("updated_at"), problem=problem)

    def cached(self, key: Any, build: Callable[[], T]) -> T:
        """``build()``'s result, kept until the settings change (a new snapshot)."""
        snapshot = self.snapshot()
        with self._lock:
            if self._cache_for is not snapshot:
                self._cache = {}
                self._cache_for = snapshot
            if key not in self._cache:
                self._cache[key] = build()
            value: T = self._cache[key]
            return value

    @staticmethod
    def integration(provider_type: str) -> IntegrationType:
        kind = TYPES.get(provider_type)
        if kind is None:
            raise ServiceError(f'Unknown integration "{provider_type}".', kind="validation", service=SERVICE)
        return kind

    def provider(self, provider_type: str, name: str) -> type[IntegrationProvider]:
        kind = self.integration(provider_type)
        cls = kind.providers.get(name)
        if cls is None:
            raise ServiceError(f'Unknown {kind.label} provider "{name}". Choose one of: {", ".join(kind.providers)}.',
                               kind="validation", service=SERVICE)
        return cls

    def active_name(self, provider_type: str) -> str:
        """The provider in use: the one activated in the dashboard, else the environment's choice."""
        kind = self.integration(provider_type)
        for (saved_type, name), saved in self.snapshot().rows.items():
            if saved_type == provider_type and saved.active and name in kind.providers:
                return name
        chosen = kind.env_default(self.config)
        return chosen if chosen in kind.providers else next(iter(kind.providers))

    def values(self, provider_type: str, name: str) -> Values:
        """A provider's settings: saved, else the environment's, else the defaults (see the module docstring)."""
        return self._resolve(provider_type, name)[0]

    def _resolve(self, provider_type: str, name: str) -> tuple[Values, dict[str, str]]:
        """The settings and where each came from: saved, env or default ("" when unset)."""
        cls = self.provider(provider_type, name)
        env = cls.from_env(self.config)
        saved = self.snapshot().rows.get((provider_type, name))
        stored = saved.values if saved is not None else {}
        env_first = saved is not None and saved.source == "env"
        values: Values = {}
        origins: dict[str, str] = {}
        for f in cls.fields:
            layers = [("env", env.get(f.key, "")), ("saved", stored.get(f.key, ""))]
            for origin, value in (layers if env_first else layers[::-1]) + [("default", f.default)]:
                if value:
                    values[f.key], origins[f.key] = value, origin
                    break
            else:
                values[f.key], origins[f.key] = "", ""
        return values, origins

    def effective_config(self) -> Config:
        """The environment's Config with the active storage and Meta settings in place, for MediaService and
        MetaService (which take a Config)."""
        def build() -> Config:
            overrides: dict[str, Any] = {}
            for provider_type in ("storage", "meta"):
                name = self.active_name(provider_type)
                cls = self.provider(provider_type, name)
                if issubclass(cls, ConfigProvider):
                    overrides.update(cls.overrides(self.values(provider_type, name), self.config))
            return dataclasses.replace(self.config, **overrides)

        return self.cached(("config",), build)

    # -- describing (masked) ------------------------------------------------------------------------------------------

    def describe(self, problems: Mapping[str, str | None] | None = None) -> dict[str, Any]:
        """Every integration, its providers and their settings, for the dashboard. Secrets only ever masked.
        ``problems`` are the active providers' own configuration problems, by type."""
        snapshot = self.snapshot()
        return {
            "editable": self.editable_problem is None,
            "editableProblem": self.editable_problem,
            "encryption": {"configured": self.box.configured, "problem": self.box.problem},
            "database": {"configured": self.store.configured and snapshot.problem is None,
                         "problem": snapshot.problem or self.store.problem},
            "integrations": {key: self.describe_type(key, (problems or {}).get(key, "")) for key in TYPES},
        }

    def describe_type(self, provider_type: str, active_problem: str | None = "") -> dict[str, Any]:
        kind = self.integration(provider_type)
        active = self.active_name(provider_type)
        saved_active = any(t == provider_type and s.active for (t, _), s in self.snapshot().rows.items())
        return {"type": provider_type, "label": kind.label, "active": active,
                "activeSource": "database" if saved_active else "environment",
                "providers": [self.describe_provider(provider_type, name, active_problem if name == active else "")
                              for name in kind.providers]}

    def describe_provider(self, provider_type: str, name: str, live_problem: str | None = "") -> dict[str, Any]:
        cls = self.provider(provider_type, name)
        values, origins = self._resolve(provider_type, name)
        saved = self.snapshot().rows.get((provider_type, name))
        problem = cls.check_values(values) if live_problem == "" else live_problem
        fields = []
        for f in cls.fields:
            entry = {**f.public(), "origin": origins[f.key]}
            if f.secret:
                entry.update(value="", masked=mask_secret(values[f.key]), hasValue=bool(values[f.key]))
            else:
                entry.update(value=values[f.key], hasValue=bool(values[f.key]))
            fields.append(entry)
        return {
            **cls.describe(), "fields": fields, "active": name == self.active_name(provider_type),
            "configured": problem is None, "problem": (saved.problem if saved and saved.problem else problem),
            "source": saved.source if saved is not None and (saved.values or saved.source == "admin") else None,
            "status": saved.status if saved is not None else UNTESTED,
            "lastTestedAt": saved.last_tested_at if saved else None,
            "lastSuccessfulConnection": saved.last_successful_connection if saved else None,
            "error": saved.error_message if saved else None, "updatedAt": saved.updated_at if saved else None,
        }

    def health(self, problems: Mapping[str, str | None]) -> list[dict[str, Any]]:
        """One line per integration: its active provider, whether it is configured, and its last test."""
        lines = []
        for key, kind in TYPES.items():
            name = self.active_name(key)
            saved = self.snapshot().rows.get((key, name))
            problem = problems.get(key)
            status = "not_configured" if problem else (saved.status if saved is not None else UNTESTED)
            lines.append({"type": key, "service": kind.label, "provider": name, "providerLabel": kind.providers[name].label,
                          "configured": problem is None, "problem": problem, "status": status,
                          "lastTestedAt": saved.last_tested_at if saved else None,
                          "lastSuccessfulConnection": saved.last_successful_connection if saved else None,
                          "error": saved.error_message if saved else None})
        return lines

    # -- saving -------------------------------------------------------------------------------------------------------

    def candidate(self, provider_type: str, name: str, submitted: Any) -> tuple[Values, Values]:
        """What saving ``submitted`` would store, and the settings that would then apply.

        Unknown keys are refused; a secret left blank, or sent back masked, keeps the current one: the saved
        secret, or the environment's (which is then not copied into the database, so it keeps following the
        environment). A kept secret never goes to a new address: changing an address the secrets are sent to
        (Field.sends_secrets) needs them entered again, so the settings cannot redirect a stored key elsewhere."""
        cls = self.provider(provider_type, name)
        if submitted is None:
            submitted = {}
        if not isinstance(submitted, dict):
            raise ServiceError('"config" must be an object of settings.', kind="validation", service=SERVICE)
        known = {f.key: f for f in cls.fields}
        unknown = sorted(set(submitted) - set(known))
        if unknown:
            raise ServiceError(f"Unknown settings for {cls.label}: {', '.join(unknown)}.", kind="validation", service=SERVICE)
        saved = self.snapshot().rows.get((provider_type, name))
        saved_values = saved.values if saved is not None and saved.source == "admin" else {}
        stored: Values = {}
        kept: list[Field] = []
        for key, f in known.items():
            value = self._clean(f, submitted.get(key)) if key in submitted else None
            # Not sent, or a secret left blank or sent back masked: the saved value stays.
            if value is None or (f.secret and (not value or is_masked(value))):
                value = saved_values.get(key, "")
                if f.secret:
                    kept.append(f)
            if value:
                stored[key] = value
        env = cls.from_env(self.config)
        effective = {f.key: stored.get(f.key) or env.get(f.key) or f.default for f in cls.fields}
        current = self.values(provider_type, name)
        moved = [f for f in cls.fields if f.kind == "url" and f.sends_secrets and effective[f.key] != current[f.key]]
        kept = [f for f in kept if effective[f.key]]
        if moved and kept:
            raise ServiceError(f"Enter the {kept[0].label} again to use it with a different {moved[0].label}: a saved key "
                               "is only sent to the address it was saved for.", kind="validation", service=SERVICE)
        return stored, effective

    @staticmethod
    def _clean(f: Field, value: Any) -> str:
        if isinstance(value, list) and f.kind in ("list", "pages"):
            value = ",".join(str(item) for item in value)
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ServiceError(f"{f.label} must be text.", kind="validation", service=SERVICE)
        value = value.strip()
        if len(value) > API["settings"]["max_value_chars"]:
            raise ServiceError(f"{f.label} is too long.", kind="validation", service=SERVICE)
        if f.kind in ("list", "pages"):
            value = ",".join(split_list(value))
        return str(value)

    def save(self, provider_type: str, name: str, submitted: Any, *, activate: bool = True) -> dict[str, Any]:
        """Saves a provider's settings (encrypted) and, with ``activate``, makes it the active one. Both take effect
        with the next request. Every change is written to the activity log, without any value."""
        kind = self.integration(provider_type)
        cls = self.provider(provider_type, name)
        if self.editable_problem:
            raise ServiceError(self.editable_problem, kind="config", service=SERVICE,
                               hint="Set SETTINGS_ENCRYPTION_KEY (and DATABASE_URL on Vercel), then reload this page.")
        before, _ = self._resolve(provider_type, name)
        previous_active = self.active_name(provider_type)
        stored, effective = self.candidate(provider_type, name, submitted)
        problem = cls.check_values(effective)
        if problem:
            raise ServiceError(problem, kind="validation", service=SERVICE)
        changed = [f.label for f in cls.fields if before.get(f.key, "") != effective.get(f.key, "")]
        saved = self.snapshot().rows.get((provider_type, name))
        values_changed = bool(changed) or saved is None or saved.source != "admin" or saved.values != stored
        stamp = now_iso()
        if values_changed:
            row: Row = {"id": uuid.uuid4().hex, "provider_type": provider_type, "provider_name": name,
                        "encrypted_config": self.box.encrypt(stored), "source": "admin", "is_active": 0,
                        "status": UNTESTED, "error_message": None, "created_at": stamp, "updated_at": stamp}
            if not changed and saved is not None:
                row.update(status=saved.status, error_message=saved.error_message)
            self.store.save_setting(row)
        if activate:
            self.store.activate_setting(provider_type, name)
        self.invalidate()
        if changed or (values_changed and saved is None):
            self.audit("info", f"{kind.noun} updated ({cls.label}).", f"Changed: {', '.join(changed) or 'saved as shown'}.")
        if activate and previous_active != name:
            old = kind.providers[previous_active].label if previous_active in kind.providers else previous_active
            self.audit("info", f"{kind.label} provider changed from {old} to {cls.label}.")
        return self.describe_type(provider_type)

    def record_test(self, provider_type: str, name: str, *, ok: bool, message: str, tested: Values | None = None) -> None:
        """Writes a test's outcome to the activity log and, when the tested settings are the provider's current
        ones, to its status (last tested, last success, error)."""
        kind = self.integration(provider_type)
        cls = self.provider(provider_type, name)
        verb = {"storage": "Storage validation performed", "meta": "Meta connection tested"}.get(
            provider_type, f"{kind.label} provider connection tested")
        self.audit("success" if ok else "error", f"{verb} ({cls.label}): {'connected' if ok else 'failed'}.",
                   None if ok else message)
        if tested is not None and tested != self.values(provider_type, name):
            return  # unsaved settings: the stored status stays about the saved ones
        if not self.store.configured:
            return
        stamp = now_iso()
        fields: dict[str, Any] = {"status": CONNECTED if ok else ERROR, "last_tested_at": stamp,
                                  "error_message": None if ok else truncate(message, 500)}
        if ok:
            fields["last_successful_connection"] = stamp
        try:
            if (provider_type, name) not in self.snapshot().rows:
                # A provider configured by the environment alone gets a row for its status (no settings in it).
                self.store.add_setting({"id": uuid.uuid4().hex, "provider_type": provider_type, "provider_name": name,
                                        "encrypted_config": "", "source": "env", "is_active": 0, "created_at": stamp,
                                        "updated_at": stamp})
            self.store.update_setting(provider_type, name, **fields)
        except ServiceError as error:
            log.warning("Could not record the %s test: %s", kind.label, error.message)
        self.invalidate()

    def audit(self, level: str, message: str, details: str | None = None) -> None:
        """An activity log entry (category "settings") naming what changed, never a value."""
        who = f"From {request.remote_addr}." if has_request_context() and request.remote_addr else ""
        details = " ".join(part for part in (details, who) if part) or None
        log.info("Settings: %s %s", message, details or "")
        self.activity.record(level, "settings", message, details)

    # -- the import from the environment ------------------------------------------------------------------------------

    def import_from_env(self) -> list[str]:
        """On first use, creates a row (source "env", active) for each integration the environment configures
        and the database does not have yet: GROQ_API_KEY makes Groq the text AI provider, POLLINATIONS_API_KEY
        makes Pollinations the image provider, and so on. Needs SETTINGS_ENCRYPTION_KEY; without it, the
        environment simply keeps being used. Returns what was imported."""
        if not self.box.configured or not self.store.configured:
            return []
        imported = []
        existing = {str(row["provider_type"]) for row in self.store.settings_rows()}
        for key, kind in TYPES.items():
            if key in existing:
                continue
            name = kind.env_default(self.config)
            cls = kind.providers.get(name)
            if cls is None:
                continue
            env = cls.from_env(self.config)
            if not env or cls.check_values({**cls.defaults(), **env}):
                continue  # the environment does not configure it
            stamp = now_iso()
            added = self.store.add_setting({"id": uuid.uuid4().hex, "provider_type": key, "provider_name": name,
                                            "encrypted_config": self.box.encrypt(env), "source": "env", "is_active": 1,
                                            "status": UNTESTED, "created_at": stamp, "updated_at": stamp})
            if added:
                imported.append(f"{kind.label}: {cls.label}")
                named = "" if cls.label == kind.label else f" ({cls.label})"
                self.audit("info", f"{kind.label} settings imported from the environment{named}.",
                           "The environment variables stay in charge until the settings are saved in Credentials & "
                           "Integrations.")
        return imported

    def rotate_key(self) -> int:
        """Encrypts every saved setting again under the first SETTINGS_ENCRYPTION_KEY. Returns how many rows."""
        count = 0
        for row in self.store.settings_rows():
            if row.get("encrypted_config"):
                self.store.update_setting(str(row["provider_type"]), str(row["provider_name"]),
                                          encrypted_config=self.box.rotate(str(row["encrypted_config"])), updated_at=now_iso())
                count += 1
        self.invalidate()
        return count
