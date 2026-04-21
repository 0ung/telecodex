from __future__ import annotations

import base64
from dataclasses import dataclass

from telecodex.gateway.telegram import TelegramClient
from telecodex.gateway.worker_client import WorkerClient
from telecodex.shared.config import GatewayConfig
from telecodex.shared.models import JobAttachment, JobDetail, JobRequest, infer_mime_type


@dataclass
class GatewayService:
    cfg: GatewayConfig
    telegram: TelegramClient
    worker: WorkerClient

    def poll_forever(self) -> None:
        offset: int | None = None
        while True:
            updates = self.telegram.get_updates(offset=offset, timeout_sec=self.cfg.poll_timeout_sec)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue
                self._handle_message(message)

    def _handle_message(self, message: dict) -> None:
        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id = chat.get("id")
        user_id = user.get("id")
        if chat.get("type") != "private" or user_id not in self.cfg.allowed_user_ids:
            return

        text = (message.get("text") or "").strip()
        caption = (message.get("caption") or "").strip()
        attachments = self._extract_attachments(message)
        if not text and not caption and not attachments:
            self.telegram.send_message(chat_id, "Send text, a photo, or both.")
            return

        if text.startswith("/run "):
            self._run_command(chat_id, user_id, text[5:].strip())
            return
        if text == "/status":
            self._status_command(chat_id)
            return
        if text == "/runs":
            self._runs_command(chat_id)
            return
        if text.startswith("/show "):
            self._show_command(chat_id, text[6:].strip())
            return
        if text.startswith("/stop "):
            self._stop_command(chat_id, text[6:].strip())
            return
        if text in {"/help", "/start"}:
            self.telegram.send_message(chat_id, self._help_text())
            return
        goal = text or caption
        self._run_command(chat_id, user_id, goal, attachments=attachments)

    def _run_command(self, chat_id: int, user_id: int, goal: str, attachments: list[JobAttachment] | None = None) -> None:
        attachments = attachments or []
        if not goal and not attachments:
            self.telegram.send_message(chat_id, "Usage: /run <goal> or send a photo with a caption.")
            return
        try:
            response = self.worker.create_job(
                JobRequest(
                    goal=goal or "Analyze the attached image input.",
                    requester_id=user_id,
                    workspace_path=".",
                    text_only=not attachments,
                    requires_private_network=True,
                    attachments=attachments,
                )
            )
            attachment_suffix = f" with {len(attachments)} attachment(s)" if attachments else ""
            self.telegram.send_message(chat_id, f"Job `{response.job_id}` started with state `{response.state.value}`{attachment_suffix}.")
        except Exception as exc:  # noqa: BLE001
            self.telegram.send_message(chat_id, f"Failed to start job: {exc}")

    def _status_command(self, chat_id: int) -> None:
        try:
            jobs = self.worker.list_jobs().jobs
            if not jobs:
                self.telegram.send_message(chat_id, "No runs yet.")
                return
            current = jobs[0]
            self.telegram.send_message(
                chat_id,
                f"Latest job `{current.job_id}` is `{current.state.value}`.\nSummary: {current.final_summary or current.goal}",
            )
        except Exception as exc:  # noqa: BLE001
            self.telegram.send_message(chat_id, f"Failed to fetch status: {exc}")

    def _runs_command(self, chat_id: int) -> None:
        try:
            jobs = self.worker.list_jobs().jobs[:5]
            if not jobs:
                self.telegram.send_message(chat_id, "No runs yet.")
                return
            lines = [f"`{item.job_id}` - {item.state.value} - {item.goal}" for item in jobs]
            self.telegram.send_message(chat_id, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self.telegram.send_message(chat_id, f"Failed to list runs: {exc}")

    def _show_command(self, chat_id: int, job_id: str) -> None:
        if not job_id:
            self.telegram.send_message(chat_id, "Usage: /show <job_id>")
            return
        try:
            detail = self.worker.get_job(job_id)
            self.telegram.send_message(chat_id, self._format_job_detail(detail))
        except Exception as exc:  # noqa: BLE001
            self.telegram.send_message(chat_id, f"Failed to load job: {exc}")

    def _stop_command(self, chat_id: int, job_id: str) -> None:
        if not job_id:
            self.telegram.send_message(chat_id, "Usage: /stop <job_id>")
            return
        try:
            response = self.worker.cancel_job(job_id)
            self.telegram.send_message(chat_id, f"Cancel requested for `{response.job_id}`.")
        except Exception as exc:  # noqa: BLE001
            self.telegram.send_message(chat_id, f"Failed to stop job: {exc}")

    @staticmethod
    def _format_job_detail(detail: JobDetail) -> str:
        lines = [
            f"Job `{detail.summary.job_id}`",
            f"State: `{detail.summary.state.value}`",
            f"Goal: {detail.summary.goal}",
        ]
        if detail.request.attachments:
            lines.append(f"Attachments: {len(detail.request.attachments)}")
        if detail.summary.final_status:
            lines.append(f"Final status: `{detail.summary.final_status.value}`")
        if detail.summary.final_summary:
            lines.append(f"Summary: {detail.summary.final_summary}")
        if detail.report_path:
            lines.append(f"Report: {detail.report_path}")
        if detail.rolling_summary and detail.rolling_summary.current_summary:
            lines.append("")
            lines.append(detail.rolling_summary.current_summary)
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join(
            [
                "/run <goal> - start a new worker job",
                "Send a photo with a caption - create an image-backed job",
                "/status - show the latest run status",
                "/runs - list recent jobs",
                "/show <job_id> - show one run in detail",
                "/stop <job_id> - cancel a running job",
            ]
        )

    def _extract_attachments(self, message: dict) -> list[JobAttachment]:
        attachments: list[JobAttachment] = []
        photo_sizes = message.get("photo") or []
        if photo_sizes:
            selected = photo_sizes[-1]
            attachment = self._download_attachment(
                file_id=selected["file_id"],
                file_unique_id=selected.get("file_unique_id", ""),
                fallback_name=f"telegram-photo-{selected['file_id']}.jpg",
                kind="photo",
            )
            attachments.append(attachment)
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
        file_meta = self.telegram.get_file(file_id)
        file_path = file_meta["file_path"]
        content = self.telegram.download_file(file_path)
        return JobAttachment(
            kind=kind,
            file_name=fallback_name,
            mime_type=infer_mime_type(file_path),
            telegram_file_id=file_id,
            telegram_file_unique_id=file_unique_id,
            telegram_file_path=file_path,
            content_base64=base64.b64encode(content).decode("ascii"),
        )
