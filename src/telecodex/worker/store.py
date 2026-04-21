from __future__ import annotations

import json
import base64
from pathlib import Path

from telecodex.shared.models import AdapterExchange, JobRequest, RollingSummary, RunMetadata


class FileRunStore:
    def __init__(self, runs_root: str, run_id: str) -> None:
        self.run_id = run_id
        self.run_dir = Path(runs_root) / run_id
        self.gemini_dir = self.run_dir / "gemini"
        self.codex_dir = self.run_dir / "codex"
        self.reports_dir = self.run_dir / "reports"
        self.attachments_dir = self.run_dir / "attachments"
        for path in [self.run_dir, self.gemini_dir, self.codex_dir, self.reports_dir, self.attachments_dir]:
            path.mkdir(parents=True, exist_ok=True)

    def save_gemini(self, turn: int, exchange: AdapterExchange) -> None:
        self._save_exchange(self.gemini_dir, turn, exchange)

    def save_codex(self, turn: int, exchange: AdapterExchange) -> None:
        self._save_exchange(self.codex_dir, turn, exchange)

    def save_metadata(self, metadata: RunMetadata) -> None:
        self._write_json(self.run_dir / "session.json", metadata.model_dump(mode="json"))

    def save_summary(self, summary: RollingSummary) -> None:
        self._write_json(self.run_dir / "summary.json", summary.model_dump(mode="json"))

    def save_final_report(self, content: str) -> str:
        path = self.reports_dir / "final-report.md"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def save_job_request(self, request: JobRequest) -> None:
        payload = request.model_dump(mode="json")
        self._write_json(self.run_dir / "request.json", payload)
        for index, attachment in enumerate(request.attachments, start=1):
            if not attachment.content_base64:
                continue
            file_path = self.attachments_dir / f"{index:02d}-{attachment.safe_file_name}"
            file_path.write_bytes(base64.b64decode(attachment.content_base64))

    def _save_exchange(self, target_dir: Path, turn: int, exchange: AdapterExchange) -> None:
        (target_dir / f"turn-{turn:02d}-request.json").write_text(exchange.request_json, encoding="utf-8")
        (target_dir / f"turn-{turn:02d}-response.json").write_text(exchange.response_json, encoding="utf-8")
        self._write_json(
            target_dir / f"turn-{turn:02d}-meta.json",
            exchange.execution.model_dump(mode="json"),
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
