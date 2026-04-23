from __future__ import annotations

import json
import time

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


def test_gemini_cli_adapter_extracts_dict_response_payload() -> None:
    adapter = JsonCliAdapter(
        "gemini",
        AdapterConfig(protocol="gemini_cli", command="gemini", model="gemini-2.5-flash"),
        dry_run=False,
    )
    stdout = json.dumps(
        {
            "session_id": "s1",
            "response": {
                "status": "done",
                "summary_for_user": "dictionary payload",
                "instruction_for_codex": "",
                "acceptance_criteria": [],
                "reason": "test",
            },
            "stats": {},
        }
    )

    response_json = adapter._extract_response_json(stdout)  # noqa: SLF001
    parsed = GeminiResponse.model_validate(json.loads(response_json))

    assert parsed.status.value == "done"
    assert parsed.summary_for_user == "dictionary payload"


def test_gemini_cli_adapter_falls_back_from_plain_text_response() -> None:
    adapter = JsonCliAdapter(
        "gemini",
        AdapterConfig(protocol="gemini_cli", command="gemini", model="gemini-2.5-flash"),
        dry_run=False,
    )
    stdout = json.dumps(
        {
            "session_id": "s1",
            "response": "지금 이 세션에서 Gemini는 계획과 검토를 맡고, Codex는 구현과 검증을 맡으며, MCP는 shared_goal.md를 읽고 갱신하는 통로입니다.",
            "stats": {},
        }
    )

    response_json = adapter._extract_response_json(stdout)  # noqa: SLF001
    parsed = GeminiResponse.model_validate(json.loads(response_json))

    assert parsed.status.value == "done"
    assert "Gemini는 계획과 검토" in parsed.summary_for_user


def test_gemini_cli_adapter_falls_back_from_plain_text_stdout() -> None:
    adapter = JsonCliAdapter(
        "gemini",
        AdapterConfig(protocol="gemini_cli", command="gemini", model="gemini-2.5-flash"),
        dry_run=False,
    )
    stdout = "지금 이 세션에서 Gemini는 계획과 검토를 맡고, Codex는 구현과 검증을 맡으며, MCP는 shared_goal.md를 읽고 갱신하는 통로입니다."

    response_json = adapter._extract_response_json(stdout)  # noqa: SLF001
    parsed = GeminiResponse.model_validate(json.loads(response_json))

    assert parsed.status.value == "done"
    assert "MCP는 shared_goal.md" in parsed.summary_for_user


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


def test_gemini_cli_adapter_includes_stderr_when_stdout_is_empty(monkeypatch) -> None:  # noqa: ANN001
    class Completed:
        stdout = ""
        stderr = "rate limit exceeded"
        returncode = 1

    def fake_run(*args, **kwargs):  # noqa: ANN001
        return Completed()

    monkeypatch.setattr("telecodex.shared.cli.subprocess.run", fake_run)

    adapter = JsonCliAdapter(
        "gemini",
        AdapterConfig(protocol="gemini_cli", command="gemini", model="gemini-2.5-flash"),
        dry_run=False,
    )

    try:
        adapter.execute({"remaining_turns": 1}, GeminiResponse)
    except Exception as exc:  # noqa: BLE001
        assert "rate limit exceeded" in str(exc)
    else:
        raise AssertionError("expected adapter execution to fail")


def test_codex_app_server_adapter_resumes_existing_thread(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    class FakePipe:
        def __init__(self, lines: list[str]) -> None:
            self._lines = [f"{line}\n" for line in lines]
            self.closed = False

        def readline(self) -> str:
            if self._lines:
                return self._lines.pop(0)
            time.sleep(0.01)
            return ""

        def close(self) -> None:
            self.closed = True

    class FakeStdin:
        def __init__(self) -> None:
            self.writes: list[str] = []
            self.closed = False

        def write(self, value: str) -> None:
            self.writes.append(value)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = FakePipe(
                [
                    json.dumps({"id": 0, "result": {"userAgent": "codex"}}),
                    json.dumps({"id": 1, "result": {"thread": {"id": "thread_123"}}}),
                    json.dumps({"id": 2, "result": {"turn": {"id": "turn_456", "status": "inProgress", "items": [], "error": None}}}),
                    json.dumps({"method": "item/started", "params": {"item": {"type": "agentMessage", "id": "msg_1", "text": "", "phase": "final_answer"}}}),
                    json.dumps({"method": "item/agentMessage/delta", "params": {"threadId": "thread_123", "turnId": "turn_456", "itemId": "msg_1", "delta": '{"status":"completed","changed_files":[],"commands_run":[],"command_results":[],"summary":"ok","next_step":"","raw_output":"ok"}'}}),
                    json.dumps({"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "msg_1", "text": '{"status":"completed","changed_files":[],"commands_run":[],"command_results":[],"summary":"ok","next_step":"","raw_output":"ok"}', "phase": "final_answer"}}}),
                    json.dumps({"method": "thread/tokenUsage/updated", "params": {"threadId": "thread_123", "turnId": "turn_456", "tokenUsage": {"last": {"totalTokens": 100, "inputTokens": 70, "cachedInputTokens": 20, "outputTokens": 30}}}}),
                    json.dumps({"method": "turn/completed", "params": {"threadId": "thread_123", "turn": {"id": "turn_456", "items": [], "status": "completed", "error": None}}}),
                ]
            )
            self.stderr = FakePipe([])
            self._poll = None

        def poll(self):  # noqa: ANN001
            return self._poll

        def terminate(self) -> None:
            self._poll = 0

        def wait(self, timeout=None) -> int:  # noqa: ANN001
            self._poll = 0
            return 0

        def kill(self) -> None:
            self._poll = -9

    fake_process = FakeProcess()

    def fake_popen(*args, **kwargs):  # noqa: ANN001
        return fake_process

    monkeypatch.setattr("telecodex.shared.cli.subprocess.Popen", fake_popen)

    adapter = JsonCliAdapter(
        "codex",
        AdapterConfig(protocol="codex_app_server", command="codex", timeout_sec=5),
        dry_run=False,
    )

    parsed, exchange = adapter.execute(
        {
            "project_path": str(tmp_path),
            "instruction_for_codex": "Continue the last coding session.",
            "execution_policy": {"allow_commands": ["pytest"], "deny_commands": []},
            "commands": ["pytest"],
            "thread_id": "thread_prev",
        },
        CodexResult,
    )

    sent_messages = "".join(fake_process.stdin.writes)
    assert '"method": "thread/resume"' in sent_messages
    assert '"threadId": "thread_prev"' in sent_messages
    assert parsed.summary == "ok"
    assert exchange.execution.provider_thread_id == "thread_123"
    assert exchange.execution.provider_response_id == "turn_456"


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
