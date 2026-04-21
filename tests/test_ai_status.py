from __future__ import annotations

import json

from telecodex.shared.config import AdapterConfig, WorkerConfig
from telecodex.worker.ai_status import AiRuntimeStatusService


def test_ai_status_service_parses_latest_usage(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    runs_dir = tmp_path / ".runs"
    codex_dir = runs_dir / "run-2" / "codex"
    gemini_dir = runs_dir / "run-2" / "gemini"
    codex_dir.mkdir(parents=True)
    gemini_dir.mkdir(parents=True)

    (codex_dir / "turn-01-meta.json").write_text(
        json.dumps(
            {
                "stdout": "\n".join(
                    [
                        json.dumps({"type": "thread.started"}),
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 5},
                            }
                        ),
                    ]
                ),
                "finished_at": "2026-04-21T13:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (gemini_dir / "turn-01-meta.json").write_text(
        json.dumps(
            {
                "stdout": json.dumps(
                    {
                        "stats": {
                            "models": {
                                "gemini-2.5-flash-lite": {
                                    "api": {"totalRequests": 2, "totalErrors": 1},
                                    "tokens": {"input": 300, "candidates": 10, "total": 350, "cached": 25},
                                }
                            }
                        }
                    }
                ),
                "finished_at": "2026-04-21T13:01:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(runs_dir),
        dry_run=False,
        gemini=AdapterConfig(protocol="gemini_cli", command="gemini", model="gemini-2.5-flash-lite"),
        codex=AdapterConfig(protocol="codex_exec_jsonl", command="codex"),
    )

    def fake_run(*args, **kwargs):  # noqa: ANN001
        class Completed:
            returncode = 0
            stdout = ""
            stderr = "Logged in using ChatGPT"

        return Completed()

    monkeypatch.setattr("telecodex.worker.ai_status.subprocess.run", fake_run)

    service = AiRuntimeStatusService(cfg)
    status = service.build()

    assert status.codex.auth_ok is True
    assert status.codex.last_usage is not None
    assert status.codex.last_usage.total_tokens == 105
    assert status.gemini.quota is not None
    assert status.gemini.quota.requests_per_day == 1000
    assert status.gemini.last_usage is not None
    assert status.gemini.last_usage.total_tokens == 350


def test_ai_status_service_reads_codex_responses_auth_and_usage(tmp_path) -> None:
    runs_dir = tmp_path / ".runs"
    codex_dir = runs_dir / "run-3" / "codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "turn-01-meta.json").write_text(
        json.dumps(
            {
                "stdout": json.dumps(
                    {
                        "id": "resp_9",
                        "usage": {"input_tokens": 40, "output_tokens": 9, "total_tokens": 49},
                    }
                ),
                "finished_at": "2026-04-21T13:05:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    home = tmp_path / "home"
    codex_auth_dir = home / ".codex"
    codex_auth_dir.mkdir(parents=True)
    (codex_auth_dir / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-test"}), encoding="utf-8")

    cfg = WorkerConfig(
        workspace_root=str(tmp_path),
        runs_dir=str(runs_dir),
        dry_run=False,
        gemini=AdapterConfig(protocol="gemini_cli", command="gemini", model="gemini-2.5-flash-lite"),
        codex=AdapterConfig(protocol="codex_responses_local_shell", model="codex-mini-latest", env={"HOME": str(home)}),
    )

    service = AiRuntimeStatusService(cfg)
    status = service.build()

    assert status.codex.auth_ok is True
    assert status.codex.auth_mode == "api_key"
    assert status.codex.last_usage is not None
    assert status.codex.last_usage.total_tokens == 49
