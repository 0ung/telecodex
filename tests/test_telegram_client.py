from __future__ import annotations

import httpx

from telecodex.gateway.telegram import TelegramClient


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    def get(self, url: str, *, retryable: bool = False, **kwargs) -> httpx.Response:  # noqa: ANN003
        self.calls.append(("GET", url, retryable))
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, content=b"image")


def test_download_file_uses_bot_file_url() -> None:
    http = FakeHttpClient()
    client = TelegramClient(token="abc123", timeout_sec=17, http_client=http)
    content = client.download_file("photos/file_0.jpg")

    assert content == b"image"
    assert http.calls[-1] == ("GET", "https://api.telegram.org/file/botabc123/photos/file_0.jpg", True)
