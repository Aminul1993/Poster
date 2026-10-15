"""HTTP Basic authentication for the dashboard and its API.

Browsers ask for the password once and then send it with every same-origin
request, so the dashboard needs no changes. Without POSTER_PASSWORD, only
requests to a local address are served: a deployment never runs open, and a
DNS-rebinding page (whose requests carry its own host name) is refused.
"""

from __future__ import annotations

import hmac
from typing import Any
from urllib.parse import urlparse

from flask import Response, jsonify, request

from poster.config import Config, is_local_host

# Buffer fetches post images without credentials; health checks and static assets carry none either.
PUBLIC_PATHS = ("/api/health", "/api/media/image/", "/assets/")
CHALLENGE = 'Basic realm="Poster", charset="UTF-8"'


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
