from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from telecodex.shared.cli import JsonCliAdapter
from telecodex.shared.config import WorkerConfig
from telecodex.shared.models import (
    AdapterExchange,
    AuditEvent,
    CancelResponse,
    CodexRequest,
    CodexResult,
    CodexStatus,
    CommandExecution,
    FinalResult,
    FinalStatus,
    GeminiRequest,
    GeminiResponse,
    GeminiStatus,
    JobDetail,
    JobRequest,
    JobState,
    JobSummary,
    RollingSummary,
    RunMetadata,
    SessionContinueRequest,
    SessionCreateResponse,
    SessionDetail,
    SessionRequest,
    SessionState,
    SessionSummary,
    SessionVerdict,
    TurnRecord,
    codex_terminal_reason,
    derive_acceptance_criteria,
    generate_final_report,
    merge_unique_items,
    resolve_session_verdict,
    truncate_text,
    update_rolling_summary,
    utc_now,
)
from telecodex.worker.session_docs import SessionDocumentStore, merge_text
from telecodex.worker.session_mcp import SessionMcpServer, SessionMcpService
from telecodex.worker.sessions import ConversationSessionStore
from telecodex.worker.store import FileRunStore


logger = logging.getLogger(__name__)


@dataclass
class OrchestrationRuntime:
    cfg: WorkerConfig
    gemini: JsonCliAdapter
    codex: JsonCliAdapter


@dataclass
class SessionRuntimeState:
    summary: SessionSummary
    request: SessionRequest
    detail: SessionDetail
    cancel_event: Event = field(default_factory=Event)
    processing: bool = False
    run_counter: int = 0
    lock: Lock = field(default_factory=Lock)


class WorkerOrchestrator:
    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg
        self.store = SessionDocumentStore(cfg.runs_dir)
        self.sessions = ConversationSessionStore(cfg.runs_dir)
        self.mcp_service = SessionMcpService(self.store)
        self.mcp_server = SessionMcpServer(self.mcp_service)
        self.mcp_config = None
        self.runtime = OrchestrationRuntime(
            cfg=cfg,
            gemini=JsonCliAdapter("gemini", cfg.gemini, cfg.dry_run),
            codex=JsonCliAdapter("codex", cfg.codex, cfg.dry_run),
        )

    def start(self) -> None:
        if self.mcp_config is None:
            self.mcp_config = self.mcp_server.start()

    def shutdown(self) -> None:
        self.mcp_server.stop()
        self.mcp_config = None

    def process_session(self, state: SessionRuntimeState) -> SessionDetail:
        self.start()
        assert self.mcp_config is not None
        session_id = state.summary.session_id
        state.run_counter += 1
        run_id = f"{session_id}-run-{state.run_counter:02d}"
        store = FileRunStore(self.cfg.runs_dir, run_id, max_attachment_bytes=self.cfg.max_attachment_bytes)
        started_at = utc_now()
        effective_workspace = self._resolve_workspace(state.request.workspace_path)
        state.request.workspace_path = effective_workspace

        self.mcp_service.session_set_verdict(
            session_id=session_id,
            verdict=SessionVerdict.CONTINUE.value,
            status=SessionState.PLANNING.value,
            active_run_id=run_id,
        )
        self._refresh_session_detail(state)

        metadata = RunMetadata(
            run_id=run_id,
            session_id=session_id,
            project_path=state.request.workspace_path,
            goal=state.request.goal,
            max_turns=self.cfg.max_turns,
            effective_max_turns=self.cfg.max_turns,
            max_codex_failures=self.cfg.max_codex_failures,
            started_at=started_at,
        )
        store.save_metadata(metadata)
        store.save_job_request(
            JobRequest(
                **state.request.model_dump(mode="json"),
                session_id=session_id,
            )
        )

        rolling = state.detail.latest_job.rolling_summary if state.detail.latest_job and state.detail.latest_job.rolling_summary else RollingSummary()
        latest_codex = state.detail.turns[-1].codex if state.detail.turns else CodexResult()
        conversation_key = self._conversation_key(state.request)
        previous_response_id = self.sessions.get_previous_response_id(conversation_key)
        thread_id = self.sessions.get_thread_id(conversation_key)
        failures = rolling.codex_failures
        local_turns: list[TurnRecord] = []
        final_status: FinalStatus | None = None
        final_reason = ""
        final_summary = ""
        completed_criteria = list(state.detail.completed_acceptance_criteria)
        try:
            for local_turn in range(1, self.cfg.max_turns + 1):
                global_turn = len(state.detail.turns) + 1
                if state.cancel_event.is_set():
                    final_status = FinalStatus.CANCELED
                    final_reason = "session canceled by user"
                    final_summary = "The session was canceled before completion."
                    break

                gemini_state = SessionState.PLANNING if not state.detail.turns and local_turn == 1 else SessionState.REVIEWING
                self.mcp_service.session_set_verdict(
                    session_id=session_id,
                    verdict=SessionVerdict.CONTINUE.value,
                    status=gemini_state.value,
                    active_run_id=run_id,
                )
                latest_user_input = state.request.user_notes[-1] if state.request.user_notes else ""
                gemini_resp, gemini_exchange = self.runtime.gemini.execute(
                    GeminiRequest(
                        session_id=session_id,
                        user_goal=state.request.goal,
                        current_summary=rolling.current_summary,
                        acceptance_criteria=list(state.detail.acceptance_criteria),
                        user_notes=list(state.request.user_notes),
                        latest_user_input=latest_user_input,
                        shared_goal_path=str(self.store.shared_goal_path(session_id)),
                        latest_codex_result=latest_codex,
                        remaining_turns=self.cfg.max_turns - local_turn + 1,
                        configured_max_turns=self.cfg.max_turns,
                        max_turns_cap=self.cfg.max_turns_cap,
                        system_prompt=self._gemini_system_prompt(),
                    ).compact().model_dump(mode="json"),
                    GeminiResponse,
                )
                store.save_gemini(local_turn, gemini_exchange)

                revised_goal = gemini_resp.revised_goal.strip()
                goal_changed = bool(revised_goal and revised_goal != state.request.goal.strip())
                if goal_changed:
                    state.request.goal = revised_goal
                    self.store.save_request(session_id, state.request)
                    store.save_job_request(
                        JobRequest(
                            **state.request.model_dump(mode="json"),
                            session_id=session_id,
                        )
                    )
                    completed_criteria = []
                    resolved_criteria = self._criteria_for_revised_goal(revised_goal, gemini_resp.acceptance_criteria)
                else:
                    resolved_criteria = merge_unique_items(
                        state.detail.acceptance_criteria,
                        state.request.acceptance_criteria,
                        gemini_resp.acceptance_criteria,
                    )
                if not resolved_criteria:
                    resolved_criteria = derive_acceptance_criteria(state.request.goal)
                state.request.acceptance_criteria = list(resolved_criteria)
                completed_criteria = merge_unique_items(
                    completed_criteria,
                    gemini_resp.completed_acceptance_criteria,
                )
                completed_criteria = self._filter_completed_criteria(completed_criteria, resolved_criteria)
                verdict = resolve_session_verdict(gemini_resp)
                gemini_plan = gemini_resp.gemini_plan.strip() or gemini_resp.summary_for_user.strip()
                gemini_review = gemini_resp.review_notes.strip() or gemini_resp.reason.strip() or gemini_resp.summary_for_user.strip()
                next_action = gemini_resp.next_action.strip() or gemini_resp.question_for_user.strip() or gemini_resp.instruction_for_codex.strip()
                self.mcp_service.session_write_gemini_sections(
                    session_id=session_id,
                    goal=revised_goal,
                    gemini_plan=gemini_plan,
                    gemini_review=gemini_review,
                    next_action=next_action,
                    acceptance_criteria=resolved_criteria,
                    completed_acceptance_criteria=completed_criteria,
                    verdict=verdict.value,
                    status=gemini_state.value,
                    replace_goal_context=goal_changed,
                )
                self._refresh_session_detail(state)
                self._audit(state, "gemini", f"turn {global_turn} completed with verdict={verdict.value}")

                if verdict == SessionVerdict.DONE:
                    completed_criteria = self._completed_criteria_for_done(resolved_criteria, completed_criteria)
                    final_status = FinalStatus.DONE
                    final_reason = gemini_resp.reason.strip() or "gemini marked the goal complete"
                    final_summary = gemini_resp.summary_for_user.strip() or next_action
                    self.mcp_service.session_write_gemini_sections(
                        session_id=session_id,
                        final_outcome=final_summary,
                        acceptance_criteria=resolved_criteria,
                        completed_acceptance_criteria=completed_criteria,
                        verdict=SessionVerdict.DONE.value,
                        status=SessionState.COMPLETED.value,
                    )
                    break
                if verdict == SessionVerdict.ASK_USER:
                    final_status = FinalStatus.WAITING_USER
                    final_reason = gemini_resp.reason.strip() or "gemini requested user input"
                    final_summary = gemini_resp.question_for_user.strip() or gemini_resp.summary_for_user.strip() or next_action
                    self.mcp_service.session_write_gemini_sections(
                        session_id=session_id,
                        next_action=final_summary,
                        verdict=SessionVerdict.ASK_USER.value,
                        status=SessionState.WAITING_USER.value,
                    )
                    break
                if verdict == SessionVerdict.FAIL:
                    final_status = FinalStatus.GEMINI_FAILED
                    final_reason = gemini_resp.reason.strip() or "gemini marked the session as failed"
                    final_summary = gemini_resp.summary_for_user.strip() or next_action
                    self.mcp_service.session_write_gemini_sections(
                        session_id=session_id,
                        final_outcome=final_summary,
                        verdict=SessionVerdict.FAIL.value,
                        status=SessionState.FAILED.value,
                    )
                    break

                instruction = gemini_resp.instruction_for_codex.strip() or gemini_resp.next_action.strip() or latest_codex.next_step.strip()
                if not instruction:
                    final_status = FinalStatus.GEMINI_FAILED
                    final_reason = "gemini returned continue without an instruction for codex"
                    final_summary = gemini_resp.summary_for_user.strip()
                    self.mcp_service.session_write_gemini_sections(
                        session_id=session_id,
                        final_outcome=final_summary,
                        verdict=SessionVerdict.FAIL.value,
                        status=SessionState.FAILED.value,
                    )
                    break

                self.mcp_service.session_set_verdict(
                    session_id=session_id,
                    verdict=SessionVerdict.CONTINUE.value,
                    status=SessionState.EXECUTING.value,
                    active_run_id=run_id,
                )
                codex_resp, codex_exchange = self.runtime.codex.execute(
                    CodexRequest(
                        project_path=state.request.workspace_path,
                        instruction_for_codex=instruction,
                        execution_policy=self.cfg.execution_policy,
                        commands=self._validated_commands(list(self.cfg.codex.default_commands)),
                        system_prompt=self._codex_system_prompt(),
                        session_id=session_id,
                        shared_goal_path=str(self.store.shared_goal_path(session_id)),
                        acceptance_criteria=list(state.detail.acceptance_criteria),
                        latest_gemini_next_action=next_action,
                        mcp_server=self.mcp_config,
                        previous_response_id=previous_response_id,
                        conversation_key=conversation_key,
                        thread_id=thread_id,
                    ).model_dump(mode="json"),
                    CodexResult,
                )
                previous_response_id = codex_exchange.execution.provider_response_id or previous_response_id
                thread_id = codex_exchange.execution.provider_thread_id or thread_id
                if conversation_key:
                    if codex_exchange.execution.provider_response_id:
                        self.sessions.save_previous_response_id(conversation_key, codex_exchange.execution.provider_response_id)
                    if codex_exchange.execution.provider_thread_id:
                        self.sessions.save_thread_id(conversation_key, codex_exchange.execution.provider_thread_id)
                store.save_codex(local_turn, codex_exchange)
                self._audit(state, "codex", f"turn {global_turn} completed with status={codex_resp.status.value}")

                codex_completed_criteria = self._filter_completed_criteria(
                    codex_resp.verified_acceptance_criteria,
                    state.detail.acceptance_criteria,
                )
                completed_criteria = merge_unique_items(
                    completed_criteria,
                    codex_completed_criteria,
                )
                completed_criteria = self._filter_completed_criteria(completed_criteria, state.detail.acceptance_criteria)
                self.mcp_service.session_write_codex_sections(
                    session_id=session_id,
                    codex_plan=codex_resp.codex_plan.strip() or instruction,
                    codex_execution=self._format_codex_execution(codex_resp),
                    codex_verification=self._format_codex_verification(codex_resp),
                    completed_acceptance_criteria=codex_completed_criteria,
                )

                if codex_resp.status == CodexStatus.FAILED:
                    failures += 1

                turn_record = TurnRecord(
                    turn_number=global_turn,
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    gemini=gemini_resp,
                    codex=codex_resp,
                )
                local_turns.append(turn_record)
                state.detail.turns.append(turn_record)
                latest_codex = codex_resp
                rolling = update_rolling_summary(rolling, turn_record)
                rolling.codex_failures = failures
                store.save_summary(rolling)
                self._refresh_session_detail(state)

                if failures > self.cfg.max_codex_failures:
                    final_status = FinalStatus.CODEX_FAILURES_EXCEEDED
                    final_reason = f"codex failure threshold exceeded after turn {global_turn}"
                    final_summary = codex_resp.summary.strip() or gemini_resp.summary_for_user.strip()
                    self.mcp_service.session_write_gemini_sections(
                        session_id=session_id,
                        final_outcome=final_summary,
                        verdict=SessionVerdict.FAIL.value,
                        status=SessionState.FAILED.value,
                    )
                    break

            if final_status is None:
                final_status = FinalStatus.MAX_TURNS_EXCEEDED
                final_reason = "maximum turns exceeded before a Gemini verdict"
                final_summary = state.detail.turns[-1].gemini.summary_for_user if state.detail.turns else ""
                self.mcp_service.session_write_gemini_sections(
                    session_id=session_id,
                    final_outcome=final_summary,
                    verdict=SessionVerdict.FAIL.value,
                    status=SessionState.FAILED.value,
                )

            finished_at = utc_now()
            result = FinalResult(
                status=final_status,
                reason=final_reason,
                turns=local_turns,
                final_summary=final_summary,
                project_path=state.request.workspace_path,
                run_directory=str(store.run_dir),
                started_at=started_at,
                finished_at=finished_at,
                codex_failures=failures,
                completed_acceptance_criteria=completed_criteria,
            )
            metadata.finished_at = finished_at
            metadata.status = final_status
            metadata.reason = final_reason
            metadata.codex_failures = failures
            metadata.turns_completed = len(local_turns)
            store.save_metadata(metadata)
            report_path = store.save_final_report(generate_final_report(result, rolling))

            job_summary = JobSummary(
                job_id=run_id,
                state=self._job_state_for_final_status(final_status),
                goal=state.request.goal,
                session_id=session_id,
                conversation_id=state.request.conversation_id,
                created_at=started_at,
                started_at=started_at,
                finished_at=finished_at,
                final_status=final_status,
                final_summary=final_summary,
                run_directory=str(store.run_dir),
            )
            latest_job = JobDetail(
                summary=job_summary,
                request=JobRequest(**state.request.model_dump(mode="json"), session_id=session_id),
                result=result,
                rolling_summary=rolling,
                audit_log=list(state.detail.latest_job.audit_log if state.detail.latest_job else []),
                report_path=report_path,
            )
            state.detail.latest_job = latest_job
            state.detail.completed_acceptance_criteria = completed_criteria

            session_state, session_verdict = self._session_status_for_final_status(final_status)
            state.summary.state = session_state
            state.summary.verdict = session_verdict
            state.summary.active_run_id = run_id
            state.summary.updated_at = finished_at
            if final_status != FinalStatus.WAITING_USER:
                state.summary.final_summary = final_summary
            self._audit(state, "run", f"finished run {run_id} with status={final_status.value}")
            self._refresh_session_detail(state)
            return state.detail
        except Exception as exc:  # noqa: BLE001
            self._log_exception(
                "session_runtime_failed",
                exc,
                session_id=session_id,
                run_id=run_id,
                channel=state.request.channel,
                conversation_id=state.request.conversation_id,
                workspace_path=state.request.workspace_path,
            )
            finished_at = utc_now()
            result = FinalResult(
                status=FinalStatus.RUNTIME_ERROR,
                reason=str(exc),
                turns=local_turns,
                final_summary="The worker session failed with a runtime error.",
                project_path=state.request.workspace_path,
                run_directory=str(store.run_dir),
                started_at=started_at,
                finished_at=finished_at,
                codex_failures=failures,
                completed_acceptance_criteria=completed_criteria,
            )
            metadata.finished_at = finished_at
            metadata.status = FinalStatus.RUNTIME_ERROR
            metadata.reason = str(exc)
            metadata.codex_failures = failures
            metadata.turns_completed = len(local_turns)
            store.save_metadata(metadata)
            report_path = store.save_final_report(generate_final_report(result, rolling))
            job_summary = JobSummary(
                job_id=run_id,
                state=JobState.FAILED,
                goal=state.request.goal,
                session_id=session_id,
                conversation_id=state.request.conversation_id,
                created_at=started_at,
                started_at=started_at,
                finished_at=finished_at,
                final_status=FinalStatus.RUNTIME_ERROR,
                final_summary=result.final_summary,
                run_directory=str(store.run_dir),
            )
            state.detail.latest_job = JobDetail(
                summary=job_summary,
                request=JobRequest(**state.request.model_dump(mode="json"), session_id=session_id),
                result=result,
                rolling_summary=rolling,
                audit_log=list(state.detail.latest_job.audit_log if state.detail.latest_job else []),
                report_path=report_path,
                error=str(exc),
            )
            self.mcp_service.session_write_gemini_sections(
                session_id=session_id,
                final_outcome=result.final_summary,
                verdict=SessionVerdict.FAIL.value,
                status=SessionState.FAILED.value,
            )
            state.summary.state = SessionState.FAILED
            state.summary.verdict = SessionVerdict.FAIL
            state.detail.error = str(exc)
            self._audit(state, "run", f"failed with error={exc}")
            self._refresh_session_detail(state)
            return state.detail

    def _refresh_session_detail(self, state: SessionRuntimeState) -> None:
        session_read = self.mcp_service.session_read(state.summary.session_id)
        state.detail.shared_goal_markdown = session_read["shared_goal_markdown"]
        state.detail.acceptance_criteria = list(session_read["sections"]["acceptance_criteria"])
        state.detail.completed_acceptance_criteria = list(session_read["sections"]["completed_acceptance_criteria"])
        state.detail.user_notes = list(session_read["sections"]["user_notes"])
        state.detail.gemini_plan = session_read["sections"]["gemini_plan"]
        state.detail.codex_plan = session_read["sections"]["codex_plan"]
        state.detail.codex_execution = session_read["sections"]["codex_execution"]
        state.detail.codex_verification = session_read["sections"]["codex_verification"]
        state.detail.gemini_review = session_read["sections"]["gemini_review"]
        state.detail.next_action = session_read["sections"]["next_action"]
        state.detail.final_outcome = session_read["sections"]["final_outcome"]
        summary = SessionSummary.model_validate(session_read["summary"])
        state.summary = summary
        state.detail.summary = summary

    @staticmethod
    def _audit(state: SessionRuntimeState, stage: str, message: str) -> None:
        if state.detail.latest_job:
            state.detail.latest_job.audit_log.append(AuditEvent(stage=stage, message=message))

    @staticmethod
    def _log_exception(action: str, exc: Exception, **context) -> None:  # noqa: ANN003
        merged = {"error_type": exc.__class__.__name__, **context}
        logger.exception("%s | %s", action, WorkerOrchestrator._log_context(merged))

    @staticmethod
    def _log_context(context: dict[str, object]) -> str:
        parts = [f"{key}={value}" for key, value in context.items() if value not in {None, ""}]
        return " ".join(parts)

    def _resolve_workspace(self, requested_path: str) -> str:
        requested = Path(requested_path)
        if requested.is_absolute():
            return str(requested)
        return str((Path(self.cfg.workspace_root) / requested).resolve())

    @staticmethod
    def _conversation_key(request: SessionRequest) -> str:
        channel = request.channel.strip()
        conversation_id = request.conversation_id.strip()
        if not channel or not conversation_id:
            return ""
        return f"{channel}:{conversation_id}"

    def _validated_commands(self, commands: list[str]) -> list[str]:
        allow = self.cfg.execution_policy.allow_commands
        deny = self.cfg.execution_policy.deny_commands
        for command in commands:
            prefix = command.split()[0]
            if deny and prefix in deny:
                raise RuntimeError(f"command '{prefix}' is denied by execution policy")
            if allow and prefix not in allow:
                raise RuntimeError(f"command '{prefix}' is not allowed by execution policy")
        return commands

    @staticmethod
    def _criteria_for_revised_goal(revised_goal: str, gemini_criteria: list[str]) -> list[str]:
        criteria = merge_unique_items(gemini_criteria)
        if criteria:
            return criteria
        return derive_acceptance_criteria(revised_goal)

    @staticmethod
    def _filter_completed_criteria(completed: list[str], acceptance_criteria: list[str]) -> list[str]:
        if not acceptance_criteria:
            return merge_unique_items(completed)
        accepted_by_key = {item.casefold(): item for item in acceptance_criteria}
        filtered: list[str] = []
        seen: set[str] = set()
        for item in completed:
            key = item.strip().casefold()
            if not key or key in seen or key not in accepted_by_key:
                continue
            seen.add(key)
            filtered.append(accepted_by_key[key])
        return filtered

    @classmethod
    def _completed_criteria_for_done(cls, acceptance_criteria: list[str], completed: list[str]) -> list[str]:
        filtered = cls._filter_completed_criteria(completed, acceptance_criteria)
        if not acceptance_criteria:
            return filtered
        return merge_unique_items(filtered, acceptance_criteria)

    @staticmethod
    def _gemini_system_prompt() -> str:
        return (
            "You are the session planner and reviewer. Read the shared session document before every turn and keep the "
            "user's literal goal at the center of your decisions. All user-facing fields you write "
            "(summary_for_user, gemini_plan, review_notes, next_action, question_for_user, reason) must match the "
            "user's language. If the user wrote in Korean, reply in natural Korean only and avoid mixed English except "
            "for unavoidable product names such as Gemini, Codex, GitHub, or LinkedIn. "
            "Every summary_for_user and next_action must explicitly reflect the real goal instead of generic task-management "
            "phrases. When you synthesize acceptance criteria, make them concrete, outcome-based, and specific to the goal. "
            "If the latest user message clearly changes the task, redirects scope, or asks for a different deliverable, "
            "set revised_goal and switch the session to that goal instead of forcing the previous one. "
            "If the user asks a direct conversational question about current capabilities, session state, or what Gemini, "
            "Codex, or MCP can do, answer it directly in the user's language and prefer a final response instead of "
            "sending Codex to code. In this system, MCP is the structured tool bridge used to read and update shared "
            "session state such as shared_goal.md, so do not describe MCP as unknown or unconfirmed. "
            "Treat latest_user_input as the user's newest answer to your previous question. Do not ask again for "
            "information that appears in latest_user_input or user_notes; merge partial answers across turns. "
            "When a user provides a concrete value such as a folder name or path, proceed with that value and use the "
            "workspace root as the default location unless the request explicitly says otherwise. "
            "If you need user input, ask only for the minimum missing information required for the next step, and format "
            "question_for_user so the gateway can show it as a short introduction followed by concise bullet-ready items. "
            "Return exactly one verdict: continue, done, ask_user, or fail. Only use ask_user when Codex truly cannot "
            "continue safely without that missing input."
        )

    @staticmethod
    def _codex_system_prompt() -> str:
        return (
            "You are the execution engine for the current session. Use the shared session document and MCP server context, "
            "implement the requested change, verify it, and report whether the acceptance criteria appear satisfied. "
            "All user-facing text you produce (summary, next_step, verification_notes, codex_plan) must match the user's "
            "language. If the user wrote in Korean, respond in natural Korean and avoid generic English boilerplate. "
            "Anchor your summary to the actual goal, what you changed, what was verified, and what is still missing. "
            "Do not decide final completion; Gemini owns the final verdict."
        )

    @staticmethod
    def _format_codex_execution(result: CodexResult) -> str:
        lines = []
        if result.summary.strip():
            lines.append(result.summary.strip())
        if result.changed_files:
            lines.append("변경 파일: " + ", ".join(result.changed_files))
        if result.commands_run:
            lines.append("실행 명령: " + ", ".join(result.commands_run))
        if result.next_step.strip():
            lines.append("다음 제안: " + result.next_step.strip())
        return "\n".join(lines)

    @staticmethod
    def _format_codex_verification(result: CodexResult) -> str:
        lines = []
        if result.verification_notes.strip():
            lines.append(result.verification_notes.strip())
        if result.command_results:
            for item in result.command_results:
                lines.append(f"{item.command}: 종료 코드 {item.exit_code}")
        if result.proposed_completion:
            lines.append("Codex 판단: 현재 구현은 Gemini 검토 단계로 넘길 준비가 되었습니다.")
        return "\n".join(lines)

    @staticmethod
    def _job_state_for_final_status(status: FinalStatus) -> JobState:
        if status == FinalStatus.CANCELED:
            return JobState.CANCELED
        if status == FinalStatus.WAITING_USER:
            return JobState.WAITING_USER
        if status in {FinalStatus.GEMINI_FAILED, FinalStatus.RUNTIME_ERROR, FinalStatus.CODEX_FAILURES_EXCEEDED, FinalStatus.MAX_TURNS_EXCEEDED}:
            return JobState.FAILED
        return JobState.COMPLETED

    @staticmethod
    def _session_status_for_final_status(status: FinalStatus) -> tuple[SessionState, SessionVerdict]:
        if status == FinalStatus.CANCELED:
            return SessionState.CANCELED, SessionVerdict.FAIL
        if status == FinalStatus.WAITING_USER:
            return SessionState.WAITING_USER, SessionVerdict.ASK_USER
        if status in {FinalStatus.GEMINI_FAILED, FinalStatus.RUNTIME_ERROR, FinalStatus.CODEX_FAILURES_EXCEEDED, FinalStatus.MAX_TURNS_EXCEEDED}:
            return SessionState.FAILED, SessionVerdict.FAIL
        return SessionState.COMPLETED, SessionVerdict.DONE


class SessionManager:
    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg
        self.orchestrator = WorkerOrchestrator(cfg)
        self.sessions: dict[str, SessionRuntimeState] = {}
        self._lock = Lock()
        self._load_existing_sessions()

    def startup(self) -> None:
        self.orchestrator.start()

    def shutdown(self) -> None:
        self.orchestrator.shutdown()

    def create_session(self, request: SessionRequest) -> SessionSummary:
        with self._lock:
            request = self._with_recent_conversation_context(request)
            active_session_id = self.orchestrator.store.get_active_session(request.channel, request.conversation_id)
            if active_session_id:
                self._supersede_session(active_session_id, request.channel, request.conversation_id)
            session_id = utc_now().strftime("%Y%m%d-%H%M%S-%f")
            document = self.orchestrator.store.create_session(session_id, request, self.cfg.gemini.model or "gemini-2.5-flash")
            summary = document.to_summary(request, str(self.orchestrator.store.shared_goal_path(session_id)))
            detail = SessionDetail(
                summary=summary,
                request=request,
                acceptance_criteria=list(document.acceptance_criteria),
                completed_acceptance_criteria=list(document.completed_acceptance_criteria),
                user_notes=list(document.user_notes),
                gemini_plan=document.gemini_plan,
                codex_plan=document.codex_plan,
                codex_execution=document.codex_execution,
                codex_verification=document.codex_verification,
                gemini_review=document.gemini_review,
                shared_goal_markdown=document.render_markdown(),
            )
            runtime = SessionRuntimeState(summary=summary, request=request, detail=detail)
            self.sessions[session_id] = runtime
            self._start_background_processing(runtime)
            return runtime.summary

    def _with_recent_conversation_context(self, request: SessionRequest) -> SessionRequest:
        context_note = self._recent_conversation_context_note(request)
        if not context_note:
            return request
        marker = "[recent conversation context]"
        if any(note.startswith(marker) for note in request.user_notes):
            return request
        return request.model_copy(update={"user_notes": [*request.user_notes, context_note]})

    def _recent_conversation_context_note(self, request: SessionRequest, limit: int = 3) -> str:
        if not request.channel or not request.conversation_id:
            return ""
        candidates = [
            runtime
            for runtime in self.sessions.values()
            if runtime.summary.channel == request.channel and runtime.summary.conversation_id == request.conversation_id
        ]
        candidates.sort(key=lambda item: item.summary.updated_at, reverse=True)
        lines: list[str] = []
        for runtime in candidates[:limit]:
            summary = runtime.summary
            detail = runtime.detail
            parts = [
                f"session={summary.session_id}",
                f"state={summary.state.value}",
                f"verdict={summary.verdict.value if summary.verdict else ''}",
                f"goal={truncate_text(summary.goal, 180)}",
            ]
            if detail.final_outcome:
                parts.append(f"final={truncate_text(detail.final_outcome, 220)}")
            elif summary.final_summary:
                parts.append(f"final={truncate_text(summary.final_summary, 220)}")
            if detail.next_action:
                parts.append(f"next={truncate_text(detail.next_action, 220)}")
            if detail.codex_execution:
                parts.append(f"codex={truncate_text(detail.codex_execution, 220)}")
            if detail.user_notes:
                parts.append(f"user_notes={truncate_text(' | '.join(detail.user_notes[-2:]), 220)}")
            lines.append("- " + "; ".join(item for item in parts if item))
        if not lines:
            return ""
        return "\n".join(
            [
                "[recent conversation context]",
                "Use this as prior conversation context. The latest user message remains authoritative.",
                *lines,
            ]
        )

    def list_sessions(
        self,
        channel: str | None = None,
        conversation_id: str | None = None,
        active_only: bool = False,
        updated_after: datetime | None = None,
    ) -> list[SessionSummary]:
        summaries = [runtime.summary for runtime in self.sessions.values()]
        if channel:
            summaries = [item for item in summaries if item.channel == channel]
        if conversation_id:
            summaries = [item for item in summaries if item.conversation_id == conversation_id]
        if active_only:
            summaries = [item for item in summaries if item.state in {SessionState.PLANNING, SessionState.EXECUTING, SessionState.REVIEWING, SessionState.WAITING_USER}]
        if updated_after:
            summaries = [item for item in summaries if item.updated_at > updated_after]
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def get_session(self, session_id: str) -> SessionDetail:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        return self.sessions[session_id].detail

    def continue_session(self, session_id: str, request: SessionContinueRequest) -> SessionSummary:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        runtime = self.sessions[session_id]
        text = request.text.strip()
        if text:
            runtime.request.user_notes.append(text)
        if request.attachments:
            runtime.request.attachments.extend(request.attachments)
        self.orchestrator.mcp_service.session_update_user_input(
            session_id=session_id,
            text=text,
            attachments=[item.model_dump(mode="json") for item in request.attachments],
        )
        self.orchestrator.store.save_request(session_id, runtime.request)
        self.orchestrator._refresh_session_detail(runtime)
        if runtime.summary.state == SessionState.WAITING_USER or not runtime.processing:
            runtime.cancel_event = Event()
            self._start_background_processing(runtime)
        return runtime.summary

    def cancel_session(self, session_id: str) -> SessionSummary:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        runtime = self.sessions[session_id]
        runtime.cancel_event.set()
        runtime.summary.state = SessionState.CANCELED
        runtime.summary.verdict = SessionVerdict.FAIL
        runtime.summary.updated_at = utc_now()
        self.orchestrator.mcp_service.session_write_gemini_sections(
            session_id=session_id,
            final_outcome="The session was canceled by the user.",
            verdict=SessionVerdict.FAIL.value,
            status=SessionState.CANCELED.value,
        )
        self.orchestrator.store.set_active_session(runtime.summary.channel, runtime.summary.conversation_id, None)
        self.orchestrator._refresh_session_detail(runtime)
        return runtime.summary

    def list_jobs(self) -> list[JobSummary]:
        jobs = [runtime.detail.latest_job.summary for runtime in self.sessions.values() if runtime.detail.latest_job is not None]
        return sorted(jobs, key=lambda item: item.created_at, reverse=True)

    def get_job(self, job_id: str) -> JobDetail:
        for runtime in self.sessions.values():
            if runtime.detail.latest_job and runtime.detail.latest_job.summary.job_id == job_id:
                return runtime.detail.latest_job
            if runtime.summary.session_id == job_id and runtime.detail.latest_job is not None:
                return runtime.detail.latest_job
        raise KeyError(job_id)

    def cancel_job(self, job_id: str) -> JobSummary:
        detail = self.get_job(job_id)
        self.cancel_session(detail.summary.session_id)
        return self.get_job(detail.summary.job_id).summary

    def create_job(self, request: JobRequest) -> JobSummary:
        session_summary = self.create_session(
            SessionRequest(
                goal=request.goal,
                requester_id=request.requester_id,
                workspace_path=request.workspace_path,
                channel=request.channel,
                conversation_id=request.conversation_id,
                constraints=list(request.constraints),
                acceptance_criteria=list(request.acceptance_criteria),
                user_notes=list(request.user_notes),
                text_only=request.text_only,
                requires_private_network=request.requires_private_network,
                attachments=list(request.attachments),
            )
        )
        runtime = self.sessions[session_summary.session_id]
        return JobSummary(
            job_id=session_summary.active_run_id or session_summary.session_id,
            state=JobState.RUNNING,
            goal=session_summary.goal,
            session_id=session_summary.session_id,
            conversation_id=session_summary.conversation_id,
            created_at=session_summary.created_at,
        )

    def health(self) -> dict[str, Any]:
        active = next((runtime for runtime in self.sessions.values() if runtime.processing), None)
        return {
            "status": "ok",
            "active_job_id": active.detail.latest_job.summary.job_id if active and active.detail.latest_job else None,
            "active_session_id": active.summary.session_id if active else None,
        }

    def _start_background_processing(self, runtime: SessionRuntimeState) -> None:
        if runtime.processing:
            return
        runtime.processing = True
        thread = Thread(target=self._run_in_background, args=(runtime.summary.session_id,), daemon=True)
        thread.start()

    def _run_in_background(self, session_id: str) -> None:
        runtime = self.sessions[session_id]
        runtime.detail = self.orchestrator.process_session(runtime)
        runtime.processing = False

    def _supersede_session(self, session_id: str, channel: str = "", conversation_id: str = "") -> None:
        if session_id not in self.sessions:
            if channel and conversation_id:
                self.orchestrator.store.set_active_session(channel, conversation_id, None)
            return
        runtime = self.sessions[session_id]
        if runtime.summary.state.is_terminal:
            self.orchestrator.store.set_active_session(runtime.summary.channel, runtime.summary.conversation_id, None)
            return
        runtime.cancel_event.set()
        runtime.summary.state = SessionState.CANCELED
        runtime.summary.verdict = SessionVerdict.FAIL
        self.orchestrator.store.mark_superseded(session_id, runtime.request, "Superseded by a newer /run request in the same conversation.")
        self.orchestrator._refresh_session_detail(runtime)

    def _load_existing_sessions(self) -> None:
        for summary in self.orchestrator.store.list_recent(limit=500):
            request = self.orchestrator.store.load_request(summary.session_id)
            document = self.orchestrator.store.load_document(summary.session_id)
            detail = SessionDetail(
                summary=summary,
                request=request,
                acceptance_criteria=list(document.acceptance_criteria),
                completed_acceptance_criteria=list(document.completed_acceptance_criteria),
                user_notes=list(document.user_notes),
                gemini_plan=document.gemini_plan,
                codex_plan=document.codex_plan,
                codex_execution=document.codex_execution,
                codex_verification=document.codex_verification,
                gemini_review=document.gemini_review,
                next_action=document.next_action,
                final_outcome=document.final_outcome,
                shared_goal_markdown=self.orchestrator.store.shared_goal_path(summary.session_id).read_text(encoding="utf-8"),
            )
            self.sessions[summary.session_id] = SessionRuntimeState(summary=summary, request=request, detail=detail)


JobManager = SessionManager
