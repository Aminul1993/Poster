"""Retries, the per-request time budget, and the HTTP client's error classification."""

from __future__ import annotations

from typing import Any

import pytest
import requests

import poster.http
from poster.errors import ServiceError
from poster.http import HttpClient
from poster.retry import RetryLog, browser_timeout, remaining_budget, retry_request, time_budget, worst_case_seconds


def failing(*errors: ServiceError) -> Any:
    """An operation that raises the given errors in turn, then returns "ok"."""
    queue = list(errors)
    calls: list[int] = []

    def operation() -> str:
        calls.append(1)
        if queue:
            raise queue.pop(0)
        return "ok"

    operation.calls = calls  # type: ignore[attr-defined]
    return operation


def timeout() -> ServiceError:
    return ServiceError("slow", kind="timeout", service="Test")


class TestRetryRequest:
    def test_retries_transient_failures_with_backoff(self, waits: list[float]) -> None:
        log = RetryLog("test")
        assert retry_request(failing(timeout(), timeout()), on_retry=log) == "ok"
        assert waits == [1, 2]
        assert [entry["attempt"] for entry in log] == [1, 2] and log[0]["delayMs"] == 1000

    def test_gives_up_after_three_retries(self, waits: list[float]) -> None:
        operation = failing(*(timeout() for _ in range(5)))
        with pytest.raises(ServiceError):
            retry_request(operation)
        assert len(operation.calls) == 4 and waits == [1, 2, 4]

    @pytest.mark.parametrize("error", [ServiceError("bad", kind="validation"), ServiceError("no", kind="auth", status=401),
                                       ServiceError("gone", kind="not_found", status=404)])
    def test_never_retries_permanent_failures(self, error: ServiceError) -> None:
        operation = failing(error)
        with pytest.raises(ServiceError):
            retry_request(operation)
        assert len(operation.calls) == 1

    def test_custom_predicate(self) -> None:
        operation = failing(ServiceError("busy", kind="server", status=500))
        with pytest.raises(ServiceError):
            retry_request(operation, should_retry=lambda error: error.status == 429)
        assert len(operation.calls) == 1

    def test_budget_stops_retries_that_would_overrun_it(self, waits: list[float]) -> None:
        operation = failing(*(timeout() for _ in range(5)))
        # Each retry needs its wait plus MIN_ATTEMPT_SECONDS (5 s): 1 + 5 and 2 + 5 fit in 8 s, 4 + 5 does not.
        with time_budget(8), pytest.raises(ServiceError):
            retry_request(operation)
        assert waits == [1, 2] and len(operation.calls) == 3

    def test_no_budget_by_default(self) -> None:
        assert remaining_budget() is None
        with time_budget(0):
            assert remaining_budget() is None
        with time_budget(30):
            assert 29 < (remaining_budget() or 0) <= 30
        assert remaining_budget() is None


def test_timeouts_for_the_browser() -> None:
    assert worst_case_seconds(120) == 120 * 4 + 7
    assert browser_timeout(989, 0) == 989
    assert browser_timeout(989, 280) == 295
    assert browser_timeout(100, 280) == 100


class TestHttpClient:
    @pytest.fixture
    def seen(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def fake_request(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
            calls.append({"method": method, "url": url, **kwargs})
            response = requests.Response()
            response.status_code = 200
            response._content = b'{"ok": true}'
            return response

        monkeypatch.setattr(requests.Session, "request", fake_request)
        return calls

    def test_timeout_is_capped_by_the_budget(self, seen: list[dict[str, Any]]) -> None:
        with time_budget(30):
            HttpClient().request("GET", "https://x.test/", service="Test", timeout=120)
        assert seen[0]["timeout"] == (10, 30)

    def test_out_of_budget_calls_are_not_made(self, seen: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(poster.http, "remaining_budget", lambda: 0.5)
        with pytest.raises(ServiceError) as caught:
            HttpClient().request("GET", "https://x.test/", service="Test", timeout=120)
        assert caught.value.kind == "timeout" and not seen

    @pytest.mark.parametrize(("exc", "kind"), [(requests.ReadTimeout("t"), "timeout"), (requests.ConnectionError("c"), "network"),
                                               (requests.TooManyRedirects("r"), "network")])
    def test_transport_errors_are_classified(self, monkeypatch: pytest.MonkeyPatch, exc: Exception, kind: str) -> None:
        def boom(*_: Any, **__: Any) -> requests.Response:
            raise exc

        monkeypatch.setattr(requests.Session, "request", boom)
        with pytest.raises(ServiceError) as caught:
            HttpClient().request("GET", "https://x.test/", service="Test", timeout=5)
        assert caught.value.kind == kind


@pytest.mark.parametrize(("status", "kind"), [(401, "auth"), (403, "auth"), (404, "not_found"), (408, "timeout"), (402, "client"),
                                              (413, "client"), (429, "rate_limit"), (500, "server"), (503, "server"), (418, "client")])
def test_http_errors_are_classified(fake: Any, status: int, kind: str) -> None:
    fake.fail("ollama.chat", status, {"error": {"message": "nope"}}, headers={"Retry-After": "7"})
    with pytest.raises(ServiceError) as caught:
        HttpClient().request("POST", "https://ollama.test/api/chat", service="Ollama", timeout=5)
    assert caught.value.kind == kind and caught.value.status == status
    assert caught.value.details == "nope"
    if status == 429:
        assert "Retry after 7s." in caught.value.message
