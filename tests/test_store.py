from __future__ import annotations

from telecodex.shared.models import AdapterExchange, CommandExecution, JobAttachment, JobRequest, RollingSummary, RunMetadata
from telecodex.worker.store import FileRunStore


def test_file_store_writes_expected_artifacts(tmp_path) -> None:
    store = FileRunStore(str(tmp_path), "run-1")
    exchange = AdapterExchange(
        request_json='{"hello":"world"}',
        response_json='{"status":"ok"}',
        execution=CommandExecution(command="mock"),
    )
    store.save_gemini(1, exchange)
    store.save_codex(1, exchange)
    store.save_metadata(RunMetadata(run_id="run-1", project_path=".", goal="goal", max_turns=3, effective_max_turns=3, max_codex_failures=1))
    store.save_summary(RollingSummary(current_summary="summary"))
    store.save_job_request(
        JobRequest(
            goal="goal",
            requester_id=1,
            workspace_path=".",
            attachments=[
                JobAttachment(
                    kind="photo",
                    file_name="photo.jpg",
                    content_base64="aGVsbG8=",
                )
            ],
        )
    )
    report_path = store.save_final_report("# report")

    assert (tmp_path / "run-1" / "gemini" / "turn-01-request.json").exists()
    assert (tmp_path / "run-1" / "codex" / "turn-01-response.json").exists()
    assert (tmp_path / "run-1" / "summary.json").exists()
    assert (tmp_path / "run-1" / "attachments" / "01-photo.jpg").exists()
    assert report_path.endswith("final-report.md")
