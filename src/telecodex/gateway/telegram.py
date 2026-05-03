from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import httpx

from telecodex.gateway.interfaces import IncomingMessage
from telecodex.shared.http_client import ResilientHttpClient
from telecodex.shared.models import JobAttachment, infer_mime_type


@dataclass
class _AttachmentCandidate:
    file_id: str
    file_unique_id: str
    file_name: str
    kind: str
    mime_type: str
    file_path: str
    size_bytes: int


class TelegramAdapter:
    def __init__(
        self,
        token: str,
        timeout_sec: int = 30,
        http_client: ResilientHttpClient | None = None,
        max_attachment_bytes: int = 5 * 1024 * 1024,
        max_total_attachment_bytes: int = 10 * 1024 * 1024,
        allowed_attachment_mime_types: list[str] | None = None,
    ) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{token}"
        self.timeout_sec = timeout_sec
        self.http = http_client
        self._offset: int | None = None
        self.max_attachment_bytes = max_attachment_bytes
        self.max_total_attachment_bytes = max_total_attachment_bytes
        self.allowed_attachment_mime_types = set(allowed_attachment_mime_types or [])

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
        try:
            response = self._http().get(
                f"{self.base_url}/getUpdates",
                params=payload,
                retryable=True,
                timeout=max(self.timeout_sec, timeout_sec) + 5,
            )
        except httpx.TimeoutException:
            return []
        response.raise_for_status()
        body = response.json()
        return body.get("result", [])

    def send_message(self, chat_id: int, text: str) -> None:
        response = self._http().post(
            f"{self.base_url}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        response.raise_for_status()

    def get_file(self, file_id: str) -> dict[str, Any]:
        response = self._http().get(
            f"{self.base_url}/getFile",
            params={"file_id": file_id},
            retryable=True,
        )
        response.raise_for_status()
        body = response.json()
        return body["result"]

    def download_file(self, file_path: str) -> bytes:
        response = self._http().get(
            f"{self.file_base_url}/{file_path}",
            retryable=True,
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
        attachments, attachment_errors = self._extract_attachments(message)
        return IncomingMessage(
            channel="telegram",
            conversation_id=str(chat_id),
            sender_id=int(user_id),
            text=text,
            is_direct_message=chat.get("type") == "private",
            attachments=attachments,
            attachment_errors=attachment_errors,
        )

    def _extract_attachments(self, message: dict[str, Any]) -> tuple[list[JobAttachment], list[str]]:
        candidates: list[_AttachmentCandidate] = []
        errors: list[str] = []
        photo_sizes = message.get("photo") or []
        if photo_sizes:
            selected = photo_sizes[-1]
            file_id = selected["file_id"]
            fallback_name = f"telegram-photo-{file_id}.jpg"
            file_meta = self.get_file(file_id)
            size_bytes = int(file_meta.get("file_size") or selected.get("file_size") or 0)
            file_path = str(file_meta["file_path"])
            mime_type = infer_mime_type(file_path, fallback="image/jpeg")
            errors.extend(self._validate_attachment(fallback_name, mime_type, size_bytes))
            candidates.append(
                _AttachmentCandidate(
                    file_id=file_id,
                    file_unique_id=selected.get("file_unique_id", ""),
                    file_name=fallback_name,
                    kind="photo",
                    mime_type=mime_type,
                    file_path=file_path,
                    size_bytes=size_bytes,
                )
            )
        document = message.get("document")
        if isinstance(document, dict):
            file_id = document["file_id"]
            fallback_name = document.get("file_name", f"telegram-document-{file_id}")
            file_meta = self.get_file(file_id)
            file_path = str(file_meta["file_path"])
            mime_type = str(document.get("mime_type") or infer_mime_type(file_path))
            size_bytes = int(file_meta.get("file_size") or document.get("file_size") or 0)
            errors.extend(self._validate_attachment(fallback_name, mime_type, size_bytes))
            candidates.append(
                _AttachmentCandidate(
                    file_id=file_id,
                    file_unique_id=document.get("file_unique_id", ""),
                    file_name=fallback_name,
                    kind="document",
                    mime_type=mime_type,
                    file_path=file_path,
                    size_bytes=size_bytes,
                )
            )
        total_size = sum(item.size_bytes for item in candidates)
        if total_size > self.max_total_attachment_bytes:
            errors.append(
                f"첨부 총 용량이 너무 큽니다. 최대 {self._format_size(self.max_total_attachment_bytes)}까지 보낼 수 있습니다."
            )
        if errors:
            return [], errors
        attachments = [self._download_attachment(item) for item in candidates]
        return attachments, []

    def _download_attachment(self, candidate: _AttachmentCandidate) -> JobAttachment:
        content = self.download_file(candidate.file_path)
        return JobAttachment(
            kind=candidate.kind,
            file_name=candidate.file_name,
            mime_type=candidate.mime_type,
            size_bytes=candidate.size_bytes or len(content),
            telegram_file_id=candidate.file_id,
            telegram_file_unique_id=candidate.file_unique_id,
            telegram_file_path=candidate.file_path,
            content_base64=base64.b64encode(content).decode("ascii"),
        )

    def _validate_attachment(self, file_name: str, mime_type: str, size_bytes: int) -> list[str]:
        errors: list[str] = []
        if mime_type not in self.allowed_attachment_mime_types:
            errors.append(f"`{file_name}` 파일 형식 `{mime_type}` 은(는) 아직 지원하지 않습니다.")
        if size_bytes > self.max_attachment_bytes:
            errors.append(
                f"`{file_name}` 파일이 너무 큽니다. 파일당 최대 {self._format_size(self.max_attachment_bytes)}까지 보낼 수 있습니다."
            )
        return errors

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes >= 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f}MB"
        if size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f}KB"
        return f"{size_bytes}B"

    def _http(self) -> ResilientHttpClient:
        if self.http is None:
            raise RuntimeError("telegram adapter requires an http client")
        return self.http


TelegramClient = TelegramAdapter
