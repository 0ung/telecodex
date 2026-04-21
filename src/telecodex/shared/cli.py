from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

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

        if not self.config.command:
            raise CliExecutionError(f"{self.name} adapter requires command when dry_run is false")

        attempts = self.config.retries + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            started = utc_now()
            started_monotonic = time.perf_counter()
            try:
                response_json, stdout, stderr, exit_code, executed_args = self._run_process(payload, request_json)
                parsed = response_type.model_validate(json.loads(response_json))
                finished = utc_now()
                execution = CommandExecution(
                    command=self.config.command,
                    args=executed_args,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    duration_ms=int((time.perf_counter() - started_monotonic) * 1000),
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

    def _run_process(self, payload: dict[str, Any], request_json: str) -> tuple[str, str, str, int, list[str]]:
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
        return response_json, completed.stdout, completed.stderr, completed.returncode, args[1:]

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
        args = [self.config.command, *self.config.args]
        if self.config.model:
            args.extend(["--model", self.config.model])
        return args

    def _render_input(self, payload: dict[str, Any], request_json: str) -> str:
        if self.config.protocol == "gemini_cli":
            return _render_gemini_prompt(request_json)
        if self.config.protocol == "codex_exec_jsonl":
            return _render_codex_prompt(request_json)
        if not self.config.prompt_template:
            return request_json
        template = Path(self.config.prompt_template).read_text(encoding="utf-8")
        return template.replace("{request_json}", request_json)

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
