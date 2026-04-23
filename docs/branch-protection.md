# 브랜치 보호 전략

이 저장소는 아래와 같은 단순한 승격 흐름을 사용합니다.

```text
작업 브랜치 -> develop -> main
```

## 의도한 브랜치 흐름

- 작업 브랜치:
  - `feature/*`
  - `hotfix/*`
  - `codex/*`
  - 그 외 짧게 쓰는 작업 브랜치
- 통합 브랜치:
  - `develop`
- 릴리스 브랜치:
  - `main`

규칙은 다음과 같습니다.

1. 기능 작업은 모두 Pull Request를 통해 `develop` 으로 들어갑니다.
2. `main` 은 `develop` 에서 올라온 Pull Request만 받습니다.
3. `main` 과 `develop` 에 대한 직접 push는 막혀 있어야 합니다.
4. `develop -> main` 승격은 승인 후에만 머지합니다.

## 저장소 안쪽 가드레일

이 저장소에는 다음과 같은 가드레일이 포함되어 있습니다.

- `.github/workflows/pr-policy.yml`
  - 허용된 브랜치 흐름이 아닌 PR이면 실패시킵니다.
- `.github/pull_request_template.md`
  - 기여자가 기대되는 승격 경로를 다시 확인하게 합니다.
- `.githooks/pre-push`
  - 로컬에서 `main` 과 `develop` 으로 직접 push하는 것을 막습니다.

현재 클론에서 로컬 훅을 활성화하려면:

```bash
git config core.hooksPath .githooks
```

## GitHub 보호 설정

다음 설정은 GitHub의 branch protection 또는 ruleset에도 적용해야 합니다.

### `develop`

- Pull Request 없이 머지할 수 없게 설정
- 직접 push 금지
- 필수 상태 체크:
  - `test (3.10)`
  - `test (3.12)`
  - `validate-target`
- 머지 전에 브랜치를 최신 상태로 맞추도록 강제

### `main`

- Pull Request 없이 머지할 수 없게 설정
- 직접 push 금지
- 필수 상태 체크:
  - `test (3.10)`
  - `test (3.12)`
  - `validate-target`
- 머지 전에 브랜치를 최신 상태로 맞추도록 강제
- 머지 전에 대화 스레드 해결 요구
- 최소 `1`개의 승인 요구
- 새 커밋이 올라오면 기존 승인을 무효화하는 옵션 권장

## `main` 승인 규칙에 대한 실무적인 메모

저장소를 사실상 혼자 운영하더라도 `main` 에 `1 approval required` 를 강하게 걸면 아래 둘 중 하나가 필요합니다.

- 두 번째 리뷰 계정 또는 협업자
- 정말 필요한 경우에만 쓰는 관리자 우회

즉, `main` 을 강하게 보호하는 대신 릴리스 브랜치 운영이 조금 더 엄격해지는 트레이드오프가 있습니다.
