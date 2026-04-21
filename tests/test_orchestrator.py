from __future__ import annotations

from telecodex.shared.config import AdapterConfig, WorkerConfig
from telecodex.shared.models import ExecutionPolicy, JobDetail, JobRequest, JobState, JobSummary, MockAdapterResponse
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
