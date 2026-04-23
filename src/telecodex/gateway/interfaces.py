from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from telecodex.shared.models import JobAttachment


@dataclass
class IncomingMessage:
    channel: str
    conversation_id: str
    sender_id: int
    text: str = ""
    is_direct_message: bool = True
    attachments: list[JobAttachment] = field(default_factory=list)
    attachment_errors: list[str] = field(default_factory=list)


class ChatAdapter(Protocol):
    def poll_messages(self, timeout_sec: int) -> list[IncomingMessage]:
        ...

    def send_message(self, conversation_id: str, text: str) -> None:
        ...
