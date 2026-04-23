from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from telecodex.shared.models import ExecutionPolicy, MockAdapterResponse


@dataclass
class AdapterConfig:
    protocol: str
    command: str = ""
    api_base_url: str = "https://api.openai.com/v1"
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_sec: int = 120
    retries: int = 0
    prompt_template: str = ""
    default_commands: list[str] = field(default_factory=list)
    model: str = ""
    mock_responses: list[MockAdapterResponse] = field(default_factory=list)


@dataclass
class WorkerConfig:
    host: str = "0.0.0.0"
    port: int = 8081
    workspace_root: str = "."
    runs_dir: str = ".runs"
    max_turns: int = 6
    max_turns_cap: int = 6
    dynamic_turn_budget: bool = False
    max_codex_failures: int = 2
    dry_run: bool = True
    print_io: bool = False
    gemini: AdapterConfig = field(default_factory=lambda: AdapterConfig(protocol="gemini_cli", model="gemini-2.5-flash"))
    codex: AdapterConfig = field(default_factory=lambda: AdapterConfig(protocol="codex_app_server", command="codex", timeout_sec=900))
    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    worker_token: str = ""


@dataclass
class GatewayConfig:
    allowed_user_ids: list[int]
    worker_base_url: str
    channel_provider: str = "telegram"
    telegram_token: str = ""
    worker_token: str = ""
    poll_timeout_sec: int = 30
    request_timeout_sec: int = 30
    session_push_interval_sec: int = 5


def load_worker_config(path: str) -> WorkerConfig:
    raw = _load_yaml(path)
    base = Path(path).resolve().parent
    gemini = _load_adapter(raw.get("gemini", {}))
    codex = _load_adapter(raw.get("codex", {}))
    policy = ExecutionPolicy(**raw.get("execution_policy", {}))
    cfg = WorkerConfig(
        host=raw.get("host", "0.0.0.0"),
        port=int(raw.get("port", 8081)),
        workspace_root=_resolve(base, raw.get("workspace_root", ".")),
        runs_dir=_resolve(base, raw.get("runs_dir", ".runs")),
        max_turns=int(raw.get("max_turns", 6)),
        max_turns_cap=int(raw.get("max_turns_cap", raw.get("max_turns", 6))),
        dynamic_turn_budget=bool(raw.get("dynamic_turn_budget", False)),
        max_codex_failures=int(raw.get("max_codex_failures", 2)),
        dry_run=bool(raw.get("dry_run", True)),
        print_io=bool(raw.get("print_io", False)),
        gemini=gemini,
        codex=codex,
        execution_policy=policy,
        worker_token=os.getenv("TELECODEX_WORKER_TOKEN", raw.get("worker_token", "")),
    )
    _validate_worker_config(cfg)
    return cfg


def load_gateway_config(path: str) -> GatewayConfig:
    raw = _load_yaml(path)
    token = os.getenv("TELECODEX_TELEGRAM_TOKEN", raw.get("telegram_token", ""))
    worker_token = os.getenv("TELECODEX_WORKER_TOKEN", raw.get("worker_token", ""))
    cfg = GatewayConfig(
        channel_provider=str(raw.get("channel_provider", "telegram")),
        telegram_token=token,
        allowed_user_ids=[int(item) for item in raw.get("allowed_user_ids", [])],
        worker_base_url=str(raw.get("worker_base_url", "")).rstrip("/"),
        worker_token=worker_token,
        poll_timeout_sec=int(raw.get("poll_timeout_sec", 30)),
        request_timeout_sec=int(raw.get("request_timeout_sec", 30)),
        session_push_interval_sec=int(raw.get("session_push_interval_sec", 5)),
    )
    if cfg.channel_provider == "telegram" and not cfg.telegram_token:
        raise ValueError("gateway config: telegram_token is required")
    if not cfg.allowed_user_ids:
        raise ValueError("gateway config: allowed_user_ids is required")
    if not cfg.worker_base_url:
        raise ValueError("gateway config: worker_base_url is required")
    return cfg


def _load_adapter(raw: dict[str, Any]) -> AdapterConfig:
    responses = [MockAdapterResponse(**item) for item in raw.get("mock_responses", [])]
    return AdapterConfig(
        protocol=str(raw.get("protocol", "generic_json")),
        command=str(raw.get("command", "")),
        api_base_url=str(raw.get("api_base_url", "https://api.openai.com/v1")).rstrip("/"),
        args=[str(item) for item in raw.get("args", [])],
        env={str(key): str(value) for key, value in raw.get("env", {}).items()},
        timeout_sec=int(raw.get("timeout_sec", 120)),
        retries=int(raw.get("retries", 0)),
        prompt_template=str(raw.get("prompt_template", "")),
        default_commands=[str(item) for item in raw.get("default_commands", [])],
        model=str(raw.get("model", "")),
        mock_responses=responses,
    )


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config at {path} must be a mapping")
    return data


def _resolve(base: Path, value: str) -> str:
    target = Path(value)
    if target.is_absolute():
        return str(target)
    return str((base / target).resolve())


def _validate_worker_config(cfg: WorkerConfig) -> None:
    if cfg.max_turns <= 0:
        raise ValueError("worker config: max_turns must be > 0")
    if cfg.max_turns_cap < cfg.max_turns:
        raise ValueError("worker config: max_turns_cap must be >= max_turns")
    if cfg.max_codex_failures < 0:
        raise ValueError("worker config: max_codex_failures must be >= 0")
    Path(cfg.workspace_root).mkdir(parents=True, exist_ok=True)
    Path(cfg.runs_dir).mkdir(parents=True, exist_ok=True)
