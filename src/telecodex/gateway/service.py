from __future__ import annotations

import hashlib
import json
import logging
import re
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


logger = logging.getLogger(__name__)


@dataclass
class GatewayService:
    cfg: GatewayConfig
    chat: ChatAdapter
    worker: WorkerClient
    _push_state: dict[str, tuple[str, str]] = field(default_factory=dict, init=False, repr=False)
    _push_state_primed: bool = field(default=False, init=False, repr=False)
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
        if message.attachment_errors:
            self._log_warning(
                "attachment_validation_failed",
                channel=message.channel,
                conversation_id=message.conversation_id,
                sender_id=message.sender_id,
                error_count=len(message.attachment_errors),
            )
            self.chat.send_message(message.conversation_id, self._format_attachment_errors(message.attachment_errors))
            return
        if not text and not attachments:
            self.chat.send_message(message.conversation_id, "텍스트나 사진을 함께 보내주세요.")
            return

        if text.startswith("/run "):
            self._run_command(message.channel, message.conversation_id, message.sender_id, text[5:].strip(), attachments=attachments)
            return
        if text == "/run":
            self.chat.send_message(message.conversation_id, "사용법: `/run <목표>` 또는 설명이 붙은 사진을 보내주세요.")
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
            body = self._format_input_ack(
                active.summary.session_id,
                "최신 입력을 반영했습니다. 이어서 처리 중입니다.",
            )
            self.chat.send_message(conversation_id, body)
            self._remember_session_snapshot(detail)
            return
        body = self._format_input_ack(
            active.summary.session_id,
            "메모를 추가했습니다. 다음 검토 턴에 반영하겠습니다.",
        )
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
            self.chat.send_message(conversation_id, "사용법: `/run <목표>` 또는 설명이 붙은 사진을 보내주세요.")
            return
        try:
            response = self.worker.create_session(
                SessionRequest(
                    goal=goal or "첨부한 입력을 분석하고, 사용자가 원하는 결과가 나올 때까지 세션을 이어가세요.",
                    requester_id=user_id,
                    workspace_path=".",
                    channel=channel,
                    conversation_id=conversation_id,
                    text_only=not attachments,
                    requires_private_network=True,
                    attachments=attachments,
                )
            )
            attachment_suffix = f" / 첨부 {len(attachments)}개" if attachments else ""
            detail = self._session_detail_or_none(response.session_id)
            if detail is None:
                self.chat.send_message(
                    conversation_id,
                    f"`{response.session_id}` 세션을 시작했습니다. 현재 상태는 {self._state_label(response.state)}입니다{attachment_suffix}.",
                )
                return
            body = self._format_session_brief(detail, f"`{response.session_id}` 세션을 시작했습니다{attachment_suffix}.")
            self.chat.send_message(conversation_id, body)
            self._remember_session_snapshot(detail)
        except Exception as exc:  # noqa: BLE001
            self._log_exception(
                "run_command_failed",
                exc,
                channel=channel,
                conversation_id=conversation_id,
                sender_id=user_id,
                attachment_count=len(attachments),
            )
            self.chat.send_message(conversation_id, f"세션 시작에 실패했습니다: {exc}")

    @staticmethod
    def _format_attachment_errors(errors: list[str]) -> str:
        lines = ["첨부를 처리할 수 없습니다."]
        lines.extend(f"- {item}" for item in errors)
        return "\n".join(lines)

    def _status_command(self, channel: str, conversation_id: str) -> None:
        try:
            detail = self._latest_session(channel, conversation_id)
            if detail is None:
                self.chat.send_message(conversation_id, "아직 시작된 세션이 없습니다.")
                return
            self.chat.send_message(conversation_id, self._format_session_status(detail))
        except Exception as exc:  # noqa: BLE001
            self._log_exception(
                "status_command_failed",
                exc,
                channel=channel,
                conversation_id=conversation_id,
            )
            self.chat.send_message(conversation_id, f"상태를 불러오지 못했습니다: {exc}")

    def _runs_command(self, channel: str, conversation_id: str) -> None:
        try:
            sessions = self.worker.list_sessions(channel=channel, conversation_id=conversation_id).sessions[:5]
            if not sessions:
                self.chat.send_message(conversation_id, "아직 시작된 세션이 없습니다.")
                return
            lines = []
            for item in sessions:
                summary = GatewayService._compact_text(item.final_summary or item.goal, limit=180)
                lines.append(
                    f"- `{item.session_id}` | {self._state_label(item.state)} | {self._verdict_label(item.verdict)} | {summary}"
                )
            self.chat.send_message(conversation_id, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self._log_exception(
                "runs_command_failed",
                exc,
                channel=channel,
                conversation_id=conversation_id,
            )
            self.chat.send_message(conversation_id, f"세션 목록을 불러오지 못했습니다: {exc}")

    def _ai_status_command(self, conversation_id: str) -> None:
        try:
            status = self.worker.ai_status()
            self.chat.send_message(conversation_id, self._format_ai_status(status))
        except Exception as exc:  # noqa: BLE001
            self._log_exception("ai_status_command_failed", exc, conversation_id=conversation_id)
            self.chat.send_message(conversation_id, f"AI 런타임 상태를 불러오지 못했습니다: {exc}")

    def _show_command(self, conversation_id: str, identifier: str) -> None:
        if not identifier:
            self.chat.send_message(conversation_id, "사용법: `/show <session_id>`")
            return
        try:
            detail = self.worker.get_session(identifier)
            self.chat.send_message(conversation_id, self._format_session_detail(detail))
            return
        except Exception as exc:
            self._log_exception("show_session_lookup_failed", exc, conversation_id=conversation_id, identifier=identifier)
            pass
        try:
            detail = self.worker.get_job(identifier)
            self.chat.send_message(conversation_id, self._format_job_detail(detail))
        except Exception as exc:  # noqa: BLE001
            self._log_exception("show_job_lookup_failed", exc, conversation_id=conversation_id, identifier=identifier)
            self.chat.send_message(conversation_id, f"세션이나 작업 정보를 불러오지 못했습니다: {exc}")

    def _stop_command(self, conversation_id: str, identifier: str) -> None:
        if not identifier:
            self.chat.send_message(conversation_id, "사용법: `/stop <session_id>`")
            return
        try:
            response = self.worker.cancel_session(identifier)
            label = response.session_id or identifier
            self.chat.send_message(conversation_id, f"`{label}` 세션에 중단 요청을 보냈습니다.")
            return
        except Exception as exc:
            self._log_exception("stop_session_failed", exc, conversation_id=conversation_id, identifier=identifier)
            pass
        try:
            response = self.worker.cancel_job(identifier)
            self.chat.send_message(conversation_id, f"`{response.job_id}` 작업에 중단 요청을 보냈습니다.")
        except Exception as exc:  # noqa: BLE001
            self._log_exception("stop_job_failed", exc, conversation_id=conversation_id, identifier=identifier)
            self.chat.send_message(conversation_id, f"세션이나 작업을 중단하지 못했습니다: {exc}")

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
        if not self._push_state_primed:
            self._prime_push_state()
        self._watcher_stop.clear()
        self._watcher_thread = Thread(target=self._watch_session_updates, daemon=True)
        self._watcher_thread.start()

    def _watch_session_updates(self) -> None:
        interval = max(1, self.cfg.session_push_interval_sec)
        while not self._watcher_stop.wait(interval):
            try:
                self._push_session_updates_once()
            except Exception as exc:  # noqa: BLE001
                self._log_exception("session_update_push_failed", exc, interval=interval)
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
            self._push_state_primed = True
            self._push_state[detail.summary.session_id] = (
                detail.summary.updated_at.isoformat(),
                self._message_digest(body),
            )

    def _prime_push_state(self) -> None:
        sessions = self.worker.list_sessions().sessions[:20]
        primed: dict[str, tuple[str, str]] = {}
        for summary in sessions:
            if summary.channel != self.cfg.channel_provider:
                continue
            if not summary.conversation_id:
                continue
            detail = self._session_detail_or_none(summary.session_id)
            if detail is None or not self._should_push_session_update(detail):
                continue
            body = self._format_session_update(detail)
            primed[detail.summary.session_id] = (
                detail.summary.updated_at.isoformat(),
                self._message_digest(body),
            )
        with self._push_lock:
            self._push_state_primed = True
            self._push_state.update(primed)

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
            title = f"`{detail.summary.session_id}` 세션에서 추가 정보가 필요합니다."
        elif detail.summary.state == SessionState.COMPLETED:
            title = f"`{detail.summary.session_id}` 세션이 완료되었습니다."
        elif detail.summary.state == SessionState.FAILED:
            title = f"`{detail.summary.session_id}` 세션이 실패 상태로 종료되었습니다."
        elif detail.summary.state == SessionState.CANCELED:
            title = f"`{detail.summary.session_id}` 세션이 취소되었습니다."
        else:
            title = f"`{detail.summary.session_id}` 세션 업데이트"
        return GatewayService._format_session_brief(detail, title)

    @staticmethod
    def _format_input_ack(session_id: str, message: str) -> str:
        return "\n".join(
            [
                f"`{session_id}` 세션에 입력을 받았습니다.",
                message,
            ]
        )

    @staticmethod
    def _message_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _session_detail_or_none(self, session_id: str) -> SessionDetail | None:
        try:
            return self.worker.get_session(session_id)
        except Exception as exc:  # noqa: BLE001
            self._log_exception("session_detail_lookup_failed", exc, session_id=session_id)
            return None

    @staticmethod
    def _log_warning(action: str, **context) -> None:  # noqa: ANN003
        logger.warning("%s | %s", action, GatewayService._log_context(context))

    @staticmethod
    def _log_exception(action: str, exc: Exception, **context) -> None:  # noqa: ANN003
        merged = {"error_type": exc.__class__.__name__, **context}
        logger.exception("%s | %s", action, GatewayService._log_context(merged))

    @staticmethod
    def _log_context(context: dict[str, object]) -> str:
        parts = [f"{key}={value}" for key, value in context.items() if value not in {None, ''}]
        return " ".join(parts)

    @staticmethod
    def _format_session_brief(detail: SessionDetail, title: str) -> str:
        focus_lines = GatewayService._focus_lines(detail)
        lines = [
            title,
            f"상태: {GatewayService._state_label(detail.summary.state)}",
            f"판단: {GatewayService._verdict_label(detail.summary.verdict)}",
            f"목표: {GatewayService._compact_text(detail.summary.goal, limit=260)}",
        ]
        progress_line = GatewayService._progress_line(detail)
        if progress_line:
            lines.append(progress_line)
        summary_lines = GatewayService._summary_lines(detail, exclude_texts=focus_lines)
        if summary_lines:
            lines.append("")
            lines.append("진행 요약")
            lines.extend(summary_lines)
        if focus_lines:
            lines.append("")
            lines.append(GatewayService._focus_heading(detail))
            lines.extend(focus_lines)
        return "\n".join(lines)

    @staticmethod
    def _format_session_status(detail: SessionDetail) -> str:
        focus_lines = GatewayService._focus_lines(detail)
        lines = [
            f"최근 세션 `{detail.summary.session_id}`",
            f"상태: {GatewayService._state_label(detail.summary.state)}",
            f"판단: {GatewayService._verdict_label(detail.summary.verdict)}",
            f"목표: {GatewayService._compact_text(detail.summary.goal, limit=320)}",
        ]
        progress_line = GatewayService._progress_line(detail)
        if progress_line:
            lines.append(progress_line)
        summary_lines = GatewayService._summary_lines(detail, exclude_texts=focus_lines)
        if summary_lines:
            lines.append("")
            lines.append("진행 요약")
            lines.extend(summary_lines)
        if detail.acceptance_criteria:
            lines.append("")
            lines.append("완료 조건")
            for item in detail.acceptance_criteria[:5]:
                marker = "x" if item in detail.completed_acceptance_criteria else " "
                lines.append(f"- [{marker}] {GatewayService._compact_text(item, limit=260)}")
        dialogue_lines = []
        if detail.summary.state not in {SessionState.COMPLETED, SessionState.CANCELED}:
            dialogue_lines = GatewayService._recent_dialogue_lines(detail, max_turns=2)
        if dialogue_lines:
            lines.append("")
            lines.append("최근 대화")
            lines.extend(dialogue_lines)
        if focus_lines:
            lines.append("")
            lines.append(GatewayService._focus_heading(detail))
            lines.extend(focus_lines)
        return "\n".join(lines)

    @staticmethod
    def _format_session_detail(detail: SessionDetail) -> str:
        focus_lines = GatewayService._focus_lines(detail)
        lines = [
            f"세션 `{detail.summary.session_id}`",
            f"상태: {GatewayService._state_label(detail.summary.state)}",
            f"판단: {GatewayService._verdict_label(detail.summary.verdict)}",
            f"목표: {GatewayService._compact_text(detail.summary.goal, limit=320)}",
        ]
        progress_line = GatewayService._progress_line(detail)
        if progress_line:
            lines.append(progress_line)
        if detail.request.attachments:
            lines.append(f"첨부: {len(detail.request.attachments)}개")
        summary_lines = GatewayService._summary_lines(detail, exclude_texts=focus_lines)
        if summary_lines:
            lines.append("")
            lines.append("진행 요약")
            lines.extend(summary_lines)
        if detail.acceptance_criteria:
            lines.append("")
            lines.append("완료 조건")
            for item in detail.acceptance_criteria:
                marker = "x" if item in detail.completed_acceptance_criteria else " "
                lines.append(f"- [{marker}] {GatewayService._compact_text(item, limit=260)}")
        dialogue_lines = GatewayService._recent_dialogue_lines(detail, max_turns=5)
        if dialogue_lines:
            lines.append("")
            lines.append("대화 기록")
            lines.extend(dialogue_lines)
        if focus_lines:
            lines.append("")
            lines.append(GatewayService._focus_heading(detail))
            lines.extend(focus_lines)
        if detail.final_outcome:
            lines.append("")
            lines.append("최종 결과")
            lines.append(GatewayService._compact_text(detail.final_outcome, limit=420))
        return "\n".join(lines)

    @staticmethod
    def _recent_dialogue_lines(detail: SessionDetail, max_turns: int) -> list[str]:
        if not detail.turns:
            return []
        lines: list[str] = []
        for turn in detail.turns[-max_turns:]:
            lines.append(f"턴 {turn.turn_number}")
            gemini_line = turn.gemini.summary_for_user.strip() or turn.gemini.next_action.strip() or turn.gemini.status.value
            codex_line = turn.codex.summary.strip() or turn.codex.next_step.strip() or turn.codex.status.value
            lines.append(f"- Gemini: {GatewayService._compact_text(gemini_line, limit=260)}")
            lines.append(f"- Codex: {GatewayService._compact_text(codex_line, limit=260)}")
        return lines

    @staticmethod
    def _summary_lines(detail: SessionDetail, exclude_texts: list[str] | None = None) -> list[str]:
        lines: list[str] = []
        seen = {GatewayService._summary_key(item) for item in (exclude_texts or []) if item.strip()}
        latest_turn = detail.turns[-1] if detail.turns else None
        if detail.final_outcome:
            GatewayService._append_unique_summary(lines, seen, "결과", detail.final_outcome)
            if detail.summary.state in {SessionState.COMPLETED, SessionState.CANCELED}:
                return lines
        error_candidate = (detail.error or (detail.latest_job.error if detail.latest_job else "")).strip()
        GatewayService._append_unique_summary(lines, seen, "오류", error_candidate)
        if detail.summary.state == SessionState.FAILED and lines:
            return lines
        gemini_candidate = GatewayService._pick_summary(
            latest_turn.gemini.summary_for_user if latest_turn else "",
            detail.gemini_review,
            detail.gemini_plan,
        )
        GatewayService._append_unique_summary(lines, seen, "계획", gemini_candidate)
        codex_candidate = GatewayService._pick_summary(
            latest_turn.codex.summary if latest_turn else "",
            detail.codex_execution,
            detail.codex_verification,
        )
        GatewayService._append_unique_summary(lines, seen, "실행", codex_candidate)
        review_candidate = GatewayService._pick_summary(
            detail.gemini_review,
            latest_turn.gemini.reason if latest_turn else "",
            detail.codex_verification,
        )
        GatewayService._append_unique_summary(lines, seen, "검토", review_candidate)
        return lines

    @staticmethod
    def _focus_heading(detail: SessionDetail) -> str:
        return "필요한 정보" if detail.summary.state == SessionState.WAITING_USER else "다음 단계"

    @staticmethod
    def _focus_lines(detail: SessionDetail) -> list[str]:
        if detail.summary.state.is_terminal:
            return []
        if detail.summary.state == SessionState.WAITING_USER:
            request_text = detail.next_action or detail.gemini_review or detail.gemini_plan
            if request_text:
                return GatewayService._readable_lines(request_text, limit=260)
            return []
        if detail.next_action:
            return GatewayService._readable_lines(detail.next_action, limit=260)
        return []

    @staticmethod
    def _progress_line(detail: SessionDetail) -> str:
        completed = len(detail.completed_acceptance_criteria)
        total = len(detail.acceptance_criteria)
        if total == 0:
            return "완료 조건: 정리 중"
        return f"완료 조건: {completed}/{total}"

    @staticmethod
    def _pick_summary(*candidates: str) -> str:
        for candidate in candidates:
            latest = GatewayService._latest_block(candidate)
            if latest:
                return latest
        return ""

    @staticmethod
    def _append_unique_summary(lines: list[str], seen: set[str], label: str, value: str) -> None:
        compact = GatewayService._compact_text(value, limit=360)
        if not compact:
            return
        key = GatewayService._summary_key(compact)
        if key in seen:
            return
        seen.add(key)
        lines.append(f"- {label}: {compact}")

    @staticmethod
    def _summary_key(text: str) -> str:
        return " ".join(text.casefold().split())

    @staticmethod
    def _latest_block(text: str) -> str:
        stripped = GatewayService._extract_display_text(text)
        if not stripped:
            return ""
        blocks = [chunk.strip() for chunk in stripped.replace("\r\n", "\n").split("\n\n") if chunk.strip()]
        return blocks[-1] if blocks else stripped

    @staticmethod
    def _compact_text(text: str, limit: int = 320) -> str:
        display_text = GatewayService._extract_display_text(text)
        cleaned_lines = [line.strip() for line in display_text.replace("\r\n", "\n").splitlines() if line.strip() and line.strip() != "_None_"]
        compact = " ".join(cleaned_lines)
        return truncate_text(compact, limit)

    @staticmethod
    def _readable_lines(text: str, limit: int = 320, max_items: int = 6) -> list[str]:
        normalized = GatewayService._extract_display_text(text).replace("\r\n", "\n").replace("|", "\n")
        items: list[str] = []
        for raw_line in normalized.splitlines():
            cleaned = raw_line.strip()
            if not cleaned or cleaned == "_None_":
                continue
            if cleaned.startswith("- "):
                cleaned = cleaned[2:].strip()
            elif cleaned.startswith("* "):
                cleaned = cleaned[2:].strip()
            items.append(cleaned)
        if not items:
            return []
        lines: list[str] = []
        for index, item in enumerate(items[:max_items]):
            prefix = "" if index == 0 else "- "
            lines.append(truncate_text(f"{prefix}{item}", limit))
        return lines

    @staticmethod
    def _extract_display_text(text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return ""
        payload = GatewayService._parse_jsonish_payload(stripped)
        if payload is None:
            extracted = GatewayService._extract_display_field_from_jsonish_text(stripped)
            if extracted:
                return extracted
            if GatewayService._looks_like_structured_payload(stripped):
                return ""
            return stripped
        extracted = GatewayService._extract_display_text_from_payload(payload)
        return extracted or stripped

    @staticmethod
    def _extract_display_text_from_payload(payload: object) -> str:
        if isinstance(payload, dict):
            if "response" in payload:
                nested = GatewayService._extract_display_text_from_payload(payload["response"])
                if nested:
                    return nested
            for key in ("summary_for_user", "question_for_user", "next_action", "summary", "raw_output", "reason"):
                value = payload.get(key)
                if value is None:
                    continue
                nested = GatewayService._extract_display_text(str(value))
                if nested:
                    return nested
            return ""
        if isinstance(payload, str):
            return payload.strip()
        return ""

    @staticmethod
    def _parse_jsonish_payload(text: str) -> object | None:
        for candidate in GatewayService._json_text_candidates(text):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _json_text_candidates(text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []
        variants = [stripped, GatewayService._strip_markdown_json_bullets(stripped)]
        candidates: list[str] = []
        for variant in variants:
            if not variant:
                continue
            candidates.append(variant)
            object_start = variant.find("{")
            object_end = variant.rfind("}")
            if 0 <= object_start < object_end:
                candidates.append(variant[object_start : object_end + 1].strip())
        unique: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in unique:
                unique.append(candidate)
        return unique

    @staticmethod
    def _strip_markdown_json_bullets(text: str) -> str:
        lines: list[str] = []
        for line in text.splitlines():
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]
            if stripped.startswith(("- ", "* ")):
                candidate = stripped[2:].lstrip()
                if candidate.startswith(('"', "{", "}", "[", "]")):
                    line = indent + candidate
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _extract_display_field_from_jsonish_text(text: str) -> str:
        for candidate in GatewayService._json_text_candidates(text):
            for key in ("summary_for_user", "question_for_user", "next_action", "summary", "reason"):
                pattern = rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"'
                match = re.search(pattern, candidate, flags=re.DOTALL)
                if not match:
                    continue
                raw_value = match.group(1)
                try:
                    value = json.loads(f'"{raw_value}"')
                except json.JSONDecodeError:
                    value = raw_value
                extracted = GatewayService._extract_display_text(str(value))
                if extracted:
                    return extracted
        return ""

    @staticmethod
    def _looks_like_structured_payload(text: str) -> bool:
        normalized = text.casefold()
        return (
            normalized.startswith(("{", "- {", "* {"))
            or '"status"' in normalized
            or '"summary_for_user"' in normalized
            or '"instruction_for_codex"' in normalized
            or '"acceptance_criteria"' in normalized
        )

    @staticmethod
    def _state_label(state: SessionState) -> str:
        return {
            SessionState.PLANNING: "계획 수립 중",
            SessionState.EXECUTING: "구현 진행 중",
            SessionState.REVIEWING: "검토 중",
            SessionState.WAITING_USER: "입력 대기",
            SessionState.COMPLETED: "완료",
            SessionState.FAILED: "실패",
            SessionState.CANCELED: "취소됨",
        }.get(state, state.value)

    @staticmethod
    def _verdict_label(verdict: object) -> str:
        if verdict is None:
            return "정리 중"
        value = getattr(verdict, "value", str(verdict))
        return {
            "continue": "계속 진행",
            "done": "완료",
            "ask_user": "추가 정보 필요",
            "fail": "실패",
            "pending": "정리 중",
        }.get(str(value), str(value))

    @staticmethod
    def _format_job_detail(detail: JobDetail) -> str:
        lines = [
            f"작업 `{detail.summary.job_id}`",
            f"상태: {detail.summary.state.value}",
            f"목표: {detail.summary.goal}",
        ]
        if detail.summary.final_status:
            lines.append(f"최종 상태: {detail.summary.final_status.value}")
        if detail.summary.final_summary:
            lines.append(f"요약: {detail.summary.final_summary}")
        if detail.report_path:
            lines.append(f"리포트: {detail.report_path}")
        return "\n".join(lines)

    @staticmethod
    def _help_text() -> str:
        return "\n".join(
            [
                "/run <목표> - 새 세션 시작",
                "세션이 진행 중일 때 텍스트/사진/파일 전송 - 이어서 진행하거나 메모 추가",
                "/status - 최근 세션 상태 보기",
                "/ai status - Codex / Gemini 런타임 상태 보기",
                "/runs - 현재 대화의 최근 세션 목록 보기",
                "/show <session_id> - 특정 세션 자세히 보기",
                "/stop <session_id> - 실행 중인 세션 중단 요청",
            ]
        )

    @staticmethod
    def _format_ai_status(status: AIStatusResponse) -> str:
        codex = status.codex
        gemini = status.gemini
        lines = [
            "AI 런타임 상태",
            "",
            f"Codex: {'준비됨' if codex.auth_ok else '미준비'}",
            f"모델: {codex.configured_model or 'default'}",
            f"인증: {codex.auth_message}",
        ]
        if codex.last_usage:
            lines.append(
                "최근 사용량: "
                f"in={codex.last_usage.input_tokens}, out={codex.last_usage.output_tokens}, total={codex.last_usage.total_tokens}"
            )
        else:
            lines.append("최근 사용량: 없음")
        lines.extend(
            [
                "",
                f"Gemini: {'준비됨' if gemini.auth_ok else '미준비'}",
                f"모델: {gemini.configured_model or 'default'}",
                f"인증: {gemini.auth_message}",
            ]
        )
        if gemini.quota:
            lines.append(
                "쿼터: "
                f"{gemini.quota.requests_per_minute or '?'} RPM, "
                f"{gemini.quota.tokens_per_minute or '?'} TPM, "
                f"{gemini.quota.requests_per_day or '?'} RPD"
            )
        if gemini.last_usage:
            lines.append(
                "최근 사용량: "
                f"in={gemini.last_usage.input_tokens}, out={gemini.last_usage.output_tokens}, total={gemini.last_usage.total_tokens}, "
                f"req={gemini.last_usage.requests}, err={gemini.last_usage.errors}"
            )
        else:
            lines.append("최근 사용량: 없음")
        return "\n".join(lines)
