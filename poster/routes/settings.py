"""Settings → Credentials & Integrations: the API that reads, saves and tests the integrations' settings.

Every route is for administrators only (auth.require_admin) and answers with masked secrets only. Saving takes
effect with the next request: the factories hand out providers built from the new settings.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from flask import Blueprint, jsonify, request

from poster import validation
from poster.auth import require_admin
from poster.context import run, services
from poster.errors import ServiceError
from poster.retry import RetryLog
from poster.services import Values
from poster.services.media import MediaService
from poster.services.meta import MetaService
from poster.services.settings_service import TYPES, ConfigProvider

bp = Blueprint("settings", __name__, url_prefix="/api/settings")

# URL slugs of the integration types.
SLUGS = {"text-ai": "text_ai", "image-ai": "image_ai", "storage": "storage", "meta": "meta"}


def integration_type(slug: str) -> str:
    provider_type = SLUGS.get(slug)
    if provider_type is None:
        raise ServiceError(f'Unknown integration "{slug}". Use one of: {", ".join(SLUGS)}.', kind="validation", service="Settings")
    return provider_type


def chosen_provider(provider_type: str, body: dict[str, Any]) -> str:
    """The provider the request names, or the active one."""
    name = body.get("provider") or services().settings.active_name(provider_type)
    if not isinstance(name, str):
        raise ServiceError('"provider" must be a provider name.', kind="validation", service="Settings")
    services().settings.provider(provider_type, name)  # refuses unknown names
    return name


def body_or_empty() -> dict[str, Any]:
    return validation.json_body() if request.get_data() else {}


@bp.get("/integrations")
@require_admin
def integrations() -> Any:
    """Every integration with its providers, their settings (secrets masked), status and the active one."""
    s = services()
    return jsonify(s.settings.describe(s.problems()))


@bp.post("/integrations/<slug>")
@require_admin
def save_integration(slug: str) -> Any:
    """Saves a provider's settings and, unless ``"activate": false``, makes it the active provider."""
    provider_type = integration_type(slug)
    body = validation.json_body()
    name = chosen_provider(provider_type, body)
    activate = body.get("activate", True)
    if not isinstance(activate, bool):
        raise ServiceError('"activate" must be true or false.', kind="validation", service="Settings")
    s = services()
    described = s.settings.save(provider_type, name, body.get("config"), activate=activate)
    problems = s.problems()
    described["providers"] = [s.settings.describe_provider(provider_type, p["name"],
                                                           problems[provider_type] if p["active"] else "")
                              for p in described["providers"]]
    return jsonify(integration=described, status=s.settings.health(problems))


@bp.get("/status")
@require_admin
def status() -> Any:
    """Health monitoring: each integration's active provider, whether it is configured, and its last test."""
    s = services()
    return jsonify(services=s.settings.health(s.problems()))


@bp.route("/text-ai/models", methods=["GET", "POST"])
@require_admin
def text_ai_models() -> Any:
    """The models the text AI provider's account can use: the saved settings (GET, ?provider=), or unsaved ones
    (POST with "provider" and "config")."""
    body = validation.json_body() if request.method == "POST" else {"provider": request.args.get("provider")}
    name = chosen_provider("text_ai", body)
    values = candidate_values("text_ai", name, body)
    provider = services().text_ai.build(name, values)
    return run(f"{provider.label} models", lambda retries: {"provider": name, "label": provider.label,
                                                             "models": provider.list_models(retries)})


@bp.post("/test/<slug>")
@require_admin
def test_integration(slug: str) -> Any:
    """Tests a provider's settings: the saved ones, or the unsaved ones the request sends ("config"). The outcome
    goes to the activity log, and to the provider's status when the saved settings were tested."""
    provider_type = integration_type(slug)
    body = body_or_empty()
    name = chosen_provider(provider_type, body)
    values = candidate_values(provider_type, name, body)
    problem = services().settings.provider(provider_type, name).check_values(values)
    if problem:
        raise ServiceError(problem, kind="validation", service="Settings")
    tester = {"text_ai": test_text_ai, "image_ai": test_image_ai, "storage": test_storage, "meta": test_meta}[provider_type]
    label = f"{TYPES[provider_type].label} test"

    def action(retries: RetryLog) -> dict[str, Any]:
        settings = services().settings
        try:
            result = tester(name, values, retries)
        except ServiceError as error:
            settings.record_test(provider_type, name, ok=False, message=error.message, tested=values)
            raise
        ok = result.pop("ok", True)
        settings.record_test(provider_type, name, ok=ok, message=result.get("detail", ""), tested=values)
        return {"ok": ok, **result, "integration": settings.describe_provider(provider_type, name)}

    return run(label, action)


def candidate_values(provider_type: str, name: str, body: dict[str, Any]) -> Values:
    """The settings to use: the provider's current ones, with the request's unsaved "config" (if any) in place."""
    settings = services().settings
    if body.get("config") is None:
        return settings.values(provider_type, name)
    return settings.candidate(provider_type, name, body["config"])[1]


def test_text_ai(name: str, values: Values, retries: RetryLog) -> dict[str, Any]:
    provider = services().text_ai.build(name, values)
    result = provider.test(provider.default_model, retries)
    count = len(result["models"])
    if result["modelAvailable"] is False:
        return {**result, "ok": False, "detail": f'Connected to {result["host"]}, but "{provider.default_model}" is not among '
                                                 f"the account's {count} models."}
    detail = (f"Connected to {result['host']}: {count} models available, including {provider.default_model}." if count
              else f"Connected to {result['host']}. The model list is not available; the first post confirms the model.")
    return {**result, "detail": detail}


def test_image_ai(name: str, values: Values, retries: RetryLog) -> dict[str, Any]:
    return services().image_ai.build(name, values).test(retries)


def config_for(provider_type: str, name: str, values: Values) -> Any:
    s = services()
    cls = s.settings.provider(provider_type, name)
    if not issubclass(cls, ConfigProvider):
        raise ServiceError(f"{cls.label} is not configured through Config.", kind="server", service="Settings")
    return dataclasses.replace(s.settings.effective_config(), **cls.overrides(values, s.config))


def test_storage(name: str, values: Values, retries: RetryLog) -> dict[str, Any]:
    media = MediaService(services().http, config_for("storage", name, values))
    return media.validate(retries)


def test_meta(name: str, values: Values, retries: RetryLog) -> dict[str, Any]:
    """Connects with every Page the token manages, so the owner can choose among them."""
    meta = MetaService(services().http, config_for("meta", name, values))
    meta.require_configured()
    connection = meta.connect(retries, every_page=True)
    channels = connection["channels"]
    pages = [c for c in channels if c["service"] == "facebook"]
    accounts = [c for c in channels if c["service"] == "instagram"]
    selected = sorted(meta.page_ids)
    detail = (f"Connected as {connection['account']['name'] or 'the system user'}: {len(pages)} Facebook Pages and "
              f"{len(accounts)} Instagram accounts.")
    return {"detail": detail, "account": connection["account"], "pages": pages, "instagramAccounts": accounts,
            "selectedPageIds": selected, "tokenId": meta.token_id}
