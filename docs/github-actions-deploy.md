# GitHub Actions CI/CD

`telecodex` uses two GitHub Actions workflows:

- `ci`: runs on pushes, pull requests, and manual dispatch to verify the Python package on Python `3.10` and `3.12`
- `deploy`: runs on pushes to `main` or manual dispatch to deploy the latest release archive to the gateway and worker nodes

## Required repository variables

- `TELECODEX_GATEWAY_HOST`: public SSH host for the gateway node
- `TELECODEX_WORKER_HOST`: public SSH host for the worker node
- `TELECODEX_SSH_USER`: optional, defaults to `ubuntu`
- `TELECODEX_DEPLOY_ROOT`: optional, defaults to `/opt/telecodex`
- `TELECODEX_KEEP_RELEASES`: optional, defaults to `5`

## Required repository secrets

- `TELECODEX_GATEWAY_SSH_KEY`: private key for the gateway server
- `TELECODEX_WORKER_SSH_KEY`: private key for the worker server

Each server key should authorize the GitHub Actions runner to upload:

- `/tmp/telecodex-release.tar.gz`
- `/tmp/telecodex-remote-release.sh`

## Remote deploy behavior

The remote script:

1. extracts the release archive into `/opt/telecodex/releases/<release-id>`
2. updates `/opt/telecodex/current` to the new release
3. runs `pip install -e /opt/telecodex/current` inside `/opt/telecodex/venv`
4. restarts the appropriate `systemd` unit
5. verifies the service is active
6. for the worker, additionally verifies `http://127.0.0.1:8081/health`

## Manual deploy

The `deploy` workflow supports manual dispatch with a `target` input:

- `all`
- `worker`
- `gateway`

`push` to `main` always deploys both nodes in order:

1. worker
2. gateway
