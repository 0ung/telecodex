from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from telecodex.shared.config import AdapterConfig
from telecodex.shared.models import (
    AdapterExchange,
    CodexResult,
    CommandExecution,
    GeminiResponse,
    MockAdapterResponse,
    utc_now,
)


class CliExecutionError(RuntimeError):
    """Raised when a CLI execution cannot produce a valid response."""


class JsonCliAdapter:
    def __init__(self, name: str, config: AdapterConfig, dry_run: bool) -> None:
        self.name = name
        self.config = config
        self.dry_run = dry_run
        self._mock_index = 0

    def execute(self, payload: dict[str, Any], response_type: type[GeminiResponse] | type[CodexResult]) -> tuple[Any, AdapterExchange]:
        request_json = json.dumps(payload, ensure_ascii=False, indent=2)
        if self.dry_run:
            return self._execute_mock(request_json, response_type)

        if self.config.protocol != "codex_responses_local_shell" and not self.config.command:
            raise CliExecutionError(f"{self.name} adapter requires command when dry_run is false")

        attempts = self.config.retries + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            started = utc_now()
            started_monotonic = time.perf_counter()
            try:
                response_json, stdout, stderr, exit_code, executed_args, provider_response_id = self._run_process(payload, request_json)
                parsed = response_type.model_validate(json.loads(response_json))
                finished = utc_now()
                execution = CommandExecution(
                    command=self.config.command or self.name,
                    args=executed_args,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    duration_ms=int((time.perf_counter() - started_monotonic) * 1000),
                    provider_response_id=provider_response_id,
                    started_at=started,
                    finished_at=finished,
                )
                return parsed, AdapterExchange(
                    request_json=request_json,
                    response_json=response_json,
                    execution=execution,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        assert last_error is not None
        raise CliExecutionError(str(last_error)) from last_error

    def _execute_mock(self, request_json: str, response_type: type[GeminiResponse] | type[CodexResult]) -> tuple[Any, AdapterExchange]:
        if not self.config.mock_responses:
            if response_type is GeminiResponse:
                fallback = GeminiResponse(
                    status="done",
                    summary_for_user="Dry-run completed without configured mock responses.",
                    reason="default dry-run response",
                )
            else:
                fallback = CodexResult(status="completed", summary="Dry-run completed without configured mock responses.")
            response_json = fallback.model_dump_json(indent=2)
            parsed = fallback
        else:
            item: MockAdapterResponse = self.config.mock_responses[min(self._mock_index, len(self.config.mock_responses) - 1)]
            self._mock_index += 1
            parsed = response_type.model_validate(item.to_payload())
            response_json = parsed.model_dump_json(indent=2)
        now = utc_now()
        exchange = AdapterExchange(
            request_json=request_json,
            response_json=response_json,
            execution=CommandExecution(
                command=f"mock:{self.name}",
                args=[],
                exit_code=0,
                started_at=now,
                finished_at=now,
            ),
        )
        return parsed, exchange

    def _run_process(self, payload: dict[str, Any], request_json: str) -> tuple[str, str, str, int, list[str], str]:
        if self.config.protocol == "codex_responses_local_shell":
            return self._run_openai_local_shell(payload, request_json)
        env = {**os.environ, **self.config.env}
        args = self._build_args(payload)
        stdin_payload = self._render_input(payload, request_json)
        completed = subprocess.run(
            args,
            input=stdin_payload,
            text=True,
            capture_output=True,
            timeout=self.config.timeout_sec,
            env=env or None,
            cwd=self._resolve_cwd(payload),
            check=False,
        )
        response_json = self._extract_response_json(completed.stdout)
        return response_json, completed.stdout, completed.stderr, completed.returncode, args[1:], ""

    def _resolve_cwd(self, payload: dict[str, Any]) -> str | None:
        if self.config.protocol == "codex_exec_jsonl":
            project_path = str(payload.get("project_path", "")).strip()
            return project_path or None
        return None

    def _build_args(self, payload: dict[str, Any]) -> list[str]:
        if self.config.protocol == "gemini_cli":
            args = [self.config.command, *self.config.args]
            if self.config.model:
                args.extend(["--model", self.config.model])
            args.extend(["--prompt", self._render_input(payload, json.dumps(payload, ensure_ascii=False, indent=2)), "--output-format", "json"])
            return args
        if self.config.protocol == "codex_exec_jsonl":
            args = [
                self.config.command,
                "exec",
                "--json",
                "--full-auto",
                "--skip-git-repo-check",
            ]
            if self.config.model:
                args.extend(["--model", self.config.model])
            args.extend(self.config.args)
            project_path = str(payload.get("project_path", "")).strip()
            if project_path:
                args.extend(["-C", project_path])
            args.append("-")
            return args
        if self.config.protocol == "codex_responses_local_shell":
            return ["responses", self.config.model or "codex-mini-latest", "local_shell"]
        args = [self.config.command, *self.config.args]
        if self.config.model:
            args.extend(["--model", self.config.model])
        return args

    def _render_input(self, payload: dict[str, Any], request_json: str) -> str:
        if self.config.protocol == "gemini_cli":
            return _render_gemini_prompt(request_json)
        if self.config.protocol == "codex_exec_jsonl":
            return _render_codex_prompt(request_json)
        if self.config.protocol == "codex_responses_local_shell":
            return _render_codex_prompt(request_json)
        if not self.config.prompt_template:
            return request_json
        template = Path(self.config.prompt_template).read_text(encoding="utf-8")
        return template.replace("{request_json}", request_json)

    def _run_openai_local_shell(self, payload: dict[str, Any], request_json: str) -> tuple[str, str, str, int, list[str], str]:
        env = {**os.environ, **self.config.env}
        api_key = self._resolve_openai_api_key(env)
        if not api_key:
            raise CliExecutionError("OPENAI_API_KEY is not configured for the Codex Responses API adapter")

        model = self.config.model or "codex-mini-latest"
        tool_spec = {"type": "local_shell"}
        request_body: dict[str, Any] = {
            "model": model,
            "tools": [tool_spec],
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": self._render_input(payload, request_json),
                        }
                    ],
                }
            ],
        }
        previous_response_id = str(payload.get("previous_response_id", "")).strip()
        if previous_response_id:
            request_body["previous_response_id"] = previous_response_id

        response_trace: list[str] = []
        latest_response: dict[str, Any] | None = None
        base_url = self.config.api_base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        while True:
            response = httpx.post(
                f"{base_url}/responses",
                headers=headers,
                json=request_body,
                timeout=self.config.timeout_sec,
            )
            response.raise_for_status()
            latest_response = response.json()
            response_trace.append(json.dumps(latest_response, ensure_ascii=False))

            shell_calls = self._extract_local_shell_calls(latest_response)
            if not shell_calls:
                break

            output_items = [self._execute_local_shell_call(call, payload, env) for call in shell_calls]
            latest_response_id = str(latest_response.get("id", "")).strip()
            request_body = {
                "model": model,
                "tools": [tool_spec],
                "previous_response_id": latest_response_id,
                "input": output_items,
            }

        if latest_response is None:
            raise CliExecutionError("Codex Responses API returned no payload")

        final_text = self._extract_openai_message_text(latest_response)
        response_json = _extract_last_json_blob(final_text)
        provider_response_id = str(latest_response.get("id", "")).strip()
        return response_json, "\n".join(response_trace), "", 0, [model, "local_shell"], provider_response_id

    def _extract_local_shell_calls(self, response_payload: dict[str, Any]) -> list[dict[str, Any]]:
        shell_calls: list[dict[str, Any]] = []
        for item in response_payload.get("output", []):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "local_shell_call":
                shell_calls.append(item)
            elif item_type == "tool_call" and item.get("tool_name") == "local_shell":
                shell_calls.append(item)
        return shell_calls

    def _execute_local_shell_call(self, call: dict[str, Any], payload: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
        call_id = str(call.get("call_id", "")).strip()
        args = call.get("action") or call.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        command = args.get("command")
        command_argv = self._normalize_command(command)
        output = ""

        if not command_argv:
            output = "No command was provided by the Codex shell tool."
        else:
            allowed, reason = self._is_shell_command_allowed(command_argv, payload)
            if not allowed:
                output = reason
            else:
                runtime_env = {
                    key: value
                    for key, value in env.items()
                    if key not in {"OPENAI_API_KEY"}
                }
                runtime_env.update({str(key): str(value) for key, value in (args.get("env") or {}).items()})
                timeout_sec = self._resolve_shell_timeout(args)
                working_directory = str(args.get("working_directory") or payload.get("project_path") or "").strip() or None
                try:
                    completed = subprocess.run(
                        command_argv,
                        cwd=working_directory,
                        env=runtime_env,
                        capture_output=True,
                        text=True,
                        timeout=timeout_sec,
                        check=False,
                    )
                    output = (completed.stdout or "") + (completed.stderr or "")
                    if not output.strip():
                        output = f"Command exited with code {completed.returncode}."
                except subprocess.TimeoutExpired as exc:
                    output = ((exc.stdout or "") + (exc.stderr or "")).strip()
                    if output:
                        output += "\n"
                    output += f"Command timed out after {timeout_sec} seconds."
                except Exception as exc:  # noqa: BLE001
                    output = f"Command execution failed: {exc}"

        return {
            "type": "local_shell_call_output",
            "call_id": call_id,
            "output": output,
        }

    def _resolve_openai_api_key(self, env: dict[str, str]) -> str:
        api_key = env.get("OPENAI_API_KEY", "").strip()
        if api_key:
            return api_key
        home = Path(env.get("HOME", str(Path.home())))
        auth_path = home / ".codex" / "auth.json"
        if auth_path.exists():
            try:
                payload = json.loads(auth_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise CliExecutionError(f"invalid Codex auth.json: {exc}") from exc
            embedded_key = str(payload.get("OPENAI_API_KEY", "")).strip()
            if embedded_key:
                return embedded_key
        return ""

    @staticmethod
    def _normalize_command(command: Any) -> list[str]:
        if isinstance(command, str):
            return shlex.split(command)
        if isinstance(command, list):
            return [str(item) for item in command if str(item).strip()]
        return []

    def _resolve_shell_timeout(self, args: dict[str, Any]) -> float | None:
        timeout_ms = args.get("timeout_ms")
        if timeout_ms is None:
            return self.config.timeout_sec or None
        try:
            timeout_sec = max(float(timeout_ms) / 1000.0, 1.0)
        except (TypeError, ValueError):
            return self.config.timeout_sec or None
        if self.config.timeout_sec:
            return min(timeout_sec, float(self.config.timeout_sec))
        return timeout_sec

    @staticmethod
    def _extract_openai_message_text(response_payload: dict[str, Any]) -> str:
        texts: list[str] = []
        top_level_output_text = str(response_payload.get("output_text", "")).strip()
        if top_level_output_text:
            texts.append(top_level_output_text)
        for item in response_payload.get("output", []):
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            for content_item in item.get("content", []):
                if not isinstance(content_item, dict):
                    continue
                if content_item.get("type") not in {"output_text", "text"}:
                    continue
                text_value = content_item.get("text", "")
                if isinstance(text_value, dict):
                    text_value = text_value.get("value", "")
                text_value = str(text_value).strip()
                if text_value:
                    texts.append(text_value)
        if texts:
            return "\n".join(texts)
        raise CliExecutionError("Codex Responses API did not return a final assistant message")

    @staticmethod
    def _is_shell_command_allowed(command_argv: list[str], payload: dict[str, Any]) -> tuple[bool, str]:
        prefix = command_argv[0]
        policy = payload.get("execution_policy") or {}
        allow = [str(item) for item in policy.get("allow_commands", [])]
        deny = [str(item) for item in policy.get("deny_commands", [])]
        if deny and prefix in deny:
            return False, f"Command '{prefix}' is denied by execution policy."
        if allow and prefix not in allow:
            return False, f"Command '{prefix}' is not allowed by execution policy."
        return True, ""

    def _extract_response_json(self, stdout: str) -> str:
        text = stdout.strip()
        if not text:
            raise CliExecutionError(f"{self.name} adapter returned empty stdout")
        if self.config.protocol == "gemini_cli":
            outer = json.loads(_extract_last_json_blob(text))
            if not isinstance(outer, dict):
                raise CliExecutionError("gemini adapter returned a non-object payload")
            response_text = str(outer.get("response", "")).strip()
            if not response_text:
                raise CliExecutionError("gemini adapter returned an empty response field")
            return _extract_last_json_blob(response_text)
        if self.config.protocol == "generic_json":
            return _extract_last_json_blob(text)
        if self.config.protocol == "codex_exec_jsonl":
            last_message: str | None = None
            for line in reversed(text.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and "result" in payload and isinstance(payload["result"], dict):
                    return json.dumps(payload["result"], ensure_ascii=False)
                item = payload.get("item") if isinstance(payload, dict) else None
                if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    last_message = item["text"]
                    break
            if last_message:
                return _extract_last_json_blob(last_message)
            raise CliExecutionError(f"{self.name} adapter could not parse JSONL output")
        raise CliExecutionError(f"unsupported protocol: {self.config.protocol}")


def _extract_last_json_blob(text: str) -> str:
    cleaned = _strip_code_fences(text.strip())
    lines = cleaned.splitlines()
    for start in range(len(lines)):
        chunk = "\n".join(lines[start:]).strip()
        if not chunk:
            continue
        try:
            json.loads(chunk)
            return chunk
        except json.JSONDecodeError:
            continue
    raise CliExecutionError("stdout did not contain a valid JSON payload")


def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text


def _render_gemini_prompt(request_json: str) -> str:
    return (
        "You are the planner/reviewer for the telecodex worker.\n"
        "Read the request JSON below and respond with exactly one JSON object.\n"
        "Do not wrap the JSON in markdown fences.\n"
        "The JSON schema is:\n"
        "{\n"
        '  "status": "continue" | "done" | "failed",\n'
        '  "summary_for_user": "short plain-language summary",\n'
        '  "instruction_for_codex": "next concrete instruction for codex",\n'
        '  "acceptance_criteria": ["optional list"],\n'
        '  "reason": "brief reason",\n'
        '  "suggested_max_turns": null\n'
        "}\n"
        "Rules:\n"
        "- Use status=continue when Codex should take another action.\n"
        "- Use status=done when the task is complete.\n"
        "- Use status=failed when the run should stop due to an unrecoverable problem.\n"
        "- Keep instruction_for_codex empty unless status=continue.\n"
        "- Keep the response compact and valid JSON.\n\n"
        "Request JSON:\n"
        f"{request_json}\n"
    )


def _render_codex_prompt(request_json: str) -> str:
    return (
        "You are the executor for the telecodex worker.\n"
        "Carry out the requested work inside the provided project path.\n"
        "You may edit files and run commands when needed.\n"
        "When you finish, respond with exactly one JSON object and no markdown fences.\n"
        "The JSON schema is:\n"
        "{\n"
        '  "status": "success" | "failed" | "skipped" | "completed" | "waiting_for_input" | "blocked",\n'
        '  "changed_files": ["relative/or/absolute/path"],\n'
        '  "commands_run": ["cmd"],\n'
        '  "command_results": [{"command":"cmd","exit_code":0,"stdout":"","stderr":"","duration_ms":0}],\n'
        '  "summary": "what happened",\n'
        '  "next_step": "optional next step",\n'
        '  "raw_output": "optional short raw summary"\n'
        "}\n"
        "Rules:\n"
        "- Report only valid JSON.\n"
        "- Prefer status=completed when the requested work is finished.\n"
        "- Use waiting_for_input when you are blocked on a human decision.\n"
        "- changed_files and commands_run must reflect what actually happened.\n"
        "- Keep raw_output concise.\n\n"
        "Request JSON:\n"
        f"{request_json}\n"
    )
