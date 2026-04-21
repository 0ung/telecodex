from __future__ import annotations

import json

from telecodex.shared.cli import JsonCliAdapter
from telecodex.shared.config import AdapterConfig
from telecodex.shared.models import CodexResult, GeminiResponse


def test_gemini_cli_adapter_extracts_inner_response_json() -> None:
    adapter = JsonCliAdapter(
        "gemini",
        AdapterConfig(protocol="gemini_cli", command="gemini", model="gemini-2.5-flash-lite"),
        dry_run=False,
    )
    stdout = json.dumps(
        {
            "session_id": "s1",
            "response": json.dumps(
                {
                    "status": "done",
                    "summary_for_user": "ok",
                    "instruction_for_codex": "",
                    "acceptance_criteria": [],
                    "reason": "test",
                }
            ),
            "stats": {},
        }
    )

    response_json = adapter._extract_response_json(stdout)  # noqa: SLF001
    parsed = GeminiResponse.model_validate(json.loads(response_json))

    assert parsed.status.value == "done"
    assert parsed.summary_for_user == "ok"


def test_codex_cli_adapter_extracts_agent_message_json() -> None:
    adapter = JsonCliAdapter(
        "codex",
        AdapterConfig(protocol="codex_exec_jsonl", command="codex"),
        dry_run=False,
    )
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_0",
                        "type": "agent_message",
                        "text": json.dumps(
                            {
                                "status": "completed",
                                "changed_files": [],
                                "commands_run": [],
                                "command_results": [],
                                "summary": "ok",
                                "next_step": "",
                                "raw_output": "ok",
                            }
                        ),
                    },
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}),
        ]
    )

    response_json = adapter._extract_response_json(stdout)  # noqa: SLF001
    parsed = CodexResult.model_validate(json.loads(response_json))

    assert parsed.status.value == "completed"
    assert parsed.summary == "ok"


def test_codex_cli_adapter_builds_noninteractive_exec_args() -> None:
    adapter = JsonCliAdapter(
        "codex",
        AdapterConfig(protocol="codex_exec_jsonl", command="codex", model="gpt-5.4"),
        dry_run=False,
    )

    args = adapter._build_args({"project_path": "/workspace"})  # noqa: SLF001

    assert args[:5] == ["codex", "exec", "--json", "--full-auto", "--skip-git-repo-check"]
    assert "--model" in args
    assert "-C" in args
    assert args[-1] == "-"


def test_codex_responses_adapter_chains_previous_response_id(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-test"}), encoding="utf-8")

    requests: list[dict] = []
    responses = [
        {
            "id": "resp_1",
            "output": [
                {
                    "type": "local_shell_call",
                    "call_id": "call_1",
                    "action": {
                        "command": "pytest",
                        "working_directory": str(tmp_path),
                    },
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        },
        {
            "id": "resp_2",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "status": "completed",
                                    "changed_files": [],
                                    "commands_run": ["pytest"],
                                    "command_results": [],
                                    "summary": "session continued",
                                    "next_step": "",
                                    "raw_output": "ok",
                                }
                            ),
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
        },
    ]

    class FakeHttpResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    def fake_post(url: str, headers: dict, json: dict, timeout: int):  # noqa: ANN001,A002
        requests.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeHttpResponse(responses[len(requests) - 1])

    class Completed:
        stdout = "ok"
        stderr = ""
        returncode = 0

    def fake_run(*args, **kwargs):  # noqa: ANN001
        return Completed()

    monkeypatch.setattr("telecodex.shared.cli.httpx.post", fake_post)
    monkeypatch.setattr("telecodex.shared.cli.subprocess.run", fake_run)

    adapter = JsonCliAdapter(
        "codex",
        AdapterConfig(
            protocol="codex_responses_local_shell",
            model="codex-mini-latest",
            env={"HOME": str(tmp_path)},
            timeout_sec=30,
        ),
        dry_run=False,
    )

    parsed, exchange = adapter.execute(
        {
            "project_path": str(tmp_path),
            "instruction_for_codex": "Continue the last coding session.",
            "execution_policy": {"allow_commands": ["pytest"], "deny_commands": []},
            "commands": ["pytest"],
            "previous_response_id": "resp_prev",
        },
        CodexResult,
    )

    assert parsed.summary == "session continued"
    assert exchange.execution.provider_response_id == "resp_2"
    assert requests[0]["json"]["previous_response_id"] == "resp_prev"
    assert requests[1]["json"]["previous_response_id"] == "resp_1"
    assert requests[1]["json"]["input"][0]["type"] == "local_shell_call_output"


def test_codex_responses_adapter_reads_api_key_from_codex_auth(tmp_path) -> None:
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-test"}), encoding="utf-8")

    adapter = JsonCliAdapter(
        "codex",
        AdapterConfig(
            protocol="codex_responses_local_shell",
            env={"HOME": str(tmp_path)},
        ),
        dry_run=False,
    )

    api_key = adapter._resolve_openai_api_key({"HOME": str(tmp_path)})  # noqa: SLF001

    assert api_key == "sk-test"
