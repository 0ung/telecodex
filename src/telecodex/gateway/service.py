from __future__ import annotations

import hashlib
import json
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
        if message.attachment_errors:
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
            if not attachments and self._is_placeholder_message_without_goal(text):
                self.chat.send_message(conversation_id, "좋아요. 질문이나 요청을 한 문장으로 보내주세요.")
                return
            self._run_command(channel, conversation_id, user_id, text, attachments=attachments)
            return
        if not attachments and self._is_status_like_message(text):
            self._status_command(channel, conversation_id)
            return
        if self._should_start_new_session_from_active(active, text, attachments):
            self._run_command(channel, conversation_id, user_id, text, attachments=attachments)
            return
        self.worker.continue_session(active.summary.session_id, SessionContinueRequest(text=text, attachments=attachments))
        detail = self._session_detail_or_none(active.summary.session_id) or active
        if active.summary.state == SessionState.WAITING_USER:
            body = self._format_session_brief(detail, f"`{active.summary.session_id}` 세션에 최신 입력을 반영했습니다.")
            self.chat.send_message(conversation_id, body)
            self._remember_session_snapshot(detail)
            return
        body = self._format_session_brief(detail, f"`{active.summary.session_id}` 세션에 메모를 추가했습니다.")
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
            self.chat.send_message(conversation_id, f"세션 목록을 불러오지 못했습니다: {exc}")

    def _ai_status_command(self, conversation_id: str) -> None:
        try:
            status = self.worker.ai_status()
            self.chat.send_message(conversation_id, self._format_ai_status(status))
        except Exception as exc:  # noqa: BLE001
            self.chat.send_message(conversation_id, f"AI 런타임 상태를 불러오지 못했습니다: {exc}")

    def _show_command(self, conversation_id: str, identifier: str) -> None:
        if not identifier:
            self.chat.send_message(conversation_id, "사용법: `/show <session_id>`")
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
        except Exception:
            pass
        try:
            response = self.worker.cancel_job(identifier)
            self.chat.send_message(conversation_id, f"`{response.job_id}` 작업에 중단 요청을 보냈습니다.")
        except Exception as exc:  # noqa: BLE001
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
    def _message_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_placeholder_message_without_goal(text: str) -> bool:
        normalized = text.strip().casefold()
        if not normalized:
            return True
        placeholders = {
            "다시 질문할게",
            "다시 물어볼게",
            "질문할게",
            "잠깐만",
            "잠시만",
            "잠만",
            "다시",
            "다시요",
        }
        return normalized in placeholders

    @staticmethod
    def _is_status_like_message(text: str) -> bool:
        normalized = text.strip().casefold()
        status_messages = {
            "끝이야",
            "끝이야?",
            "됐어",
            "됐어?",
            "완료됐어",
            "완료됐어?",
            "완료야",
            "완료야?",
            "어디까지 됐어",
            "어디까지 됐어?",
            "진행됐어",
            "진행됐어?",
        }
        return normalized in status_messages

    @staticmethod
    def _should_start_new_session_from_active(
        active: SessionDetail,
        text: str,
        attachments: list[JobAttachment],
    ) -> bool:
        if attachments:
            return False
        if active.summary.state not in {SessionState.PLANNING, SessionState.REVIEWING, SessionState.WAITING_USER}:
            return False

        normalized = text.strip().casefold()
        if not normalized or GatewayService._is_placeholder_message_without_goal(text):
            return False

        strong_new_goal_tokens = [
            "gemini",
            "codex",
            "mcp",
            "ai status",
            "뭘 할 수",
            "무엇을 할 수",
            "뭐가 문제",
            "문제지",
            "설명해",
            "설명해줘",
            "알려줘",
            "what can",
            "what is",
            "why",
            "how",
            "issue",
            "problem",
        ]
        if any(token in normalized for token in strong_new_goal_tokens):
            return True

        return False

    def _session_detail_or_none(self, session_id: str) -> SessionDetail | None:
        try:
            return self.worker.get_session(session_id)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _format_session_brief(detail: SessionDetail, title: str) -> str:
        lines = [
            title,
            f"상태: {GatewayService._state_label(detail.summary.state)}",
            f"판단: {GatewayService._verdict_label(detail.summary.verdict)}",
            f"목표: {GatewayService._compact_text(detail.summary.goal, limit=260)}",
        ]
        progress_line = GatewayService._progress_line(detail)
        if progress_line:
            lines.append(progress_line)
        summary_lines = GatewayService._summary_lines(detail)
        if summary_lines:
            lines.append("")
            lines.append("진행 요약")
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
            f"최근 세션 `{detail.summary.session_id}`",
            f"상태: {GatewayService._state_label(detail.summary.state)}",
            f"판단: {GatewayService._verdict_label(detail.summary.verdict)}",
            f"목표: {GatewayService._compact_text(detail.summary.goal, limit=320)}",
        ]
        progress_line = GatewayService._progress_line(detail)
        if progress_line:
            lines.append(progress_line)
        summary_lines = GatewayService._summary_lines(detail)
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
        dialogue_lines = GatewayService._recent_dialogue_lines(detail, max_turns=2)
        if dialogue_lines:
            lines.append("")
            lines.append("최근 대화")
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
        summary_lines = GatewayService._summary_lines(detail)
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
        focus_lines = GatewayService._focus_lines(detail)
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
    def _summary_lines(detail: SessionDetail) -> list[str]:
        lines: list[str] = []
        seen: set[str] = set()
        latest_turn = detail.turns[-1] if detail.turns else None
        if detail.final_outcome:
            GatewayService._append_unique_summary(lines, seen, "결과", detail.final_outcome)
        error_candidate = (detail.error or (detail.latest_job.error if detail.latest_job else "")).strip()
        GatewayService._append_unique_summary(lines, seen, "오류", error_candidate)
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
        key = compact.casefold()
        if key in seen:
            return
        seen.add(key)
        lines.append(f"- {label}: {compact}")

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
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return GatewayService._localize_known_english_text(stripped)
        extracted = GatewayService._extract_display_text_from_payload(payload)
        return GatewayService._localize_known_english_text(extracted or stripped)

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
            return GatewayService._localize_known_english_text(payload.strip())
        return ""

    @staticmethod
    def _localize_known_english_text(text: str) -> str:
        normalized = " ".join(text.strip().split())
        replacements = {
            "The user's goal is too general to proceed. I need to ask for more specific details about the development task they wish to undertake.": (
                "사용자 목표가 아직 너무 넓어서 바로 진행할 수 없습니다. 어떤 개발 작업을 할지 조금 더 구체적인 설명이 필요합니다."
            ),
            "User indicated they want to ask again and provided no new specific goal.": (
                "사용자가 다시 질문하겠다고 했지만 아직 구체적인 요청은 주지 않았습니다."
            ),
        }
        return replacements.get(normalized, text.strip())

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
