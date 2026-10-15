"""HttpClient: requests with timeouts, the time budget and classified errors."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

from poster.config import APP_VERSION
from poster.errors import ServiceError, extract_error_detail, truncate
from poster.retry import remaining_budget

log = logging.getLogger("poster")


class HttpClient:
    """requests wrapper that turns every failure into a classified ServiceError."""

    def __init__(self) -> None:
        self._local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = f"Poster/{APP_VERSION}"
            self._local.session = session
        return session

    def request(self, method: str, url: str, *, service: str, timeout: int, **kwargs: Any) -> tuple[int, Any, str]:
        """Sends one request. Returns (status, parsed JSON or None, raw text)."""
        response = self._send(method, url, service=service, timeout=timeout, **kwargs)
        return response.status_code, self._parse_json(response), response.text

    def request_bytes(self, method: str, url: str, *, service: str, timeout: int, **kwargs: Any) -> bytes:
        """Sends one request and returns the raw response body (for images)."""
        return self._send(method, url, service=service, timeout=timeout, **kwargs).content

    def _send(self, method: str, url: str, *, service: str, timeout: int, **kwargs: Any) -> requests.Response:
        host = urlparse(url).netloc
        left = remaining_budget()
        if left is not None:
            if left < 1:
                raise ServiceError(f"{service} was not called: this request ran out of time.", kind="timeout",
                                   service=service, hint="Try again in a moment.")
            timeout = max(1, min(timeout, int(left)))
        started = time.monotonic()
        try:
            response = self._session().request(method, url, timeout=(min(10, timeout), timeout), **kwargs)
        except requests.Timeout as exc:
            raise ServiceError(f"{service} did not respond within {timeout} seconds.", kind="timeout", service=service,
                               hint="The service may be busy. Try again, or raise the timeout on the server.") from exc
        except requests.ConnectionError as exc:
            raise ServiceError(f"Network connection failed: could not reach {host}.", kind="network", service=service,
                               hint="Check the server's internet connection and the configured URL.") from exc
        except requests.RequestException as exc:
            raise ServiceError(f"The request to {service} failed ({exc.__class__.__name__}).", kind="network",
                               service=service) from exc
        log.info("%s %s %s -> %d in %.2fs", service, method, host, response.status_code, time.monotonic() - started)
        if not response.ok:
            raise self._http_error(response, self._parse_json(response), response.text, service)
        return response

    @staticmethod
    def _parse_json(response: requests.Response) -> Any:
        try:
            return response.json() if response.text else None
        except ValueError:
            return None

    @staticmethod
    def _http_error(response: requests.Response, data: Any, text: str, service: str) -> ServiceError:
        status = response.status_code
        detail = extract_error_detail(data) or (text.strip() if text and len(text) <= 300 and "<html" not in text.lower() else "")
        said = f' {service} said: "{truncate(detail, 240)}"' if detail else ""
        common: dict[str, Any] = {"status": status, "service": service, "details": detail}
        if status == 401:
            return ServiceError(f"Authentication failed. {service} rejected the API key (HTTP 401).{said}", kind="auth",
                                hint=f"Check the {service} key in the server configuration.", **common)
        if status == 403:
            return ServiceError(f"Access denied (HTTP 403): the {service} credentials lack permission for this action.{said}",
                                kind="auth", hint=f"Check the {service} key and its permissions.", **common)
        if status == 404:
            extra = " Check the endpoint URL and that the model exists." if service == "Ollama" else " Check the configured URL."
            return ServiceError(f"{service} endpoint not found (HTTP 404).{extra}{said}", kind="not_found", **common)
        if status == 408:
            return ServiceError(f"{service} timed out (HTTP 408).", kind="timeout", **common)
        if status == 402:
            return ServiceError(f"{service} requires a paid plan for this request (HTTP 402).{said}", kind="client", **common)
        if status == 413:
            return ServiceError(f"The request was too large for {service} (HTTP 413).{said}", kind="client", **common)
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            wait = f" Retry after {retry_after}s." if retry_after else ""
            return ServiceError(f"{service} rate limit reached (HTTP 429).{wait}{said}", kind="rate_limit",
                                hint="Too many requests. Wait a minute, or lower Parallel requests in Settings.", **common)
        if status >= 500:
            return ServiceError(f"{service} server error (HTTP {status}).{said}", kind="server",
                                hint="This is usually temporary. Try again shortly.", **common)
        return ServiceError(f"{service} rejected the request (HTTP {status}).{said}", kind="client", **common)
