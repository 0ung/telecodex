from __future__ import annotations

import base64
from typing import Any

import httpx

from telecodex.gateway.interfaces import IncomingMessage
from telecodex.shared.models import JobAttachment, infer_mime_type


class TelegramAdapter:
    def __init__(self, token: str, timeout_sec: int = 30) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{token}"
        self.timeout_sec = timeout_sec
        self._offset: int | None = None

    def poll_messages(self, timeout_sec: int) -> list[IncomingMessage]:
        updates = self._get_updates(offset=self._offset, timeout_sec=timeout_sec)
        messages: list[IncomingMessage] = []
        for update in updates:
            self._offset = update["update_id"] + 1
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            parsed = self._parse_message(message)
            if parsed is not None:
                messages.append(parsed)
        return messages

    def _get_updates(self, offset: int | None, timeout_sec: int) -> list[dict[str, Any]]:
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
            f"{self.file_base_url}/{file_path}",
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return response.content

    def _parse_message(self, message: dict[str, Any]) -> IncomingMessage | None:
        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id = chat.get("id")
        user_id = user.get("id")
        if chat_id is None or user_id is None:
            return None
        text = ((message.get("text") or "") or (message.get("caption") or "")).strip()
        return IncomingMessage(
            channel="telegram",
            conversation_id=str(chat_id),
            sender_id=int(user_id),
            text=text,
            is_direct_message=chat.get("type") == "private",
            attachments=self._extract_attachments(message),
        )

    def _extract_attachments(self, message: dict[str, Any]) -> list[JobAttachment]:
        attachments: list[JobAttachment] = []
        photo_sizes = message.get("photo") or []
        if photo_sizes:
            selected = photo_sizes[-1]
            attachments.append(
                self._download_attachment(
                    file_id=selected["file_id"],
                    file_unique_id=selected.get("file_unique_id", ""),
                    fallback_name=f"telegram-photo-{selected['file_id']}.jpg",
                    kind="photo",
                )
            )
        document = message.get("document")
        if isinstance(document, dict):
            attachments.append(
                self._download_attachment(
                    file_id=document["file_id"],
                    file_unique_id=document.get("file_unique_id", ""),
                    fallback_name=document.get("file_name", f"telegram-document-{document['file_id']}"),
                    kind="document",
                )
            )
        return attachments

    def _download_attachment(self, file_id: str, file_unique_id: str, fallback_name: str, kind: str) -> JobAttachment:
        file_meta = self.get_file(file_id)
        file_path = file_meta["file_path"]
        content = self.download_file(file_path)
        return JobAttachment(
            kind=kind,
            file_name=fallback_name,
            mime_type=infer_mime_type(file_path),
            telegram_file_id=file_id,
            telegram_file_unique_id=file_unique_id,
            telegram_file_path=file_path,
            content_base64=base64.b64encode(content).decode("ascii"),
        )


TelegramClient = TelegramAdapter
