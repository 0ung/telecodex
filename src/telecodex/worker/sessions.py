from __future__ import annotations

import json
from pathlib import Path
from threading import Lock


class ConversationSessionStore:
    def __init__(self, runs_dir: str) -> None:
        self.sessions_dir = Path(runs_dir) / "_sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.sessions_dir / "codex_responses.json"
        self._lock = Lock()

    def get_previous_response_id(self, conversation_key: str) -> str:
        if not conversation_key:
            return ""
        payload = self._load()
        session = payload.get(conversation_key, {})
        previous_response_id = session.get("previous_response_id", "")
        return str(previous_response_id) if previous_response_id else ""

    def save_previous_response_id(self, conversation_key: str, response_id: str) -> None:
        if not conversation_key or not response_id:
            return
        with self._lock:
            payload = self._load()
            payload[conversation_key] = {
                "previous_response_id": response_id,
            }
            self.file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self, conversation_key: str) -> None:
        if not conversation_key:
            return
        with self._lock:
            payload = self._load()
            payload.pop(conversation_key, None)
            self.file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.file_path.exists():
            return {}
        raw = json.loads(self.file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
