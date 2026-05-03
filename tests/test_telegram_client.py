from __future__ import annotations

import httpx

from telecodex.gateway.telegram import TelegramClient


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.kwargs: list[dict[str, object]] = []

    def get(self, url: str, *, retryable: bool = False, **kwargs) -> httpx.Response:  # noqa: ANN003
        self.calls.append(("GET", url, retryable))
        self.kwargs.append(kwargs)
        request = httpx.Request("GET", url)
        if url.endswith("/getUpdates"):
            return httpx.Response(200, request=request, json={"ok": True, "result": []})
        return httpx.Response(200, request=request, content=b"image")


class TimeoutHttpClient:
    def get(self, url: str, *, retryable: bool = False, **kwargs) -> httpx.Response:  # noqa: ANN003, ARG002
        request = httpx.Request("GET", url)
        raise httpx.ReadTimeout("timed out", request=request)


def test_download_file_uses_bot_file_url() -> None:
    http = FakeHttpClient()
    client = TelegramClient(token="abc123", timeout_sec=17, http_client=http)
    content = client.download_file("photos/file_0.jpg")

    assert content == b"image"
    assert http.calls[-1] == ("GET", "https://api.telegram.org/file/botabc123/photos/file_0.jpg", True)


def test_poll_messages_uses_timeout_longer_than_long_poll() -> None:
    http = FakeHttpClient()
    client = TelegramClient(token="abc123", timeout_sec=30, http_client=http)

    client.poll_messages(timeout_sec=30)

    assert http.calls[-1] == ("GET", "https://api.telegram.org/botabc123/getUpdates", True)
    assert http.kwargs[-1]["timeout"] == 35


def test_poll_messages_treats_telegram_timeout_as_empty_poll() -> None:
    client = TelegramClient(token="abc123", timeout_sec=30, http_client=TimeoutHttpClient())

    assert client.poll_messages(timeout_sec=30) == []


def test_parse_message_rejects_disallowed_document_mime(monkeypatch) -> None:  # noqa: ANN001
    client = TelegramClient(
        token="abc123",
        timeout_sec=17,
        http_client=FakeHttpClient(),
        allowed_attachment_mime_types=["image/jpeg", "application/pdf"],
    )

    monkeypatch.setattr(
        client,
        "get_file",
        lambda file_id: {"file_path": "docs/archive.zip", "file_size": 1024},  # noqa: ARG005
    )

    message = client._parse_message(
        {
            "chat": {"id": 10, "type": "private"},
            "from": {"id": 1},
            "caption": "/run analyze this file",
            "document": {
                "file_id": "doc-1",
                "file_name": "archive.zip",
                "mime_type": "application/zip",
                "file_size": 1024,
            },
        }
    )

    assert message is not None
    assert message.attachments == []
    assert "application/zip" in message.attachment_errors[0]


def test_parse_message_rejects_oversized_document(monkeypatch) -> None:  # noqa: ANN001
    client = TelegramClient(
        token="abc123",
        timeout_sec=17,
        http_client=FakeHttpClient(),
        max_attachment_bytes=5 * 1024 * 1024,
        allowed_attachment_mime_types=["application/pdf"],
    )

    monkeypatch.setattr(
        client,
        "get_file",
        lambda file_id: {"file_path": "docs/large.pdf", "file_size": 6 * 1024 * 1024},  # noqa: ARG005
    )

    message = client._parse_message(
        {
            "chat": {"id": 10, "type": "private"},
            "from": {"id": 1},
            "caption": "/run analyze this file",
            "document": {
                "file_id": "doc-2",
                "file_name": "large.pdf",
                "mime_type": "application/pdf",
                "file_size": 6 * 1024 * 1024,
            },
        }
    )

    assert message is not None
    assert message.attachments == []
    assert "최대 5.0MB" in message.attachment_errors[0]
