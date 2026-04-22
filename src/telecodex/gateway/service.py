from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from threading import Event, Lock, Thread

from telecodex.gateway.interfaces import ChatAdapter, IncomingMessage
from telecodex.gateway.worker_client import WorkerClient
from telecodex.shared.config import GatewayConfig
from telecodex.shared.models import (
    AIStatusResponse,
    JobAttachment,
    JobDetail,
    SessionContinueRequest,
    SessionDetail,
    SessionRequest,
    SessionState,
    truncate_text,
)


@dataclass
class GatewayService:
    cfg: GatewayConfig
    chat: ChatAdapter
    worker: WorkerClient
    _push_state: dict[str, tuple[str, str]] = field(default_factory=dict, init=False, repr=False)
    _push_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _watcher_stop: Event = field(default_factory=Event, init=False, repr=False)
    _watcher_thread: Thread | None = field(default=None, init=False, repr=False)

    def poll_forever(self) -> None:
        self._ensure_update_watcher()
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
            self._run_command(message.channel, message.conversation_id, message.sender_id, text[5:].strip(), attachments=attachments)
            return
        if text == "/status":
            self._status_command(message.channel, message.conversation_id)
            return
        if text == "/ai status":
            self._ai_status_command(message.conversation_id)
            return
        if text == "/runs":
            self._runs_command(message.channel, message.conversation_id)
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
        self._handle_freeform_message(message.channel, message.conversation_id, message.sender_id, text, attachments)

    def _handle_freeform_message(
        self,
        channel: str,
        conversation_id: str,
        user_id: int,
        text: str,
        attachments: list[JobAttachment],
    ) -> None:
        active = self._active_session(channel, conversation_id)
        if active is None:
            self._run_command(channel, conversation_id, user_id, text, attachments=attachments)
            return
        self.worker.continue_session(active.summary.session_id, SessionContinueRequest(text=text, attachments=attachments))
        detail = self._session_detail_or_none(active.summary.session_id) or active
        if active.summary.state == SessionState.WAITING_USER:
            body = self._format_session_brief(detail, f"Resumed session `{active.summary.session_id}` with your latest input.")
            self.chat.send_message(conversation_id, body)
            self._remember_session_snapshot(detail)
            return
        body = self._format_session_brief(detail, f"Added your note to active session `{active.summary.session_id}`.")
        self.chat.send_message(conversation_id, body)
        self._remember_session_snapshot(detail)

    def _run_command(
        self,
        channel: str,
        conversation_id: str,
        user_id: int,
        goal: str,
        attachments: list[JobAttachment] | None = None,
    ) -> None:
        attachments = attachments or []
        if not goal and not attachments:
            self.chat.send_message(conversation_id, "Usage: /run <goal> or send a photo with a caption.")
            return
        try:
            response = self.worker.create_session(
                SessionRequest(
                    goal=goal or "Analyze the attached input and continue the session toward a useful outcome.",
                    requester_id=user_id,
                    workspace_path=".",
                    channel=channel,
                    conversation_id=conversation_id,
                    text_only=not attachments,
                    requires_private_network=True,
                    attachments=attachments,
                )
            )
            attachment_suffix = f" with {len(attachments)} attachment(s)" if attachments else ""
            detail = self._session_detail_or_none(response.session_id)
            if detail is None:
                self.chat.send_message(
                    conversation_id,
                    f"Session `{response.session_id}` started with state `{response.state.value}`{attachment_suffix}.",
                )
                return
            body = self._format_session_brief(detail, f"Session `{response.session_id}` started{attachment_suffix}.")
            self.chat.send_message(conversation_id, body)
            self._remember_session_snapshot(detail)
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to start session: {exc}")

    def _status_command(self, channel: str, conversation_id: str) -> None:
        try:
            detail = self._latest_session(channel, conversation_id)
            if detail is None:
                self.chat.send_message(conversation_id, "No sessions yet.")
                return
            self.chat.send_message(conversation_id, self._format_session_status(detail))
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to fetch status: {exc}")

    def _runs_command(self, channel: str, conversation_id: str) -> None:
        try:
            sessions = self.worker.list_sessions(channel=channel, conversation_id=conversation_id).sessions[:5]
            if not sessions:
                self.chat.send_message(conversation_id, "No sessions yet.")
                return
            lines = []
            for item in sessions:
                summary = GatewayService._compact_text(item.final_summary or item.goal, limit=120)
                lines.append(
                    f"`{item.session_id}` - {item.state.value} - {item.verdict.value if item.verdict else 'pending'} - {summary}"
                )
            self.chat.send_message(conversation_id, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to list sessions: {exc}")

    def _ai_status_command(self, conversation_id: str) -> None:
        try:
            status = self.worker.ai_status()
            self.chat.send_message(conversation_id, self._format_ai_status(status))
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to fetch AI runtime status: {exc}")

    def _show_command(self, conversation_id: str, identifier: str) -> None:
        if not identifier:
            self.chat.send_message(conversation_id, "Usage: /show <session_id>")
            return
        try:
            detail = self.worker.get_session(identifier)
            self.chat.send_message(conversation_id, self._format_session_detail(detail))
            return
        except Exception:
            pass
        try:
            detail = self.worker.get_job(identifier)
            self.chat.send_message(conversation_id, self._format_job_detail(detail))
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to load session or job: {exc}")

    def _stop_command(self, conversation_id: str, identifier: str) -> None:
        if not identifier:
            self.chat.send_message(conversation_id, "Usage: /stop <session_id>")
            return
        try:
            response = self.worker.cancel_session(identifier)
            label = response.session_id or identifier
            self.chat.send_message(conversation_id, f"Cancel requested for session `{label}`.")
            return
        except Exception:
            pass
        try:
            response = self.worker.cancel_job(identifier)
            self.chat.send_message(conversation_id, f"Cancel requested for job `{response.job_id}`.")
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"Failed to stop session or job: {exc}")

    def _active_session(self, channel: str, conversation_id: str) -> SessionDetail | None:
        sessions = self.worker.list_sessions(channel=channel, conversation_id=conversation_id, active_only=True).sessions
        if not sessions:
            return None
        return self.worker.get_session(sessions[0].session_id)

    def _latest_session(self, channel: str, conversation_id: str) -> SessionDetail | None:
        sessions = self.worker.list_sessions(channel=channel, conversation_id=conversation_id).sessions
        if not sessions:
            return None
        return self.worker.get_session(sessions[0].session_id)

    def _ensure_update_watcher(self) -> None:
        if self._watcher_thread and self._watcher_thread.is_alive():
            return
        self._watcher_stop.clear()
        self._watcher_thread = Thread(target=self._watch_session_updates, daemon=True)
        self._watcher_thread.start()

    def _watch_session_updates(self) -> None:
        interval = max(1, self.cfg.session_push_interval_sec)
        while not self._watcher_stop.wait(interval):
            try:
                self._push_session_updates_once()
            except Exception:  # noqa: BLE001
                continue

    def _push_session_updates_once(self) -> None:
        sessions = self.worker.list_sessions().sessions[:20]
        for summary in sessions:
            if summary.channel != self.cfg.channel_provider:
                continue
            if not summary.conversation_id:
                continue
            detail = self._session_detail_or_none(summary.session_id)
            if detail is None or not self._should_push_session_update(detail):
                continue
            body = self._format_session_update(detail)
            digest = self._message_digest(body)
            updated_at = detail.summary.updated_at.isoformat()
            with self._push_lock:
                previous = self._push_state.get(detail.summary.session_id)
                if previous and (previous[0] == updated_at or previous[1] == digest):
                    continue
                self._push_state[detail.summary.session_id] = (updated_at, digest)
            self.chat.send_message(detail.summary.conversation_id, body)

    def _remember_session_snapshot(self, detail: SessionDetail) -> None:
        body = self._format_session_update(detail)
        with self._push_lock:
            self._push_state[detail.summary.session_id] = (
                detail.summary.updated_at.isoformat(),
                self._message_digest(body),
            )

    @staticmethod
    def _should_push_session_update(detail: SessionDetail) -> bool:
        if not detail.summary.conversation_id:
            return False
        if detail.summary.state == SessionState.PLANNING and not (
            detail.acceptance_criteria or detail.gemini_plan or detail.gemini_review or detail.next_action
        ):
            return False
        return True

    @staticmethod
    def _format_session_update(detail: SessionDetail) -> str:
        if detail.summary.state == SessionState.WAITING_USER:
            title = f"Session `{detail.summary.session_id}` needs your input."
        elif detail.summary.state == SessionState.COMPLETED:
            title = f"Session `{detail.summary.session_id}` completed."
        elif detail.summary.state == SessionState.FAILED:
            title = f"Session `{detail.summary.session_id}` failed."
        elif detail.summary.state == SessionState.CANCELED:
            title = f"Session `{detail.summary.session_id}` was canceled."
        else:
            title = f"Session `{detail.summary.session_id}` update."
        return GatewayService._format_session_brief(detail, title)

    @staticmethod
    def _message_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _session_detail_or_none(self, session_id: str) -> SessionDetail | None:
        try:
            return self.worker.get_session(session_id)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _format_session_brief(detail: SessionDetail, title: str) -> str:
        lines = [
            title,
            f"State: `{detail.summary.state.value}`",
            f"Verdict: `{detail.summary.verdict.value if detail.summary.verdict else 'pending'}`",
            f"Goal: {GatewayService._compact_text(detail.summary.goal, limit=180)}",
        ]
        progress_line = GatewayService._progress_line(detail)
        if progress_line:
            lines.append(progress_line)
        summary_lines = GatewayService._summary_lines(detail)
        if summary_lines:
            lines.append("")
            lines.append("Summary")
            lines.extend(summary_lines)
        focus_lines = GatewayService._focus_lines(detail)
        if focus_lines:
            lines.append("")
            lines.append(GatewayService._focus_heading(detail))
            lines.extend(focus_lines)
        return "\n".join(lines)

    @staticmethod
    def _format_session_status(detail: SessionDetail) -> str:
        lines = [
            f"Latest session `{detail.summary.session_id}`",
            f"State: `{detail.summary.state.value}`",
            f"Verdict: `{detail.summary.verdict.value if detail.summary.verdict else 'pending'}`",
            f"Goal: {GatewayService._compact_text(detail.summary.goal, limit=220)}",
        ]
        progress_line = GatewayService._progress_line(detail)
        if progress_line:
            lines.append(progress_line)
        summary_lines = GatewayService._summary_lines(detail)
        if summary_lines:
            lines.append("")
            lines.append("Summary")
            lines.extend(summary_lines)
        if detail.acceptance_criteria:
            lines.append("")
            lines.append("Criteria")
            for item in detail.acceptance_criteria[:5]:
                marker = "x" if item in detail.completed_acceptance_criteria else " "
                lines.append(f"- [{marker}] {GatewayService._compact_text(item, limit=180)}")
        dialogue_lines = GatewayService._recent_dialogue_lines(detail, max_turns=2)
        if dialogue_lines:
            lines.append("")
            lines.append("Recent dialogue")
            lines.extend(dialogue_lines)
        focus_lines = GatewayService._focus_lines(detail)
        if focus_lines:
            lines.append("")
            lines.append(GatewayService._focus_heading(detail))
            lines.extend(focus_lines)
        return "\n".join(lines)

    @staticmethod
    def _format_session_detail(detail: SessionDetail) -> str:
        lines = [
            f"Session `{detail.summary.session_id}`",
            f"State: `{detail.summary.state.value}`",
            f"Verdict: `{detail.summary.verdict.value if detail.summary.verdict else 'pending'}`",
            f"Goal: {GatewayService._compact_text(detail.summary.goal, limit=220)}",
        ]
        progress_line = GatewayService._progress_line(detail)
        if progress_line:
            lines.append(progress_line)
        if detail.request.attachments:
            lines.append(f"Attachments: {len(detail.request.attachments)}")
        summary_lines = GatewayService._summary_lines(detail)
        if summary_lines:
            lines.append("")
            lines.append("Summary")
            lines.extend(summary_lines)
        if detail.acceptance_criteria:
            lines.append("")
            lines.append("Criteria")
            for item in detail.acceptance_criteria:
                marker = "x" if item in detail.completed_acceptance_criteria else " "
                lines.append(f"- [{marker}] {GatewayService._compact_text(item, limit=180)}")
        dialogue_lines = GatewayService._recent_dialogue_lines(detail, max_turns=5)
        if dialogue_lines:
            lines.append("")
            lines.append("Dialogue")
            lines.extend(dialogue_lines)
        focus_lines = GatewayService._focus_lines(detail)
        if focus_lines:
            lines.append("")
            lines.append(GatewayService._focus_heading(detail))
            lines.extend(focus_lines)
        if detail.final_outcome:
            lines.append("")
            lines.append("Final outcome")
            lines.append(GatewayService._compact_text(detail.final_outcome, limit=280))
        return "\n".join(lines)

    @staticmethod
    def _recent_dialogue_lines(detail: SessionDetail, max_turns: int) -> list[str]:
        if not detail.turns:
            return []
        lines: list[str] = []
        for turn in detail.turns[-max_turns:]:
            lines.append(f"Turn {turn.turn_number}")
            gemini_line = turn.gemini.summary_for_user.strip() or turn.gemini.next_action.strip() or turn.gemini.status.value
            codex_line = turn.codex.summary.strip() or turn.codex.next_step.strip() or turn.codex.status.value
            lines.append(f"Gemini: {GatewayService._compact_text(gemini_line, limit=180)}")
            lines.append(f"Codex: {GatewayService._compact_text(codex_line, limit=180)}")
        return lines

    @staticmethod
    def _summary_lines(detail: SessionDetail) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        latest_turn = detail.turns[-1] if detail.turns else None
        if detail.final_outcome:
            GatewayService._append_unique_summary(lines, seen, "Outcome", detail.final_outcome)
        gemini_candidate = GatewayService._pick_summary(
            latest_turn.gemini.summary_for_user if latest_turn else "",
            detail.gemini_review,
            detail.gemini_plan,
        )
        GatewayService._append_unique_summary(lines, seen, "Gemini", gemini_candidate)
        codex_candidate = GatewayService._pick_summary(
            latest_turn.codex.summary if latest_turn else "",
            detail.codex_execution,
            detail.codex_verification,
        )
        GatewayService._append_unique_summary(lines, seen, "Codex", codex_candidate)
        review_candidate = GatewayService._pick_summary(
            detail.gemini_review,
            latest_turn.gemini.reason if latest_turn else "",
            detail.codex_verification,
        )
        GatewayService._append_unique_summary(lines, seen, "Review", review_candidate)
        return lines

    @staticmethod
    def _focus_heading(detail: SessionDetail) -> str:
        return "Needs from you" if detail.summary.state == SessionState.WAITING_USER else "Current focus"

    @staticmethod
    def _focus_lines(detail: SessionDetail) -> list[str]:
        if detail.summary.state == SessionState.WAITING_USER:
            request_text = detail.next_action or detail.gemini_review or detail.gemini_plan
            if request_text:
                return [GatewayService._compact_text(request_text, limit=220)]
            return []
        if detail.next_action:
            return [GatewayService._compact_text(detail.next_action, limit=220)]
        return []

    @staticmethod
    def _progress_line(detail: SessionDetail) -> str:
        completed = len(detail.completed_acceptance_criteria)
        total = len(detail.acceptance_criteria)
        if total == 0:
            return "Acceptance criteria: pending synthesis"
        return f"Acceptance criteria: {completed}/{total}"

    @staticmethod
    def _pick_summary(*candidates: str) -> str:
        for candidate in candidates:
            latest = GatewayService._latest_block(candidate)
            if latest:
                return latest
        return ""

    @staticmethod
    def _append_unique_summary(lines: list[str], seen: set[str], label: str, value: str) -> None:
        compact = GatewayService._compact_text(value, limit=220)
        if not compact:
            return
        key = compact.casefold()
        if key in seen:
            return
        seen.add(key)
        lines.append(f"{label}: {compact}")

    @staticmethod
    def _latest_block(text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return ""
        blocks = [chunk.strip() for chunk in stripped.replace("\r\n", "\n").split("\n\n") if chunk.strip()]
        return blocks[-1] if blocks else stripped

    @staticmethod
    def _compact_text(text: str, limit: int = 220) -> str:
        cleaned_lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines() if line.strip() and line.strip() != "_None_"]
        compact = " | ".join(cleaned_lines)
        return truncate_text(compact, limit)

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
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join(
            [
                "/run <goal> - start a new session",
                "Send a text/photo/file while a session is active - continue or add notes",
                "/status - show the latest session status",
                "/ai status - show Codex and Gemini runtime status",
                "/runs - list recent sessions in this conversation",
                "/show <session_id> - show one session in detail",
                "/stop <session_id> - cancel a running session",
            ]
        )

    @staticmethod
    def _format_ai_status(status: AIStatusResponse) -> str:
        codex = status.codex
        gemini = status.gemini
        lines = [
            "AI Runtime Status",
            "",
            f"Codex: {'ready' if codex.auth_ok else 'not ready'}",
            f"Model: {codex.configured_model or 'default'}",
            f"Auth: {codex.auth_message}",
        ]
        if codex.last_usage:
            lines.append(
                "Last usage: "
                f"in={codex.last_usage.input_tokens}, out={codex.last_usage.output_tokens}, total={codex.last_usage.total_tokens}"
            )
        else:
            lines.append("Last usage: unavailable")
        lines.extend(
            [
                "",
                f"Gemini: {'ready' if gemini.auth_ok else 'not ready'}",
                f"Model: {gemini.configured_model or 'default'}",
                f"Auth: {gemini.auth_message}",
            ]
        )
        if gemini.quota:
            lines.append(
                "Quota: "
                f"{gemini.quota.requests_per_minute or '?'} RPM, "
                f"{gemini.quota.tokens_per_minute or '?'} TPM, "
                f"{gemini.quota.requests_per_day or '?'} RPD"
            )
        if gemini.last_usage:
            lines.append(
                "Last usage: "
                f"in={gemini.last_usage.input_tokens}, out={gemini.last_usage.output_tokens}, total={gemini.last_usage.total_tokens}, "
                f"req={gemini.last_usage.requests}, err={gemini.last_usage.errors}"
            )
        else:
            lines.append("Last usage: unavailable")
        return "\n".join(lines)
