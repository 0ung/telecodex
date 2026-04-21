from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from telecodex.shared.config import AdapterConfig, WorkerConfig
from telecodex.shared.models import AIStatusResponse, ProviderQuota, ProviderRuntimeStatus, ProviderUsage, utc_now


class AiRuntimeStatusService:
    GEMINI_FREE_TIER = {
        "gemini-2.5-flash-lite": ProviderQuota(
            requests_per_minute=15,
            requests_per_day=1000,
            tokens_per_minute=250000,
            source="https://ai.google.dev/gemini-api/docs/quota",
        ),
        "gemini-2.5-flash": ProviderQuota(
            requests_per_minute=10,
            requests_per_day=250,
            tokens_per_minute=250000,
            source="https://ai.google.dev/gemini-api/docs/quota",
        ),
    }

    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg

    def build(self) -> AIStatusResponse:
        return AIStatusResponse(
            codex=self._build_codex_status(),
            gemini=self._build_gemini_status(),
            checked_at=utc_now(),
        )

    def _build_codex_status(self) -> ProviderRuntimeStatus:
        auth_mode, message, auth_ok = self._codex_auth_status()
        usage = self._load_latest_usage("codex")
        notes = []
        if usage is None:
            notes.append("Last known token usage is unavailable until a Codex run completes.")
        if self.cfg.codex.protocol == "codex_responses_local_shell":
            notes.append("Codex session chaining uses the Responses API previous_response_id flow.")
        else:
            notes.append("Codex CLI does not expose remaining ChatGPT/API balance through `login status`.")
        return ProviderRuntimeStatus(
            provider="codex",
            configured_model=self._configured_model(self.cfg.codex, default="default"),
            auth_ok=auth_ok,
            auth_mode=auth_mode,
            auth_message=message,
            dry_run=self.cfg.dry_run,
            last_usage=usage,
            notes=notes,
        )

    def _build_gemini_status(self) -> ProviderRuntimeStatus:
        auth_mode, auth_message, auth_ok = self._gemini_auth_status()
        configured_model = self._configured_model(self.cfg.gemini, default="gemini-2.5-flash-lite")
        notes = []
        quota = self.GEMINI_FREE_TIER.get(configured_model)
        if quota is None:
            notes.append("No built-in free-tier quota summary is defined for the configured Gemini model.")
        else:
            notes.append("Quota summary reflects the official Gemini API free-tier table as last checked on 2026-04-21.")
        usage = self._load_latest_usage("gemini")
        if usage is None:
            notes.append("Last known token usage is unavailable until a Gemini CLI run completes.")
        return ProviderRuntimeStatus(
            provider="gemini",
            configured_model=configured_model,
            auth_ok=auth_ok,
            auth_mode=auth_mode,
            auth_message=auth_message,
            dry_run=self.cfg.dry_run,
            quota=quota,
            last_usage=usage,
            notes=notes,
        )

    def _codex_auth_status(self) -> tuple[str, str, bool]:
        if self.cfg.codex.protocol == "codex_responses_local_shell":
            api_key = self._resolve_codex_api_key(self._adapter_env(self.cfg.codex))
            if api_key:
                return "api_key", "Codex Responses API key is configured for the worker runtime.", True
            return "none", "Codex Responses API key is not configured for the worker runtime.", False
        if not self.cfg.codex.command:
            return "none", "Codex command is not configured.", False
        try:
            completed = subprocess.run(
                [self.cfg.codex.command, "login", "status"],
                text=True,
                capture_output=True,
                timeout=15,
                env=self._adapter_env(self.cfg.codex),
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return "none", f"Unable to query Codex auth: {exc}", False
        message = (completed.stderr or completed.stdout).strip() or "Unknown Codex auth status."
        return ("chatgpt" if completed.returncode == 0 else "none"), message, completed.returncode == 0

    def _gemini_auth_status(self) -> tuple[str, str, bool]:
        env = self._adapter_env(self.cfg.gemini)
        if env.get("GEMINI_API_KEY"):
            return "api_key", "Gemini API key is configured in the worker environment.", True
        if env.get("GOOGLE_GENAI_USE_VERTEXAI") == "true":
            return "vertex", "Gemini is configured to use Vertex AI authentication.", True
        if env.get("GOOGLE_GENAI_USE_GCA") == "true":
            return "gca", "Gemini is configured to use Google account authentication.", True
        settings_path = Path(env.get("HOME", str(Path.home()))) / ".gemini" / "settings.json"
        if settings_path.exists():
            return "google_login", f"Gemini login settings found at `{settings_path}`.", True
        return "none", "Gemini authentication is not configured for the worker runtime user.", False

    def _adapter_env(self, adapter: AdapterConfig) -> dict[str, str]:
        return {**os.environ, **adapter.env}

    def _resolve_codex_api_key(self, env: dict[str, str]) -> str:
        api_key = env.get("OPENAI_API_KEY", "").strip()
        if api_key:
            return api_key
        auth_path = Path(env.get("HOME", str(Path.home()))) / ".codex" / "auth.json"
        if not auth_path.exists():
            return ""
        try:
            payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return ""
        return str(payload.get("OPENAI_API_KEY", "")).strip()

    def _configured_model(self, adapter: AdapterConfig, default: str) -> str:
        if adapter.model.strip():
            return adapter.model.strip()
        args = adapter.args
        for index, item in enumerate(args):
            if item == "--model" and index + 1 < len(args):
                return args[index + 1]
        return default

    def _load_latest_usage(self, provider: str) -> ProviderUsage | None:
        pattern = f"{provider}/turn-*-meta.json"
        candidates = sorted(Path(self.cfg.runs_dir).glob(f"*/{pattern}"), key=lambda item: item.stat().st_mtime, reverse=True)
        for path in candidates:
            payload = json.loads(path.read_text(encoding="utf-8"))
            stdout = payload.get("stdout", "")
            started_at = payload.get("finished_at") or payload.get("started_at")
            usage = self._parse_usage(provider, stdout, started_at)
            if usage is not None:
                return usage
        return None

    def _parse_usage(self, provider: str, stdout: str, observed_at: str | None) -> ProviderUsage | None:
        if not stdout.strip():
            return None
        if provider == "codex":
            return self._parse_codex_usage(stdout, observed_at)
        if provider == "gemini":
            return self._parse_gemini_usage(stdout, observed_at)
        return None

    def _parse_codex_usage(self, stdout: str, observed_at: str | None) -> ProviderUsage | None:
        if self.cfg.codex.protocol == "codex_responses_local_shell":
            return self._parse_codex_responses_usage(stdout, observed_at)
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("type") != "turn.completed":
                continue
            usage = payload.get("usage") or {}
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            cached_input_tokens = int(usage.get("cached_input_tokens", 0))
            return ProviderUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cached_input_tokens=cached_input_tokens,
                requests=1,
                observed_at=observed_at,
            )
        return None

    def _parse_codex_responses_usage(self, stdout: str, observed_at: str | None) -> ProviderUsage | None:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = payload.get("usage") or {}
            if not usage:
                continue
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens))
            cached_input_tokens = int(usage.get("cached_input_tokens", 0))
            return ProviderUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cached_input_tokens=cached_input_tokens,
                requests=1,
                observed_at=observed_at,
            )
        return None

    def _parse_gemini_usage(self, stdout: str, observed_at: str | None) -> ProviderUsage | None:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        stats = payload.get("stats", {})
        models = stats.get("models", {})
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        cached_input_tokens = 0
        requests = 0
        errors = 0
        for model_payload in models.values():
            api_stats = model_payload.get("api", {})
            token_stats = model_payload.get("tokens", {})
            requests += int(api_stats.get("totalRequests", 0))
            errors += int(api_stats.get("totalErrors", 0))
            input_tokens += int(token_stats.get("input", 0))
            output_tokens += int(token_stats.get("candidates", 0))
            total_tokens += int(token_stats.get("total", 0))
            cached_input_tokens += int(token_stats.get("cached", 0))
        if requests == 0 and total_tokens == 0:
            return None
        return ProviderUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            requests=requests,
            errors=errors,
            observed_at=observed_at,
        )
