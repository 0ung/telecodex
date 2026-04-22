# Telecodex Python Replatform

`telecodex` is now a Python-first project with a pluggable conversational gateway and a private worker runtime.

## Architecture

- `gateway`: channel-agnostic conversational gateway core with provider adapters
- `worker`: private FastAPI service that runs the Gemini/Codex orchestration loop
- `shared`: contracts, config models, CLI wrappers, report generation, and run artifact helpers

Network flow:

```text
Telegram user -> Gateway -> WireGuard private network -> Worker -> Gemini/Codex CLI
```

## Current v1 scope

- Text-first conversational commands through pluggable chat adapters
- Approved-user allowlist
- Private worker API for session creation, continuation, status, listing, and cancel requests
- File-based run artifacts under the worker `runs_dir`
- Telegram image or file ingestion
- Docker assets for separate gateway and worker containers

Out of scope in v1:

- Slack and Discord runtime adapters
- Public worker ingress
- Database-backed run storage

## Project layout

```text
config/
deploy/
docker/
docs/
src/telecodex/gateway
src/telecodex/shared
src/telecodex/worker
tests/
```

## Quick start

Install the project in a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
```

Run the worker:

```powershell
.\.venv\Scripts\python -m telecodex.worker.main --config config/worker.example.yaml
```

Run the gateway:

```powershell
.\.venv\Scripts\python -m telecodex.gateway.main --config config/gateway.example.yaml
```

## Conversational commands

- `/run <goal>`: start a new shared-goal session
- Send a normal message while a session is active: append user notes or answer Gemini follow-up questions
- `/status`: show the latest session state, verdict, criteria progress, and recent dialogue
- `/runs`: list recent sessions in the current conversation
- `/show <session_id>`: show one session in detail
- `/stop <session_id>`: request cancellation
- Active sessions also push compact summary updates back to the chat when the shared session document changes

Legacy `/jobs` API routes still exist as compatibility wrappers around the latest internal run for each session.

The default provider is Telegram, but the gateway core now accepts provider adapters so Slack and Discord can be added without changing worker-facing logic.

## Testing

```powershell
.\.venv\Scripts\python -m pytest tests
```

## Deployment

- `docker/gateway.Dockerfile`: conversational gateway image
- `docker/worker.Dockerfile`: private worker image
- `deploy/compose.private.yaml`: two-service deployment example
- `docs/python-replatform.md`: architecture and deployment notes

## Legacy Go archive

The previous Go implementation has been removed from the active tree and archived locally at:

`archive/telecodex-go-legacy-20260421.zip`
