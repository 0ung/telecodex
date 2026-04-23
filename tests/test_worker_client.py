from __future__ import annotations

import httpx

from telecodex.gateway.worker_client import WorkerClient
from telecodex.shared.models import SessionRequest


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def get(self, url: str, *, retryable: bool = False, **kwargs) -> httpx.Response:  # noqa: ANN003
        self.calls.append(("GET", url, retryable))
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, json={"sessions": []})

    def post(self, url: str, *, retryable: bool = False, **kwargs) -> httpx.Response:  # noqa: ANN003
        self.calls.append(("POST", url, retryable))
        request = httpx.Request("POST", url)
        if url.endswith("/sessions"):
            return httpx.Response(
                201,
                request=request,
                json={"session_id": "session-1", "state": "planning", "verdict": "continue"},
            )
        raise AssertionError(f"unexpected POST: {url}")


def test_worker_client_retries_safe_get_requests() -> None:
    http = FakeHttpClient()
    client = WorkerClient("http://worker", worker_token="secret", http_client=http)

    response = client.list_sessions(channel="telegram", conversation_id="10", active_only=True)

    assert response.sessions == []
    assert http.calls[-1] == ("GET", "http://worker/sessions", True)


def test_worker_client_keeps_session_create_non_retryable() -> None:
    http = FakeHttpClient()
    client = WorkerClient("http://worker", worker_token="secret", http_client=http)

    response = client.create_session(
        SessionRequest(
            goal="run",
            requester_id=1,
            workspace_path=".",
            channel="telegram",
            conversation_id="10",
        )
    )

    assert response.session_id == "session-1"
    assert http.calls[-1] == ("POST", "http://worker/sessions", False)
