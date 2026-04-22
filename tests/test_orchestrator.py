from __future__ import annotations

from telecodex.shared.config import AdapterConfig, WorkerConfig
from telecodex.shared.models import AdapterExchange, CodexResult, CommandExecution, ExecutionPolicy, JobDetail, JobRequest, JobState, JobSummary, MockAdapterResponse, utc_now
from telecodex.worker.orchestrator import JobRuntimeState, WorkerOrchestrator


def test_worker_orchestrator_completes_dry_run(tmp_path) -> None:
    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(tmp_path / ".runs"),
        dry_run=True,
        gemini=AdapterConfig(
            protocol="gemini_cli",
            mock_responses=[
                MockAdapterResponse(
                    status="continue",
                    summary_for_user="Proceed with implementation.",
                    instruction_for_codex="Implement feature.",
                    reason="continue",
                )
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
                )
            ],
        ),
        execution_policy=ExecutionPolicy(allow_commands=["pytest"]),
    )
    orchestrator = WorkerOrchestrator(cfg)
    request = JobRequest(goal="Build the worker.", requester_id=1, workspace_path=str(tmp_path))
    summary = JobSummary(job_id="run-1", state=JobState.QUEUED, goal=request.goal)
    detail = JobDetail(summary=summary, request=request)
    result = orchestrator.run_job(JobRuntimeState(summary=summary, request=request, detail=detail))

    assert result.summary.state == JobState.COMPLETED
    assert result.result is not None
    assert result.result.final_summary == "Implementation finished."
    assert result.turns[0].gemini.summary_for_user == "Starting Codex from the initial user goal."
    assert result.turns[0].codex.summary == "Implementation finished."


def test_worker_orchestrator_reuses_codex_thread_for_same_conversation(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(tmp_path / ".runs"),
        dry_run=False,
        gemini=AdapterConfig(protocol="gemini_cli", command="gemini", model="gemini-2.5-flash-lite"),
        codex=AdapterConfig(protocol="codex_app_server", command="codex"),
        execution_policy=ExecutionPolicy(allow_commands=["pytest"]),
    )
    orchestrator = WorkerOrchestrator(cfg)
    seen_thread_ids: list[str] = []

    def fake_execute(self, payload, response_type):  # noqa: ANN001
        assert self.name == "codex"
        seen_thread_ids.append(payload.get("thread_id", ""))
        result = CodexResult(status="completed", summary="done")
        now = utc_now()
        exchange = AdapterExchange(
            request_json="{}",
            response_json=result.model_dump_json(indent=2),
            execution=CommandExecution(
                command="codex app-server",
                args=[],
                stdout="{}",
                stderr="",
                exit_code=0,
                provider_response_id=f"turn_{len(seen_thread_ids)}",
                provider_thread_id=f"thread_{len(seen_thread_ids)}",
                started_at=now,
                finished_at=now,
            ),
        )
        return result, exchange

    monkeypatch.setattr("telecodex.worker.orchestrator.JsonCliAdapter.execute", fake_execute)

    for job_id in ["run-1", "run-2"]:
        request = JobRequest(
            goal="Keep the coding session going.",
            requester_id=1,
            workspace_path=str(tmp_path),
            channel="telegram",
            conversation_id="chat-42",
        )
        summary = JobSummary(job_id=job_id, state=JobState.QUEUED, goal=request.goal)
        detail = JobDetail(summary=summary, request=request)
        orchestrator.run_job(JobRuntimeState(summary=summary, request=request, detail=detail))

    assert seen_thread_ids == ["", "thread_1"]
