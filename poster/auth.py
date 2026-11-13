"""HTTP Basic authentication for the dashboard and its API, and the administrator check for Credentials &
Integrations.

Browsers ask for the password once and then send it with every same-origin
request, so the dashboard needs no changes. Without POSTER_PASSWORD, only
requests to a local address are served: a deployment never runs open, and a
DNS-rebinding page (whose requests carry its own host name) is refused.

Whoever may use the dashboard administers it, unless POSTER_ADMIN_PASSWORD is set: then reading or changing
credentials, switching providers and testing integrations also need that password, sent by the
Credentials & Integrations drawer in the X-Poster-Admin header (it is kept in the page's memory only).
"""

from __future__ import annotations

import functools
import hmac
from collections.abc import Callable
from typing import Any, TypeVar, cast
from urllib.parse import urlparse

from flask import Response, current_app, jsonify, request

from poster.config import Config, is_local_host

# Facebook and Instagram fetch post images without credentials; health checks and static assets carry none
# either. The publisher's scheduler sends CRON_SECRET instead, which the route checks (see web.publish_due).
PUBLIC_PATHS = ("/api/health", "/api/media/image/", "/assets/", "/api/publish/due")
CHALLENGE = 'Basic realm="Poster", charset="UTF-8"'
ADMIN_HEADER = "X-Poster-Admin"

F = TypeVar("F", bound=Callable[..., Any])


def require_login(config: Config) -> Any:
    """None when the request may go ahead, else the response that refuses it."""
    if request.path.startswith(PUBLIC_PATHS):
        return None
    if not config.password:
        if is_local_host(urlparse(request.host_url).hostname or ""):
            return None
        return _refuse(503, "Poster is reachable on a public address but has no password. Set POSTER_PASSWORD on the "
                            "server, then restart or redeploy it.", "config")
    auth = request.authorization
    supplied = (auth.password or "") if auth is not None and auth.type == "basic" else ""
    if hmac.compare_digest(supplied.encode(), config.password.encode()):
        return None
    response = _refuse(401, "Sign in to use Poster.", "auth")
    response.headers["WWW-Authenticate"] = CHALLENGE
    return response


def _refuse(status: int, message: str, kind: str) -> Response:
    if request.path.startswith("/api/"):
        response = jsonify(error={"message": message, "kind": kind})
    else:
        response = Response(message, mimetype="text/plain")
    response.status_code = status
    return response


def admin_refusal(config: Config) -> Any:
    """None when the request comes from an administrator, else the response that refuses it.

    Checked on every Credentials & Integrations route, on top of require_login (which the app runs before
    every request): the dashboard's login always, then POSTER_ADMIN_PASSWORD when it is set."""
    denied = require_login(config)
    if denied is not None:
        return denied
    if not config.admin_password:
        return None
    supplied = request.headers.get(ADMIN_HEADER, "")
    if supplied and hmac.compare_digest(supplied.encode(), config.admin_password.encode()):
        return None
    response = jsonify(error={"message": "Enter the administrator password to manage credentials and integrations."
                              if not supplied else "The administrator password is not correct.",
                              "kind": "auth", "service": "Settings", "admin": True})
    response.status_code = 403
    return response


def require_admin(view: F) -> F:
    """Refuses the route to anyone but an administrator (see admin_refusal)."""

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        denied = admin_refusal(current_app.extensions["poster"].config)
        if denied is not None:
            return denied
        return view(*args, **kwargs)

    return cast(F, wrapper)
