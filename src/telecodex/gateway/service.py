from __future__ import annotations

from dataclasses import dataclass

from telecodex.gateway.telegram import TelegramClient
from telecodex.gateway.worker_client import WorkerClient
from telecodex.shared.config import GatewayConfig
from telecodex.shared.models import JobDetail, JobRequest


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
        if not text:
            self.telegram.send_message(chat_id, "Only text requests are supported.")
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
        self._run_command(chat_id, user_id, text)

    def _run_command(self, chat_id: int, user_id: int, goal: str) -> None:
        if not goal:
            self.telegram.send_message(chat_id, "Usage: /run <goal>")
            return
        try:
            response = self.worker.create_job(
                JobRequest(
                    goal=goal,
                    requester_id=user_id,
                    workspace_path=".",
                    text_only=True,
                    requires_private_network=True,
                )
            )
            self.telegram.send_message(chat_id, f"Job `{response.job_id}` started with state `{response.state.value}`.")
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
                "/status - show the latest run status",
                "/runs - list recent jobs",
                "/show <job_id> - show one run in detail",
                "/stop <job_id> - cancel a running job",
            ]
        )
