from __future__ import annotations

from telecodex.gateway.telegram import TelegramClient


class FakeResponse:
    def __init__(self, content: bytes = b"ok", json_body: dict | None = None) -> None:
        self.content = content
        self._json_body = json_body or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._json_body


def test_download_file_uses_bot_file_url(monkeypatch) -> None:  # noqa: ANN001
    captured: dict[str, str] = {}

    def fake_get(url: str, timeout: int):  # noqa: ANN001
        captured["url"] = url
        captured["timeout"] = str(timeout)
        return FakeResponse(content=b"image")

    monkeypatch.setattr("telecodex.gateway.telegram.httpx.get", fake_get)

    client = TelegramClient(token="abc123", timeout_sec=17)
    content = client.download_file("photos/file_0.jpg")

    assert content == b"image"
    assert captured["url"] == "https://api.telegram.org/file/botabc123/photos/file_0.jpg"
    assert captured["timeout"] == "17"


def test_parse_message_rejects_disallowed_document_mime(monkeypatch) -> None:  # noqa: ANN001
    def fake_get(url: str, params=None, timeout: int = 30):  # noqa: ANN001
        if url.endswith("/getFile"):
            return FakeResponse(
                json_body={
                    "result": {
                        "file_path": "docs/archive.zip",
                        "file_size": 1024,
                    }
                }
            )
        raise AssertionError(f"unexpected download for url={url}")

    monkeypatch.setattr("telecodex.gateway.telegram.httpx.get", fake_get)

    client = TelegramClient(
        token="abc123",
        timeout_sec=17,
        allowed_attachment_mime_types=["image/jpeg", "application/pdf"],
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
    def fake_get(url: str, params=None, timeout: int = 30):  # noqa: ANN001
        if url.endswith("/getFile"):
            return FakeResponse(
                json_body={
                    "result": {
                        "file_path": "docs/large.pdf",
                        "file_size": 6 * 1024 * 1024,
                    }
                }
            )
        raise AssertionError(f"unexpected download for url={url}")

    monkeypatch.setattr("telecodex.gateway.telegram.httpx.get", fake_get)

    client = TelegramClient(
        token="abc123",
        timeout_sec=17,
        max_attachment_bytes=5 * 1024 * 1024,
        allowed_attachment_mime_types=["application/pdf"],
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
