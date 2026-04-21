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
