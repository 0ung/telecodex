# Telecodex

`telecodex` is a personal AI development tool that lets me drive real coding sessions from Telegram.
It uses Gemini as the planner and reviewer, Codex as the executor, and a shared session state layer so one goal can keep moving without me re-prompting every turn.

## Why I built it

- Codex is strong at implementation, but real feature work still needs repeated human steering
- I wanted a personal tool where Gemini could keep the goal, decide the next step, review Codex output, and only pull me back in when necessary
- Telegram became the lightweight remote interface so I could continue the same development session from chat

## Architecture

- `gateway`: conversational entrypoint with provider adapters
- `worker`: private FastAPI runtime that runs the Gemini/Codex orchestration loop
- `shared`: contracts, config models, CLI wrappers, report generation, and run artifact helpers

Network flow:

```text
Telegram user -> Gateway -> Worker -> Gemini CLI + Codex App Server
```

## Current v1 scope

- text-first conversational sessions
- approved-user allowlist
- private worker API for session creation, continuation, status, listing, and cancellation
- file-based run artifacts under the worker `runs_dir`
- Codex conversation continuity per chat conversation via persisted Codex `threadId`
- single-node bootstrap for personal use
- optional split gateway/worker deployment assets for future expansion

Out of scope in v1:

- Slack and Discord runtime adapters
- public worker ingress
- database-backed run storage
- full third-party CLI installation management

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

## Recommended runtime

The main deployment story is now **single-node first**:

- one Linux server
- host-managed `codex` and `gemini` CLIs
- `telecodex` installed with `venv + systemd`
- `worker` bound to localhost only
- `gateway` polling Telegram and calling the worker locally

This fits the actual use case best: a personal development tool, not a multi-tenant SaaS product.

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

## Single-node bootstrap

For a personal or single-server deployment, the recommended path is:

- install and authenticate `codex` on the host first
- install and authenticate `gemini` on the host first
- then bootstrap only `telecodex`

The installer intentionally validates those prerequisites instead of trying to own third-party CLI installation:

```bash
sudo bash ./deploy/install-single-node.sh \
  --telegram-token "123456:replace-me" \
  --allowed-user-id "123456789"
```

Details:

- [docs/deploy-single-node.md](docs/deploy-single-node.md)
- [docs/personal-tool-overview.md](docs/personal-tool-overview.md)
- [config/worker.single-node.example.yaml](config/worker.single-node.example.yaml)
- [config/gateway.single-node.example.yaml](config/gateway.single-node.example.yaml)

## Example development flow

1. Send `/run Make the Telegram reply text feel more natural and concise.`
2. Gemini turns that goal into a session plan and acceptance criteria
3. Codex edits code, runs checks, and reports what changed
4. Gemini reviews the result and either:
   - continues
   - asks for missing information
   - marks the goal complete
5. Use `/status`, `/runs`, or a plain reply to keep the session moving

That makes the project easy to demo as an actually used tool instead of a static prototype.

## Conversational commands

- `/run <goal>`: start a new shared-goal session
- send a normal message while a session is active: append user notes or answer Gemini follow-up questions
- `/status`: show the latest session state, verdict, criteria progress, and recent dialogue
- `/ai status`: show Codex and Gemini runtime and auth status
- `/runs`: list recent sessions in the current conversation
- `/show <session_id>`: show one session in detail
- `/stop <session_id>`: request cancellation

Active sessions also push compact summary updates back to the chat when the shared session document changes.

Legacy `/jobs` API routes still exist as compatibility wrappers around the latest internal run for each session.

## Testing

```powershell
.\.venv\Scripts\python -m pytest tests
```

## CI/CD

- `.github/workflows/python-ci.yml`: Python `3.10` and `3.12` test workflow
- `.github/workflows/pr-policy.yml`: pull request target validation for `work branch -> develop -> main`
- `.github/workflows/deploy.yml`: `main` push or manual deploy to the worker and gateway servers
- [docs/github-actions-deploy.md](docs/github-actions-deploy.md): required repository secrets, variables, and remote deploy behavior
- [docs/branch-protection.md](docs/branch-protection.md): branch protection strategy and local/remote guardrails

## Deployment assets

- `deploy/install-single-node.sh`: single-server bootstrap that validates preinstalled Codex and Gemini CLIs
- `deploy/remote-release.sh`: systemd-oriented SSH deployment script for split gateway and worker nodes
- `deploy/compose.private.yaml`: two-service Docker example kept as an optional asset
- `docs/python-replatform.md`: architecture and deployment notes
- `docs/deploy-single-node.md`: single-node bootstrap and prerequisite guide

## Legacy Go archive

The previous Go implementation has been removed from the active tree and archived locally at:

`archive/telecodex-go-legacy-20260421.zip`
