from __future__ import annotations

import time

from fastapi.testclient import TestClient

from telecodex.shared.config import AdapterConfig, WorkerConfig
from telecodex.shared.models import ExecutionPolicy, MockAdapterResponse
from telecodex.worker.service import create_worker_app


def test_worker_api_creates_and_reads_job(tmp_path) -> None:
    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(tmp_path / ".runs"),
        dry_run=True,
        worker_token="secret",
        gemini=AdapterConfig(
            protocol="gemini_cli",
            mock_responses=[
                MockAdapterResponse(
                    status="continue",
                    summary_for_user="continue",
                    instruction_for_codex="do work",
                )
            ],
        ),
        codex=AdapterConfig(
            protocol="codex_exec_jsonl",
            mock_responses=[MockAdapterResponse(status="completed", summary="done")],
        ),
        execution_policy=ExecutionPolicy(),
    )
    client = TestClient(create_worker_app(cfg))

    created = client.post(
        "/jobs",
        headers={"X-Worker-Token": "secret"},
        json={"goal": "Run", "requester_id": 1, "workspace_path": str(tmp_path), "text_only": True, "requires_private_network": True},
    )
    assert created.status_code == 201
    job_id = created.json()["job_id"]

    time.sleep(0.2)
    loaded = client.get(f"/jobs/{job_id}", headers={"X-Worker-Token": "secret"})
    assert loaded.status_code == 200
    assert loaded.json()["summary"]["job_id"] == job_id
