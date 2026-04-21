from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from telecodex.shared.cli import JsonCliAdapter
from telecodex.shared.config import WorkerConfig
from telecodex.shared.models import (
    AdapterExchange,
    AuditEvent,
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
    TurnRecord,
    codex_terminal_reason,
    generate_final_report,
    planner_bootstrap_goal,
    update_rolling_summary,
    utc_now,
)
from telecodex.worker.store import FileRunStore


@dataclass
class OrchestrationRuntime:
    cfg: WorkerConfig
    gemini: JsonCliAdapter
    codex: JsonCliAdapter


@dataclass
class JobRuntimeState:
    summary: JobSummary
    request: JobRequest
    detail: JobDetail
    cancel_event: Event = field(default_factory=Event)


class WorkerOrchestrator:
    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg
        self.runtime = OrchestrationRuntime(
            cfg=cfg,
            gemini=JsonCliAdapter("gemini", cfg.gemini, cfg.dry_run),
            codex=JsonCliAdapter("codex", cfg.codex, cfg.dry_run),
        )

    def run_job(self, state: JobRuntimeState) -> JobDetail:
        job_id = state.summary.job_id
        run_id = job_id
        store = FileRunStore(self.cfg.runs_dir, run_id)
        started_at = utc_now()
        effective_workspace = self._resolve_workspace(state.request.workspace_path)
        state.request.workspace_path = effective_workspace
        state.summary.state = JobState.RUNNING
        state.summary.started_at = started_at
        state.summary.run_directory = str(store.run_dir)
        state.detail.summary = state.summary
        self._audit(state, "run", "starting worker orchestration")

        metadata = RunMetadata(
            run_id=run_id,
            project_path=state.request.workspace_path,
            goal=state.request.goal,
            max_turns=self.cfg.max_turns,
            effective_max_turns=self.cfg.max_turns,
            max_codex_failures=self.cfg.max_codex_failures,
            started_at=started_at,
        )
        store.save_metadata(metadata)

        rolling = RollingSummary()
        latest_codex = CodexResult()
        turns: list[TurnRecord] = []
        failures = 0
        final_status: FinalStatus | None = None
        final_reason = ""
        final_summary = ""
        effective_max_turns = self.cfg.max_turns

        try:
            for turn in range(1, effective_max_turns + 1):
                if state.cancel_event.is_set():
                    final_status = FinalStatus.CANCELED
                    final_reason = "job canceled by user"
                    final_summary = "The job was canceled before completion."
                    break

                if turn == 1 and not self.cfg.dynamic_turn_budget:
                    gemini_resp = GeminiResponse(
                        status=GeminiStatus.CONTINUE,
                        summary_for_user="Starting Codex from the initial user goal.",
                        instruction_for_codex=state.request.goal,
                        reason="bootstrap from user goal",
                    )
                    gemini_exchange = self._synthetic_exchange(gemini_resp)
                else:
                    state.summary.state = JobState.WAITING_REVIEW
                    gemini_payload = GeminiRequest(
                        user_goal=planner_bootstrap_goal(state.request.goal) if turn == 1 else "",
                        current_summary=rolling.current_summary,
                        latest_codex_result=latest_codex,
                        remaining_turns=effective_max_turns - turn + 1,
                        configured_max_turns=self.cfg.max_turns,
                        max_turns_cap=self.cfg.max_turns_cap,
                    ).compact()
                    gemini_resp, gemini_exchange = self.runtime.gemini.execute(
                        gemini_payload.model_dump(mode="json"),
                        GeminiResponse,
                    )
                store.save_gemini(turn, gemini_exchange)
                self._audit(state, "gemini", f"turn {turn} completed with status={gemini_resp.status.value}")

                if gemini_resp.status == GeminiStatus.FAILED:
                    final_status = FinalStatus.GEMINI_FAILED
                    final_reason = gemini_resp.reason.strip()
                    final_summary = gemini_resp.summary_for_user.strip()
                    break
                if gemini_resp.status == GeminiStatus.DONE:
                    final_status = FinalStatus.DONE
                    final_reason = gemini_resp.reason.strip() or "gemini finished the run"
                    final_summary = gemini_resp.summary_for_user.strip()
                    break

                instruction = gemini_resp.instruction_for_codex.strip() or latest_codex.next_step.strip()
                if not instruction:
                    final_status = FinalStatus.GEMINI_FAILED
                    final_reason = "gemini returned continue without response text or next_step for codex"
                    final_summary = gemini_resp.summary_for_user.strip()
                    break

                state.summary.state = JobState.RUNNING
                codex_payload = CodexRequest(
                    project_path=state.request.workspace_path,
                    instruction_for_codex=instruction,
                    execution_policy=self.cfg.execution_policy,
                    commands=self._validated_commands(list(self.cfg.codex.default_commands)),
                )
                codex_resp, codex_exchange = self.runtime.codex.execute(
                    codex_payload.model_dump(mode="json"),
                    CodexResult,
                )
                store.save_codex(turn, codex_exchange)
                self._audit(state, "codex", f"turn {turn} completed with status={codex_resp.status.value}")

                if codex_resp.status == CodexStatus.FAILED:
                    failures += 1

                turn_record = TurnRecord(
                    turn_number=turn,
                    started_at=utc_now(),
                    finished_at=utc_now(),
                    gemini=gemini_resp,
                    codex=codex_resp,
                )
                turns.append(turn_record)
                latest_codex = codex_resp
                rolling = update_rolling_summary(rolling, turn_record)
                rolling.codex_failures = failures
                store.save_summary(rolling)

                if failures > self.cfg.max_codex_failures:
                    final_status = FinalStatus.CODEX_FAILURES_EXCEEDED
                    final_reason = f"codex failure threshold exceeded after turn {turn}"
                    final_summary = gemini_resp.summary_for_user.strip()
                    break
                if codex_resp.status.is_terminal_stop:
                    final_status = FinalStatus.DONE
                    final_reason = codex_terminal_reason(codex_resp.status, codex_resp.summary)
                    final_summary = codex_resp.summary.strip()
                    break

            if final_status is None:
                final_status = FinalStatus.MAX_TURNS_EXCEEDED
                final_reason = "maximum turns exceeded"
                final_summary = turns[-1].gemini.summary_for_user if turns else ""

            finished_at = utc_now()
            result = FinalResult(
                status=final_status,
                reason=final_reason,
                turns=turns,
                final_summary=final_summary,
                project_path=state.request.workspace_path,
                run_directory=str(store.run_dir),
                started_at=started_at,
                finished_at=finished_at,
                codex_failures=failures,
            )
            metadata.finished_at = finished_at
            metadata.status = final_status
            metadata.reason = final_reason
            metadata.codex_failures = failures
            metadata.turns_completed = len(turns)
            store.save_metadata(metadata)
            report_path = store.save_final_report(generate_final_report(result, rolling))

            state.summary.finished_at = finished_at
            state.summary.final_status = final_status
            state.summary.final_summary = result.final_summary
            state.summary.state = JobState.CANCELED if final_status == FinalStatus.CANCELED else (
                JobState.FAILED if final_status in {FinalStatus.GEMINI_FAILED, FinalStatus.RUNTIME_ERROR, FinalStatus.CODEX_FAILURES_EXCEEDED} else JobState.COMPLETED
            )
            state.detail = JobDetail(
                summary=state.summary,
                request=state.request,
                result=result,
                rolling_summary=rolling,
                audit_log=state.detail.audit_log,
                report_path=report_path,
            )
            self._audit(state, "run", f"finished with status={final_status.value}")
            return state.detail
        except Exception as exc:  # noqa: BLE001
            finished_at = utc_now()
            result = FinalResult(
                status=FinalStatus.RUNTIME_ERROR,
                reason=str(exc),
                turns=turns,
                final_summary="The worker run failed with a runtime error.",
                project_path=state.request.workspace_path,
                run_directory=str(store.run_dir),
                started_at=started_at,
                finished_at=finished_at,
                codex_failures=failures,
            )
            metadata.finished_at = finished_at
            metadata.status = FinalStatus.RUNTIME_ERROR
            metadata.reason = str(exc)
            metadata.codex_failures = failures
            metadata.turns_completed = len(turns)
            store.save_metadata(metadata)
            report_path = store.save_final_report(generate_final_report(result, rolling))
            state.summary.finished_at = finished_at
            state.summary.final_status = FinalStatus.RUNTIME_ERROR
            state.summary.final_summary = result.final_summary
            state.summary.state = JobState.FAILED
            self._audit(state, "run", f"failed with error={exc}")
            state.detail = JobDetail(
                summary=state.summary,
                request=state.request,
                result=result,
                rolling_summary=rolling,
                audit_log=state.detail.audit_log,
                report_path=report_path,
                error=str(exc),
            )
            return state.detail

    @staticmethod
    def _audit(state: JobRuntimeState, stage: str, message: str) -> None:
        state.detail.audit_log.append(AuditEvent(stage=stage, message=message))

    @staticmethod
    def _synthetic_exchange(response: GeminiResponse) -> AdapterExchange:
        now = utc_now()
        return AdapterExchange(
            request_json="{}",
            response_json=response.model_dump_json(indent=2),
            execution=CommandExecution(
                command="synthetic:gemini",
                args=[],
                stdout=response.model_dump_json(indent=2),
                stderr="",
                exit_code=0,
                duration_ms=0,
                started_at=now,
                finished_at=now,
            ),
        )

    def _resolve_workspace(self, requested_path: str) -> str:
        requested = Path(requested_path)
        if requested.is_absolute():
            return str(requested)
        return str((Path(self.cfg.workspace_root) / requested).resolve())

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


class JobManager:
    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg
        self.orchestrator = WorkerOrchestrator(cfg)
        self.jobs: dict[str, JobRuntimeState] = {}
        self.active_job_id: str | None = None
        self._lock = Lock()

    def create_job(self, request: JobRequest) -> JobSummary:
        with self._lock:
            if self.active_job_id:
                active = self.jobs[self.active_job_id]
                if active.summary.state in {JobState.QUEUED, JobState.RUNNING, JobState.WAITING_REVIEW}:
                    raise RuntimeError("another job is already active")
            job_id = utc_now().strftime("%Y%m%d-%H%M%S")
            summary = JobSummary(job_id=job_id, state=JobState.QUEUED, goal=request.goal, created_at=utc_now())
            detail = JobDetail(summary=summary, request=request)
            runtime = JobRuntimeState(summary=summary, request=request, detail=detail)
            self.jobs[job_id] = runtime
            self.active_job_id = job_id
            thread = Thread(target=self._run_in_background, args=(job_id,), daemon=True)
            thread.start()
            return summary

    def list_jobs(self) -> list[JobSummary]:
        return sorted((item.summary for item in self.jobs.values()), key=lambda item: item.created_at, reverse=True)

    def get_job(self, job_id: str) -> JobDetail:
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return self.jobs[job_id].detail

    def cancel_job(self, job_id: str) -> JobSummary:
        if job_id not in self.jobs:
            raise KeyError(job_id)
        runtime = self.jobs[job_id]
        runtime.cancel_event.set()
        return runtime.summary

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "active_job_id": self.active_job_id,
        }

    def _run_in_background(self, job_id: str) -> None:
        runtime = self.jobs[job_id]
        detail = self.orchestrator.run_job(runtime)
        runtime.detail = detail
        with self._lock:
            if self.active_job_id == job_id and detail.summary.state not in {JobState.QUEUED, JobState.RUNNING, JobState.WAITING_REVIEW}:
                self.active_job_id = None
