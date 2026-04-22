from __future__ import annotations

from telecodex.shared.config import AdapterConfig, WorkerConfig
from telecodex.shared.models import (
    ExecutionPolicy,
    MockAdapterResponse,
    SessionDetail,
    SessionRequest,
    SessionState,
)
from telecodex.worker.orchestrator import SessionRuntimeState, WorkerOrchestrator


def _build_runtime(orchestrator: WorkerOrchestrator, request: SessionRequest, session_id: str) -> SessionRuntimeState:
    document = orchestrator.store.create_session(session_id, request, orchestrator.cfg.gemini.model or "gemini-2.5-flash")
    summary = document.to_summary(request, str(orchestrator.store.shared_goal_path(session_id)))
    detail = SessionDetail(
        summary=summary,
        request=request,
        acceptance_criteria=list(document.acceptance_criteria),
        completed_acceptance_criteria=list(document.completed_acceptance_criteria),
        user_notes=list(document.user_notes),
        shared_goal_markdown=document.render_markdown(),
    )
    return SessionRuntimeState(summary=summary, request=request, detail=detail)


def test_worker_orchestrator_completes_dry_run(tmp_path) -> None:
    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(tmp_path / ".runs"),
        dry_run=True,
        gemini=AdapterConfig(
            protocol="gemini_cli",
            model="gemini-2.5-flash",
            mock_responses=[
                MockAdapterResponse(
                    status="continue",
                    verdict="continue",
                    summary_for_user="Proceed with implementation.",
                    instruction_for_codex="Implement feature.",
                    acceptance_criteria=["Implement feature.", "Verify the change."],
                    next_action="Ask Codex to implement the feature.",
                ),
                MockAdapterResponse(
                    status="done",
                    verdict="done",
                    summary_for_user="The goal is complete.",
                    completed_acceptance_criteria=["Implement feature.", "Verify the change."],
                    final_outcome="The implementation is complete.",
                ),
            ],
        ),
        codex=AdapterConfig(
            protocol="codex_exec_jsonl",
            default_commands=["pytest"],
            mock_responses=[
                MockAdapterResponse(
                    status="completed",
                    summary="Implementation finished.",
                    changed_files=["src/example.py"],
                    commands_run=["pytest"],
                    verified_acceptance_criteria=["Implement feature.", "Verify the change."],
                    proposed_completion=True,
                )
            ],
        ),
        execution_policy=ExecutionPolicy(allow_commands=["pytest"]),
    )
    orchestrator = WorkerOrchestrator(cfg)
    request = SessionRequest(
        goal="Build the worker.",
        requester_id=1,
        workspace_path=str(tmp_path),
        channel="telegram",
        conversation_id="chat-1",
    )
    runtime = _build_runtime(orchestrator, request, "session-1")
    result = orchestrator.process_session(runtime)

    assert result.summary.state == SessionState.COMPLETED
    assert result.latest_job is not None
    assert result.latest_job.result is not None
    assert result.latest_job.result.final_summary == "The goal is complete."
    assert (tmp_path / ".runs" / "_sessions" / "session-1" / "shared_goal.md").exists()


def test_worker_orchestrator_waits_for_user_when_gemini_requests_input(tmp_path) -> None:
    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(tmp_path / ".runs"),
        dry_run=True,
        gemini=AdapterConfig(
            protocol="gemini_cli",
            model="gemini-2.5-flash",
            mock_responses=[
                MockAdapterResponse(
                    status="ask_user",
                    verdict="ask_user",
                    summary_for_user="Need clarification.",
                    question_for_user="Which branch should I target?",
                    next_action="Wait for the user to choose the branch.",
                )
            ],
        ),
        codex=AdapterConfig(protocol="codex_exec_jsonl", default_commands=["pytest"]),
        execution_policy=ExecutionPolicy(allow_commands=["pytest"]),
    )
    orchestrator = WorkerOrchestrator(cfg)
    request = SessionRequest(
        goal="Build the worker.",
        requester_id=1,
        workspace_path=str(tmp_path),
        channel="telegram",
        conversation_id="chat-2",
    )
    runtime = _build_runtime(orchestrator, request, "session-2")
    result = orchestrator.process_session(runtime)

    assert result.summary.state == SessionState.WAITING_USER
    assert "Which branch should I target?" in result.next_action
