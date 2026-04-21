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
                response_json, stdout, stderr, exit_code = self._run_process(request_json)
                parsed = response_type.model_validate(json.loads(response_json))
                finished = utc_now()
                execution = CommandExecution(
                    command=self.config.command,
                    args=list(self.config.args),
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

    def _run_process(self, request_json: str) -> tuple[str, str, str, int]:
        env = {**os.environ, **self.config.env}
        args = [self.config.command, *self.config.args]
        stdin_payload = self._render_stdin(request_json)
        completed = subprocess.run(
            args,
            input=stdin_payload,
            text=True,
            capture_output=True,
            timeout=self.config.timeout_sec,
            env=env or None,
            check=False,
        )
        response_json = self._extract_response_json(completed.stdout)
        return response_json, completed.stdout, completed.stderr, completed.returncode

    def _render_stdin(self, request_json: str) -> str:
        if not self.config.prompt_template:
            return request_json
        template = Path(self.config.prompt_template).read_text(encoding="utf-8")
        return template.replace("{request_json}", request_json)

    def _extract_response_json(self, stdout: str) -> str:
        text = stdout.strip()
        if not text:
            raise CliExecutionError(f"{self.name} adapter returned empty stdout")
        if self.config.protocol in {"generic_json", "gemini_cli"}:
            return _extract_last_json_blob(text)
        if self.config.protocol == "codex_exec_jsonl":
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
                if isinstance(payload, dict):
                    return json.dumps(payload, ensure_ascii=False)
            raise CliExecutionError(f"{self.name} adapter could not parse JSONL output")
        raise CliExecutionError(f"unsupported protocol: {self.config.protocol}")


def _extract_last_json_blob(text: str) -> str:
    lines = text.splitlines()
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
