from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionState(str, Enum):
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def is_active(self) -> bool:
        return self in {
            SessionState.PLANNING,
            SessionState.EXECUTING,
            SessionState.REVIEWING,
        }

    @property
    def is_terminal(self) -> bool:
        return self in {
            SessionState.COMPLETED,
            SessionState.FAILED,
            SessionState.CANCELED,
        }


class SessionVerdict(str, Enum):
    CONTINUE = "continue"
    DONE = "done"
    ASK_USER = "ask_user"
    FAIL = "fail"


class GeminiStatus(str, Enum):
    CONTINUE = "continue"
    DONE = "done"
    ASK_USER = "ask_user"
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
    WAITING_USER = "waiting_user"
    GEMINI_FAILED = "gemini_failed"
    MAX_TURNS_EXCEEDED = "max_turns_exceeded"
    CODEX_FAILURES_EXCEEDED = "codex_failures_exceeded"
    RUNTIME_ERROR = "runtime_error"
    CANCELED = "canceled"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    WAITING_USER = "waiting_user"
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


class SessionMcpConfig(BaseModel):
    base_url: str
    token: str


class GeminiRequest(BaseModel):
    session_id: str = ""
    user_goal: str = ""
    current_summary: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    user_notes: list[str] = Field(default_factory=list)
    shared_goal_path: str = ""
    latest_codex_result: "CodexResult" = Field(default_factory=lambda: CodexResult())
    remaining_turns: int
    configured_max_turns: int | None = None
    max_turns_cap: int | None = None
    system_prompt: str = ""

    def compact(self) -> "GeminiRequest":
        compact = self.model_copy(deep=True)
        compact.user_goal = truncate_text(compact.user_goal, 400)
        compact.current_summary = truncate_text(compact.current_summary, 800)
        compact.acceptance_criteria = [truncate_text(item, 200) for item in compact.acceptance_criteria[:8]]
        compact.user_notes = [truncate_text(item, 200) for item in compact.user_notes[-8:]]
        compact.latest_codex_result = compact.latest_codex_result.compact_for_gemini()
        return compact


class GeminiResponse(BaseModel):
    status: GeminiStatus
    summary_for_user: str = ""
    instruction_for_codex: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    completed_acceptance_criteria: list[str] = Field(default_factory=list)
    verdict: SessionVerdict | None = None
    gemini_plan: str = ""
    review_notes: str = ""
    next_action: str = ""
    question_for_user: str = ""
    reason: str = ""
    suggested_max_turns: int | None = None


class CodexRequest(BaseModel):
    project_path: str
    instruction_for_codex: str
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    commands: list[str] = Field(default_factory=list)
    system_prompt: str = ""
    session_id: str = ""
    shared_goal_path: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    latest_gemini_next_action: str = ""
    mcp_server: SessionMcpConfig | None = None
    previous_response_id: str = ""
    conversation_key: str = ""
    thread_id: str = ""


class CodexResult(BaseModel):
    status: CodexStatus = CodexStatus.SUCCESS
    changed_files: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    command_results: list[CommandResult] = Field(default_factory=list)
    summary: str = ""
    next_step: str = ""
    raw_output: str = ""
    codex_plan: str = ""
    verification_notes: str = ""
    verified_acceptance_criteria: list[str] = Field(default_factory=list)
    proposed_completion: bool = False

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
            codex_plan=truncate_text(self.codex_plan, 500),
            verification_notes=truncate_text(self.verification_notes, 500),
            verified_acceptance_criteria=[truncate_text(item, 200) for item in self.verified_acceptance_criteria[:8]],
            proposed_completion=self.proposed_completion,
        )


class CommandExecution(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    provider_response_id: str = ""
    provider_thread_id: str = ""
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
    completed_acceptance_criteria: list[str] = Field(default_factory=list)


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
    session_id: str = ""
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


class JobAttachment(BaseModel):
    kind: str
    file_name: str
    mime_type: str = "application/octet-stream"
    telegram_file_id: str = ""
    telegram_file_unique_id: str = ""
    telegram_file_path: str = ""
    content_base64: str = ""

    @property
    def safe_file_name(self) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in self.file_name).strip("._")
        return cleaned or "attachment.bin"


class SessionRequest(BaseModel):
    goal: str
    requester_id: int
    workspace_path: str
    channel: str = "telegram"
    conversation_id: str = ""
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    user_notes: list[str] = Field(default_factory=list)
    text_only: bool = True
    requires_private_network: bool = True
    attachments: list["JobAttachment"] = Field(default_factory=list)


class JobRequest(SessionRequest):
    session_id: str = ""


class SessionContinueRequest(BaseModel):
    text: str = ""
    attachments: list["JobAttachment"] = Field(default_factory=list)


class JobSummary(BaseModel):
    job_id: str
    state: JobState
    goal: str
    session_id: str = ""
    conversation_id: str = ""
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

    @property
    def turns(self) -> list[TurnRecord]:
        return self.result.turns if self.result else []


class SessionSummary(BaseModel):
    session_id: str
    channel: str
    conversation_id: str
    goal: str
    state: SessionState
    verdict: SessionVerdict | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    active_run_id: str = ""
    final_summary: str = ""
    shared_goal_path: str = ""


class SessionDetail(BaseModel):
    summary: SessionSummary
    request: SessionRequest
    latest_job: JobDetail | None = None
    turns: list[TurnRecord] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    completed_acceptance_criteria: list[str] = Field(default_factory=list)
    user_notes: list[str] = Field(default_factory=list)
    gemini_plan: str = ""
    codex_plan: str = ""
    codex_execution: str = ""
    codex_verification: str = ""
    gemini_review: str = ""
    next_action: str = ""
    final_outcome: str = ""
    shared_goal_markdown: str = ""
    error: str = ""


class JobListResponse(BaseModel):
    jobs: list[JobSummary] = Field(default_factory=list)


class JobCreateResponse(BaseModel):
    job_id: str
    state: JobState


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary] = Field(default_factory=list)


class SessionCreateResponse(BaseModel):
    session_id: str
    state: SessionState
    verdict: SessionVerdict | None = None


class SessionContinueResponse(BaseModel):
    session_id: str
    state: SessionState
    verdict: SessionVerdict | None = None


class CancelResponse(BaseModel):
    job_id: str = ""
    state: JobState = JobState.CANCELED
    accepted: bool
    session_id: str = ""
    session_state: SessionState | None = None


class HealthResponse(BaseModel):
    status: str
    active_job_id: str | None = None
    active_session_id: str | None = None


class ProviderUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    requests: int = 0
    errors: int = 0
    observed_at: str | None = None


class ProviderQuota(BaseModel):
    requests_per_minute: int | None = None
    requests_per_day: int | None = None
    tokens_per_minute: int | None = None
    source: str = ""


class ProviderRuntimeStatus(BaseModel):
    provider: str
    configured_model: str = ""
    auth_ok: bool = False
    auth_mode: str = ""
    auth_message: str = ""
    dry_run: bool = False
    quota: ProviderQuota | None = None
    last_usage: ProviderUsage | None = None
    notes: list[str] = Field(default_factory=list)


class AIStatusResponse(BaseModel):
    codex: ProviderRuntimeStatus
    gemini: ProviderRuntimeStatus
    checked_at: datetime = Field(default_factory=utc_now)


def truncate_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit <= 3:
        return value[:limit]
    return value[: limit - 3] + "..."


def contains_hangul(value: str) -> bool:
    return any("\uac00" <= char <= "\ud7a3" for char in value)


def merge_unique_items(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for item in group:
            cleaned = item.strip()
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(cleaned)
    return merged


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
        "## Acceptance Criteria Completed",
        "",
    ]
    if result.completed_acceptance_criteria:
        lines.extend(f"- {item}" for item in result.completed_acceptance_criteria)
    else:
        lines.append("No acceptance criteria were marked as complete.")
    lines.extend(
        [
            "",
            "## Rolling Summary",
            "",
            f"```text\n{rollup.current_summary if rollup else 'No rolling summary available.'}\n```",
            "",
            "## Turns",
            "",
        ]
    )
    for turn in result.turns:
        lines.extend(
            [
                f"### Turn {turn.turn_number}",
                "",
                f"- Gemini Status: `{turn.gemini.status.value}`",
                f"- Gemini Summary: {turn.gemini.summary_for_user or 'n/a'}",
                f"- Gemini Verdict: `{resolve_session_verdict(turn.gemini).value}`",
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


def resolve_session_verdict(response: GeminiResponse) -> SessionVerdict:
    if response.verdict is not None:
        return response.verdict
    if response.status == GeminiStatus.DONE:
        return SessionVerdict.DONE
    if response.status == GeminiStatus.ASK_USER:
        return SessionVerdict.ASK_USER
    if response.status == GeminiStatus.FAILED:
        return SessionVerdict.FAIL
    return SessionVerdict.CONTINUE


def derive_acceptance_criteria(goal: str) -> list[str]:
    goal = goal.strip()
    if not goal:
        return [
            "Clarify the user goal before implementation.",
            "Capture the missing constraints in the shared session document.",
            "Do not mark the session done without explicit scope confirmation.",
        ]
    if contains_hangul(goal):
        return [
            f"사용자 목표를 직접 달성한다: {goal}",
            "검증 명령이나 수동 확인 근거로 결과물이 목표에 맞는지 확인한다.",
            "변경 내용, 남은 리스크, 목표 충족 여부를 사용자 언어로 요약한다.",
        ]
    return [
        f"Deliver the requested outcome: {goal}",
        "Verify the implementation with the best available command or explain why verification could not run.",
        "Summarize the changes, remaining risks, and whether the goal is fully satisfied.",
    ]


class MockAdapterResponse(BaseModel):
    status: str
    summary_for_user: str = ""
    instruction_for_codex: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    completed_acceptance_criteria: list[str] = Field(default_factory=list)
    verdict: str = ""
    gemini_plan: str = ""
    review_notes: str = ""
    next_action: str = ""
    question_for_user: str = ""
    reason: str = ""
    suggested_max_turns: int | None = None
    changed_files: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    command_results: list[CommandResult] = Field(default_factory=list)
    summary: str = ""
    raw_output: str = ""
    next_step: str = ""
    codex_plan: str = ""
    verification_notes: str = ""
    verified_acceptance_criteria: list[str] = Field(default_factory=list)
    proposed_completion: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        if not payload.get("verdict"):
            payload.pop("verdict", None)
        return payload


GeminiRequest.model_rebuild()
SessionRequest.model_rebuild()
JobRequest.model_rebuild()


def infer_mime_type(path: str, fallback: str = "application/octet-stream") -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return fallback
