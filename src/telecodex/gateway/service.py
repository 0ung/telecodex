from __future__ import annotations

from dataclasses import dataclass

from telecodex.gateway.interfaces import ChatAdapter, IncomingMessage
from telecodex.gateway.worker_client import WorkerClient
from telecodex.shared.config import GatewayConfig
from telecodex.shared.models import JobAttachment, JobDetail, JobRequest


@dataclass
class GatewayService:
    cfg: GatewayConfig
    chat: ChatAdapter
    worker: WorkerClient

    def poll_forever(self) -> None:
        while True:
            for message in self.chat.poll_messages(timeout_sec=self.cfg.poll_timeout_sec):
                self._handle_message(message)

    def _handle_message(self, message: IncomingMessage) -> None:
        if not message.is_direct_message or message.sender_id not in self.cfg.allowed_user_ids:
            return

        text = message.text.strip()
        attachments = message.attachments
        if not text and not attachments:
            self.chat.send_message(message.conversation_id, "Send text, a photo, or both.")
            return

        if text.startswith("/run "):
            self._run_command(message.conversation_id, message.sender_id, text[5:].strip())
            return
        if text == "/status":
            self._status_command(message.conversation_id)
            return
        if text == "/runs":
            self._runs_command(message.conversation_id)
            return
        if text.startswith("/show "):
            self._show_command(message.conversation_id, text[6:].strip())
            return
        if text.startswith("/stop "):
            self._stop_command(message.conversation_id, text[6:].strip())
            return
        if text in {"/help", "/start"}:
            self.chat.send_message(message.conversation_id, self._help_text())
            return
        self._run_command(message.conversation_id, message.sender_id, text, attachments=attachments)

    def _run_command(self, conversation_id: str, user_id: int, goal: str, attachments: list[JobAttachment] | None = None) -> None:
        attachments = attachments or []
        if not goal and not attachments:
            self.chat.send_message(conversation_id, "Usage: /run <goal> or send a photo with a caption.")
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
            self.chat.send_message(conversation_id, f"Job `{response.job_id}` started with state `{response.state.value}`{attachment_suffix}.")
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to start job: {exc}")

    def _status_command(self, conversation_id: str) -> None:
        try:
            jobs = self.worker.list_jobs().jobs
            if not jobs:
                self.chat.send_message(conversation_id, "No runs yet.")
                return
            current = jobs[0]
            self.chat.send_message(
                conversation_id,
                f"Latest job `{current.job_id}` is `{current.state.value}`.\nSummary: {current.final_summary or current.goal}",
            )
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to fetch status: {exc}")

    def _runs_command(self, conversation_id: str) -> None:
        try:
            jobs = self.worker.list_jobs().jobs[:5]
            if not jobs:
                self.chat.send_message(conversation_id, "No runs yet.")
                return
            lines = [f"`{item.job_id}` - {item.state.value} - {item.goal}" for item in jobs]
            self.chat.send_message(conversation_id, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to list runs: {exc}")

    def _show_command(self, conversation_id: str, job_id: str) -> None:
        if not job_id:
            self.chat.send_message(conversation_id, "Usage: /show <job_id>")
            return
        try:
            detail = self.worker.get_job(job_id)
            self.chat.send_message(conversation_id, self._format_job_detail(detail))
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to load job: {exc}")

    def _stop_command(self, conversation_id: str, job_id: str) -> None:
        if not job_id:
            self.chat.send_message(conversation_id, "Usage: /stop <job_id>")
            return
        try:
            response = self.worker.cancel_job(job_id)
            self.chat.send_message(conversation_id, f"Cancel requested for `{response.job_id}`.")
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to stop job: {exc}")

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
                "Send a photo or file with optional text - create an attachment-backed job",
                "/status - show the latest run status",
                "/runs - list recent jobs",
                "/show <job_id> - show one run in detail",
                "/stop <job_id> - cancel a running job",
            ]
        )
