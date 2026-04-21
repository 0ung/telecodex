from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GeminiStatus(str, Enum):
    CONTINUE = "continue"
    DONE = "done"
    FAILED = "failed"


class CodexStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    WAITING_FOR_INPUT = "waiting_for_input"
    BLOCKED = "blocked"

    @property
    def is_terminal_stop(self) -> bool:
        return self in {
            CodexStatus.COMPLETED,
            CodexStatus.WAITING_FOR_INPUT,
            CodexStatus.BLOCKED,
        }


class FinalStatus(str, Enum):
    DONE = "done"
    GEMINI_FAILED = "gemini_failed"
    MAX_TURNS_EXCEEDED = "max_turns_exceeded"
    CODEX_FAILURES_EXCEEDED = "codex_failures_exceeded"
    RUNTIME_ERROR = "runtime_error"
    CANCELED = "canceled"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELED = "canceled"


class CommandResult(BaseModel):
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


class ExecutionPolicy(BaseModel):
    allow_commands: list[str] = Field(default_factory=list)
    deny_commands: list[str] = Field(default_factory=list)


class GeminiRequest(BaseModel):
    user_goal: str = ""
    current_summary: str = ""
    latest_codex_result: "CodexResult" = Field(default_factory=lambda: CodexResult())
    remaining_turns: int
    configured_max_turns: int | None = None
    max_turns_cap: int | None = None
    system_prompt: str = ""

    def compact(self) -> "GeminiRequest":
        compact = self.model_copy(deep=True)
        compact.user_goal = truncate_text(compact.user_goal, 400)
        compact.current_summary = truncate_text(compact.current_summary, 600)
        compact.latest_codex_result = compact.latest_codex_result.compact_for_gemini()
        return compact


class GeminiResponse(BaseModel):
    status: GeminiStatus
    summary_for_user: str = ""
    instruction_for_codex: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    reason: str = ""
    suggested_max_turns: int | None = None


class CodexRequest(BaseModel):
    project_path: str
    instruction_for_codex: str
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    commands: list[str] = Field(default_factory=list)
    system_prompt: str = ""


class CodexResult(BaseModel):
    status: CodexStatus = CodexStatus.SUCCESS
    changed_files: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    command_results: list[CommandResult] = Field(default_factory=list)
    summary: str = ""
    next_step: str = ""
    raw_output: str = ""

    def compact_for_gemini(self) -> "CodexResult":
        items = [
            CommandResult(
                command=truncate_text(item.command, 160),
                exit_code=item.exit_code,
                duration_ms=item.duration_ms,
            )
            for item in self.command_results[:4]
        ]
        return CodexResult(
            status=self.status,
            changed_files=[truncate_text(path, 200) for path in self.changed_files[:12]],
            commands_run=[truncate_text(cmd, 200) for cmd in self.commands_run[:6]],
            command_results=items,
            summary=truncate_text(self.summary, 800),
            next_step=truncate_text(self.next_step, 400),
        )


class CommandExecution(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)


class AdapterExchange(BaseModel):
    request_json: str
    response_json: str
    execution: CommandExecution


class TurnRecord(BaseModel):
    turn_number: int
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    gemini: GeminiResponse
    codex: CodexResult


class FinalResult(BaseModel):
    status: FinalStatus
    reason: str
    turns: list[TurnRecord] = Field(default_factory=list)
    final_summary: str = ""
    project_path: str
    run_directory: str
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime = Field(default_factory=utc_now)
    codex_failures: int = 0


class RollingSummary(BaseModel):
    current_summary: str = ""
    last_user_update: str = ""
    last_codex_state: str = ""
    completed_turns: int = 0
    codex_failures: int = 0
    updated_at: datetime = Field(default_factory=utc_now)


class RunMetadata(BaseModel):
    run_id: str
    project_path: str
    goal: str
    max_turns: int
    effective_max_turns: int
    max_codex_failures: int
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: FinalStatus | None = None
    reason: str = ""
    codex_failures: int = 0
    turns_completed: int = 0


class AuditEvent(BaseModel):
    stage: str
    message: str
    timestamp: datetime = Field(default_factory=utc_now)


class JobRequest(BaseModel):
    goal: str
    requester_id: int
    workspace_path: str
    text_only: bool = True
    requires_private_network: bool = True


class JobSummary(BaseModel):
    job_id: str
    state: JobState
    goal: str
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    final_status: FinalStatus | None = None
    final_summary: str = ""
    run_directory: str = ""


class JobDetail(BaseModel):
    summary: JobSummary
    request: JobRequest
    result: FinalResult | None = None
    rolling_summary: RollingSummary | None = None
    audit_log: list[AuditEvent] = Field(default_factory=list)
    report_path: str = ""
    error: str = ""


class JobListResponse(BaseModel):
    jobs: list[JobSummary] = Field(default_factory=list)


class JobCreateResponse(BaseModel):
    job_id: str
    state: JobState


class CancelResponse(BaseModel):
    job_id: str
    state: JobState
    accepted: bool


class HealthResponse(BaseModel):
    status: str
    active_job_id: str | None = None


def truncate_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def update_rolling_summary(existing: RollingSummary, turn: TurnRecord) -> RollingSummary:
    lines = [f"Turn {turn.turn_number}"]
    if turn.gemini.summary_for_user.strip():
        lines.append(f"Gemini: {turn.gemini.summary_for_user.strip()}")
    if turn.codex.summary.strip():
        lines.append(f"Codex: {turn.codex.summary.strip()}")
    if turn.codex.changed_files:
        lines.append("Changed files: " + ", ".join(turn.codex.changed_files))
    if turn.codex.next_step.strip():
        lines.append("Next step: " + turn.codex.next_step.strip())
    return RollingSummary(
        current_summary="\n".join(lines),
        last_user_update=turn.gemini.summary_for_user.strip(),
        last_codex_state=turn.codex.status.value,
        completed_turns=turn.turn_number,
        codex_failures=existing.codex_failures + (1 if turn.codex.status == CodexStatus.FAILED else 0),
        updated_at=utc_now(),
    )


def generate_final_report(result: FinalResult, rollup: RollingSummary | None) -> str:
    lines = [
        "# Orchestrator Final Report",
        "",
        f"- Status: `{result.status.value}`",
        f"- Reason: {result.reason or 'n/a'}",
        f"- Project Path: `{result.project_path}`",
        f"- Run Directory: `{result.run_directory}`",
        f"- Started: {result.started_at.isoformat()}",
        f"- Finished: {result.finished_at.isoformat()}",
        f"- Codex Failures: {result.codex_failures}",
        "",
        "## Final Summary",
        "",
        result.final_summary or "No final summary was produced.",
        "",
        "## Rolling Summary",
        "",
        f"```text\n{rollup.current_summary if rollup else 'No rolling summary available.'}\n```",
        "",
        "## Turns",
        "",
    ]
    for turn in result.turns:
        lines.extend(
            [
                f"### Turn {turn.turn_number}",
                "",
                f"- Gemini Status: `{turn.gemini.status.value}`",
                f"- Gemini Summary: {turn.gemini.summary_for_user or 'n/a'}",
                f"- Codex Status: `{turn.codex.status.value}`",
                f"- Codex Summary: {turn.codex.summary or 'n/a'}",
            ]
        )
        if turn.codex.next_step:
            lines.append(f"- Codex Next Step: {turn.codex.next_step}")
        if turn.codex.changed_files:
            lines.append("- Changed Files: " + ", ".join(turn.codex.changed_files))
        if turn.codex.commands_run:
            lines.append("- Commands Run: " + ", ".join(turn.codex.commands_run))
        lines.append("")
    return "\n".join(lines)


def planner_bootstrap_goal(goal: str) -> str:
    goal = goal.strip()
    if not goal:
        return ""
    parts = [part.strip() for part in goal.split(".") if part.strip()]
    filtered: list[str] = []
    for part in parts:
        lowered = part.lower()
        if "agents.md" in lowered or "cmd /c npm" in lowered or "do not inspect or modify telecodex" in lowered:
            continue
        if "c:/" in part or "c:\\" in part:
            continue
        filtered.append(part)
        if len(filtered) == 3:
            break
    return ". ".join(filtered) + "." if filtered else goal


def codex_terminal_reason(status: CodexStatus, summary: str) -> str:
    summary = summary.strip()
    if status == CodexStatus.COMPLETED:
        return f"codex completed the current goal: {summary}" if summary else "codex completed the current goal"
    if status == CodexStatus.WAITING_FOR_INPUT:
        return f"codex is waiting for user input: {summary}" if summary else "codex is waiting for user input"
    if status == CodexStatus.BLOCKED:
        return f"codex reported a blocker: {summary}" if summary else "codex reported a blocker"
    return summary


class MockAdapterResponse(BaseModel):
    status: str
    summary_for_user: str = ""
    instruction_for_codex: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    reason: str = ""
    suggested_max_turns: int | None = None
    changed_files: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    command_results: list[CommandResult] = Field(default_factory=list)
    summary: str = ""
    raw_output: str = ""
    next_step: str = ""

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


GeminiRequest.model_rebuild()
