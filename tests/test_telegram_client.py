from __future__ import annotations

from telecodex.gateway.telegram import TelegramClient


class FakeResponse:
    def __init__(self, content: bytes = b"ok") -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


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
