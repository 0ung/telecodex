# GitFlow Workflow

This repository uses a GitFlow-style branch model with protected long-lived branches.

## Long-lived branches

- `main`: production-ready code only
- `develop`: integration branch for upcoming work

Direct pushes to `main` and `develop` are not part of the normal workflow.

## Branch naming

- `feature/<scope>`: new user-facing or internal development work
- `release/<scope>`: release preparation from `develop`
- `hotfix/<scope>`: urgent production fixes from `main`

## Standard flow

### Feature work

1. Branch from `develop`
2. Implement the change on `feature/<scope>`
3. Open a PR into `develop`
4. Request review from the required reviewer
5. Merge only after all required checks and review pass

### Release work

1. Branch from `develop`
2. Stabilize on `release/<scope>`
3. Open a PR into `main`
4. After release, sync the released changes back into `develop`

### Hotfix work

1. Branch from `main`
2. Fix the production issue on `hotfix/<scope>`
3. Open a PR into `main`
4. After merge, sync the hotfix back into `develop`

## Required review and protections

The GitHub branch protection rules on `main` and `develop` currently require:

- up-to-date branch before merge
- passing status checks:
  - `test (3.10)`
  - `test (3.12)`
  - `validate-target`
- resolved review conversations
- at least one approving review
- CODEOWNERS review

`CODEOWNERS` currently requires the AI reviewer account:

- `@2tpghkk8yp-stack`

That means every PR targeting protected branches must be reviewed by that account before merge.

## Local cleanup guidance

After a feature or hotfix is merged:

1. `git fetch --all --prune`
2. fast-forward local `main` / `develop`
3. delete merged local topic branches

Example:

```powershell
git checkout main
git pull --ff-only origin main
git checkout develop
git pull --ff-only origin develop
git branch -d feature/my-change
```
