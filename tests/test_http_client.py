from __future__ import annotations

import httpx

from telecodex.shared.http_client import HttpTimeoutConfig, ResilientHttpClient


class FlakyClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs):  # noqa: ANN001
        self.calls.append((method, url))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        return None


def _timeout_config() -> HttpTimeoutConfig:
    return HttpTimeoutConfig(connect_sec=1, read_sec=2, write_sec=2, pool_sec=1)


def test_resilient_http_client_retries_timeout_on_get(monkeypatch) -> None:  # noqa: ANN001
    request = httpx.Request("GET", "https://example.com/health")
    client = FlakyClient(
        [
            httpx.ReadTimeout("timed out", request=request),
            httpx.Response(200, request=request, json={"ok": True}),
        ]
    )
    http = ResilientHttpClient(_timeout_config(), max_retries=2, backoff_sec=0.01, client=client)
    monkeypatch.setattr(http, "_sleep", lambda attempt: None)

    response = http.get("https://example.com/health", retryable=True)

    assert response.status_code == 200
    assert len(client.calls) == 2


def test_resilient_http_client_retries_retryable_status(monkeypatch) -> None:  # noqa: ANN001
    request = httpx.Request("GET", "https://example.com/health")
    client = FlakyClient(
        [
            httpx.Response(503, request=request, json={"detail": "busy"}),
            httpx.Response(200, request=request, json={"ok": True}),
        ]
    )
    http = ResilientHttpClient(_timeout_config(), max_retries=2, backoff_sec=0.01, client=client)
    monkeypatch.setattr(http, "_sleep", lambda attempt: None)

    response = http.get("https://example.com/health", retryable=True)

    assert response.status_code == 200
    assert len(client.calls) == 2
