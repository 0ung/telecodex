from __future__ import annotations

from typing import Any

import httpx


class TelegramClient:
    def __init__(self, token: str, timeout_sec: int = 30) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.timeout_sec = timeout_sec

    def get_updates(self, offset: int | None, timeout_sec: int) -> list[dict[str, Any]]:
        payload = {
            "timeout": timeout_sec,
        }
        if offset is not None:
            payload["offset"] = offset
        response = httpx.get(f"{self.base_url}/getUpdates", params=payload, timeout=timeout_sec + 10)
        response.raise_for_status()
        body = response.json()
        return body.get("result", [])

    def send_message(self, chat_id: int, text: str) -> None:
        response = httpx.post(
            f"{self.base_url}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()

    def get_file(self, file_id: str) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/getFile",
            params={"file_id": file_id},
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        body = response.json()
        return body["result"]

    def download_file(self, file_path: str) -> bytes:
        response = httpx.get(
            f"https://api.telegram.org/file/{self.base_url.split('/bot', 1)[1]}/{file_path}",
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return response.content
