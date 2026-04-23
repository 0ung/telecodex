from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from telecodex.shared.config import AdapterConfig, WorkerConfig
from telecodex.shared.models import ExecutionPolicy, MockAdapterResponse
from telecodex.worker.service import create_worker_app


def _build_cfg(tmp_path) -> WorkerConfig:  # noqa: ANN001
    return WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(tmp_path / ".runs"),
        dry_run=True,
        worker_token="secret",
        gemini=AdapterConfig(
            protocol="gemini_cli",
            model="gemini-2.5-flash",
            mock_responses=[
                MockAdapterResponse(
                    status="continue",
                    verdict="continue",
                    summary_for_user="continue",
                    instruction_for_codex="do work",
                    acceptance_criteria=["Do work", "Verify the change"],
                ),
                MockAdapterResponse(
                    status="done",
                    verdict="done",
                    summary_for_user="done",
                    completed_acceptance_criteria=["Do work", "Verify the change"],
                ),
            ],
        ),
        codex=AdapterConfig(
            protocol="codex_exec_jsonl",
            mock_responses=[
                MockAdapterResponse(
                    status="completed",
                    summary="done",
                    verified_acceptance_criteria=["Do work", "Verify the change"],
                    proposed_completion=True,
                )
            ],
        ),
        execution_policy=ExecutionPolicy(),
    )


def test_worker_api_creates_and_reads_session(tmp_path) -> None:
    client = TestClient(create_worker_app(_build_cfg(tmp_path)))

    created = client.post(
        "/sessions",
        headers={"X-Worker-Token": "secret"},
        json={
            "goal": "Run",
            "requester_id": 1,
            "workspace_path": str(tmp_path),
            "channel": "telegram",
            "conversation_id": "chat-1",
            "text_only": True,
            "requires_private_network": True,
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    loaded_payload = None
    for _ in range(10):
        loaded = client.get(f"/sessions/{session_id}", headers={"X-Worker-Token": "secret"})
        assert loaded.status_code == 200
        loaded_payload = loaded.json()
        if loaded_payload["acceptance_criteria"]:
            break
        time.sleep(0.1)
    assert loaded_payload is not None
    assert loaded_payload["summary"]["session_id"] == session_id
    assert loaded_payload["acceptance_criteria"]

    jobs_payload = []
    for _ in range(10):
        jobs = client.get("/jobs", headers={"X-Worker-Token": "secret"})
        assert jobs.status_code == 200
        jobs_payload = jobs.json()["jobs"]
        if jobs_payload:
            break
        time.sleep(0.1)
    assert jobs_payload


def test_worker_api_continues_waiting_session(tmp_path) -> None:
    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(tmp_path / ".runs"),
        dry_run=True,
        worker_token="secret",
        gemini=AdapterConfig(
            protocol="gemini_cli",
            model="gemini-2.5-flash",
            mock_responses=[
                MockAdapterResponse(
                    status="ask_user",
                    verdict="ask_user",
                    summary_for_user="Need clarification",
                    question_for_user="Which branch?",
                ),
                MockAdapterResponse(
                    status="done",
                    verdict="done",
                    summary_for_user="All done",
                ),
            ],
        ),
        codex=AdapterConfig(protocol="codex_exec_jsonl"),
        execution_policy=ExecutionPolicy(),
    )
    client = TestClient(create_worker_app(cfg))
    created = client.post(
        "/sessions",
        headers={"X-Worker-Token": "secret"},
        json={
            "goal": "Run",
            "requester_id": 1,
            "workspace_path": str(tmp_path),
            "channel": "telegram",
            "conversation_id": "chat-2",
            "text_only": True,
            "requires_private_network": True,
        },
    )
    session_id = created.json()["session_id"]
    time.sleep(0.2)
    continued = client.post(
        f"/sessions/{session_id}/continue",
        headers={"X-Worker-Token": "secret"},
        json={"text": "Use main"},
    )
    assert continued.status_code == 200
    assert continued.json()["session_id"] == session_id


def test_worker_api_reports_ai_status(tmp_path) -> None:
    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(tmp_path / ".runs"),
        dry_run=True,
        worker_token="secret",
        gemini=AdapterConfig(protocol="gemini_cli", mock_responses=[]),
        codex=AdapterConfig(protocol="codex_exec_jsonl", mock_responses=[]),
        execution_policy=ExecutionPolicy(),
    )
    client = TestClient(create_worker_app(cfg))

    response = client.get("/ai/status", headers={"X-Worker-Token": "secret"})

    assert response.status_code == 200
    assert response.json()["codex"]["provider"] == "codex"
    assert response.json()["gemini"]["provider"] == "gemini"


@pytest.mark.parametrize(
    ("header_value", "expected_detail"),
    [
        (None, "missing worker token"),
        ("", "missing worker token"),
        ("wrong", "invalid worker token"),
    ],
)
def test_worker_api_rejects_invalid_auth_headers(tmp_path, header_value, expected_detail) -> None:  # noqa: ANN001
    client = TestClient(create_worker_app(_build_cfg(tmp_path)))
    headers = {} if header_value is None else {"X-Worker-Token": header_value}

    response = client.get("/health", headers=headers)

    assert response.status_code == 401
    assert response.json()["detail"] == expected_detail


def test_worker_api_rejects_session_creation_without_valid_worker_token(tmp_path) -> None:
    client = TestClient(create_worker_app(_build_cfg(tmp_path)))

    response = client.post(
        "/sessions",
        headers={"X-Worker-Token": "wrong"},
        json={
            "goal": "Run",
            "requester_id": 1,
            "workspace_path": str(tmp_path),
            "channel": "telegram",
            "conversation_id": "chat-auth",
            "text_only": True,
            "requires_private_network": True,
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid worker token"


def test_worker_app_manages_mcp_lifecycle(tmp_path) -> None:
    cfg = _build_cfg(tmp_path)

    with (
        patch("telecodex.worker.orchestrator.WorkerOrchestrator.start") as start,
        patch("telecodex.worker.orchestrator.WorkerOrchestrator.shutdown") as shutdown,
    ):
        with TestClient(create_worker_app(cfg)) as client:
            response = client.get("/health", headers={"X-Worker-Token": "secret"})
            assert response.status_code == 200

        start.assert_called_once()
        shutdown.assert_called_once()
