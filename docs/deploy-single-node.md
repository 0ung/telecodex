# Single-Node Bootstrap

This mode runs both `gateway` and `worker` on the same Linux host.

It is the recommended personal deployment mode when:

- you want the simplest operational setup
- you already have `codex` and `gemini` CLI installed on the host
- you do not need a separate WireGuard-isolated worker node yet

## What the installer does

`deploy/install-single-node.sh` is intentionally a **bootstrap** script, not a full toolchain installer.

It will:

1. verify that `codex` and `gemini` already exist for the chosen runtime user
2. verify that `codex` is already logged in
3. verify that `gemini` is already authenticated
4. copy the current repository checkout into `/opt/telecodex/current`
5. create `/opt/telecodex/venv`
6. install `telecodex` itself into that virtual environment
7. generate single-node config files under `/etc/telecodex`
8. install two `systemd` units:
   - `telecodex-worker.service`
   - `telecodex-gateway.service`
9. start both services and verify the worker health endpoint

It will **not**:

- install Docker
- install `codex`
- install `gemini`
- perform interactive login for either CLI

## Prerequisites

- Linux with `systemd`
- Python `3.10+`
- `codex` installed for the runtime user
- `gemini` installed for the runtime user
- `codex login status` must already succeed for that user
- one of:
  - `gemini` login/settings already present for that user
  - `GEMINI_API_KEY` exported before running the installer

If you install as root via `sudo`, the script defaults the runtime user to `SUDO_USER`.

## Minimum system guidance

Because Gemini and Codex do their model inference remotely, this server is mainly responsible for:

- session orchestration
- file IO
- local commands such as tests, git, and lightweight builds

Practical tiers:

- absolute minimum bootable setup:
  - `1 vCPU`
  - `1 GB RAM`
  - `2 GB swap`
  - `20 GB SSD`
- recommended personal-use setup:
  - `2 vCPU`
  - `4 GB RAM`
  - `20-40 GB SSD`
- comfortable setup for heavier local verification:
  - `4 vCPU`
  - `8 GB RAM`
  - `40 GB+ SSD`

Notes:

- `1 vCPU / 1 GB RAM` can work for a single personal bot, but only for light workloads
- heavy `pytest`, JavaScript builds, Docker builds, or multiple simultaneous sessions will feel slow or fail under memory pressure
- GPU is not required

## Recommended flow

1. Install and authenticate `codex`
2. Install and authenticate `gemini`
3. Clone this repository
4. Run the bootstrap script

Example:

```bash
sudo bash ./deploy/install-single-node.sh \
  --telegram-token "123456:replace-me" \
  --allowed-user-id "123456789"
```

With explicit runtime user and workspace root:

```bash
sudo bash ./deploy/install-single-node.sh \
  --runtime-user "ubuntu" \
  --telegram-token "123456:replace-me" \
  --allowed-user-id "123456789" \
  --workspace-root "/opt/telecodex/current"
```

## Generated files

- worker config: `/etc/telecodex/worker.single-node.yaml`
- gateway config: `/etc/telecodex/gateway.single-node.yaml`
- worker env: `/etc/telecodex/worker.env`
- gateway env: `/etc/telecodex/gateway.env`
- app root: `/opt/telecodex`

## Runtime shape

- `worker` binds to `127.0.0.1:8081`
- `gateway` calls `http://127.0.0.1:8081`
- `worker` is not publicly exposed
- Telegram traffic reaches only the gateway process

## Service management

```bash
sudo systemctl status telecodex-worker
sudo systemctl status telecodex-gateway
```

```bash
sudo systemctl restart telecodex-worker
sudo systemctl restart telecodex-gateway
```

Worker health:

```bash
source /etc/telecodex/worker.env
curl -H "X-Worker-Token: $TELECODEX_WORKER_TOKEN" http://127.0.0.1:8081/health
```

## Notes

- This mode is better suited to `systemd + venv` than Docker because the external AI CLIs live on the host and keep their own authentication state.
- A future Docker-based single-node mode is still possible, but it should remain optional rather than the primary install path.
