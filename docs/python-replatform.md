# Python Telecodex Replatform

This document captures the new Python runtime layout for the Telegram-only `telecodex` deployment.

## Runtime split

- Gateway node: Telegram long polling client and command router
- Worker node: private FastAPI service that runs Gemini/Codex orchestration
- Network: Gateway reaches Worker over a WireGuard-protected private address

This split-node model remains the stronger isolation option, but it is no longer the only deployment shape.
For single-user or single-server installs, the project now also supports a single-node bootstrap where gateway and worker
run on the same host while `codex` and `gemini` stay host-managed prerequisites.

## Python packages

- `telecodex.gateway`: Telegram polling process and worker API client
- `telecodex.worker`: private API, job manager, orchestration engine, file run store
- `telecodex.shared`: contracts, config loading, CLI adapter utilities

## Deployment notes

- Worker should not be publicly exposed.
- Gateway should use the worker private address distributed over WireGuard.
- The gateway and worker share JSON contracts but do not share state.
- Run artifacts are stored on the worker host under the configured `runs_dir`.
- Single-node mode should prefer `systemd + venv` over Docker because external AI CLI auth remains host-owned.

## Current v1 scope

- Text-only Telegram requests
- Gemini planner/reviewer plus Codex executor orchestration
- File-based run artifacts and final report generation
- Dockerfiles for separate gateway and worker containers

## Explicitly out of scope in v1

- Telegram photo or document ingestion
- Public webhook-based bot delivery
- Multi-tenant access control
- Database-backed run storage
