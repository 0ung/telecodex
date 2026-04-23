# Branch Protection Strategy

This repository uses a simple promotion flow:

```text
work branch -> develop -> main
```

## Intended branch flow

- work branches:
  - `feature/*`
  - `hotfix/*`
  - `codex/*`
  - any short-lived task branch
- integration branch:
  - `develop`
- release branch:
  - `main`

Rules:

1. All feature work lands in `develop` through a pull request
2. `main` only accepts pull requests from `develop`
3. Direct pushes to `main` and `develop` should be blocked
4. `develop -> main` requires approval before merge

## Repo-side guardrails

The repository now includes three guardrails:

- `.github/workflows/pr-policy.yml`
  - fails PRs that do not follow the allowed base branch flow
- `.github/pull_request_template.md`
  - reminds contributors of the expected promotion path
- `.githooks/pre-push`
  - blocks local direct pushes to `main` and `develop`

To enable the local hook in this clone:

```bash
git config core.hooksPath .githooks
```

## GitHub protection settings

Apply these settings in GitHub branch protection or rulesets.

### `develop`

- require a pull request before merging
- do not allow direct pushes
- require status checks:
  - `test (3.10)`
  - `test (3.12)`
  - `validate-target`
- require branches to be up to date before merging

### `main`

- require a pull request before merging
- do not allow direct pushes
- require status checks:
  - `test (3.10)`
  - `test (3.12)`
  - `validate-target`
- require branches to be up to date before merging
- require conversation resolution before merging
- require at least `1` approval
- optionally dismiss stale approvals when new commits are pushed

## Practical note about approval on `main`

If you are the only maintainer, a strict `1 approval required` rule on `main` means you need:

- a second reviewer account or collaborator, or
- a temporary admin bypass when you intentionally release alone

That is the tradeoff for keeping `main` as a protected release branch.
