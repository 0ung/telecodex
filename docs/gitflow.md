# GitFlow 작업 흐름

이 저장소는 보호된 장기 브랜치를 중심으로 GitFlow 스타일 브랜치 모델을 사용합니다.

## 장기 브랜치

- `main`: 운영 배포 가능한 코드만 유지
- `develop`: 다음 작업을 통합하는 브랜치

`main` 과 `develop` 에 직접 push 하는 것은 일반적인 작업 흐름에 포함되지 않습니다.

## 브랜치 이름 규칙

- `feature/<scope>`: 사용자 기능 또는 내부 개발 작업
- `release/<scope>`: `develop` 에서 분기한 릴리스 준비 브랜치
- `hotfix/<scope>`: `main` 에서 분기한 긴급 운영 수정 브랜치

## 기본 흐름

### 기능 개발

1. `develop` 에서 브랜치를 분기합니다.
2. `feature/<scope>` 에서 변경을 구현합니다.
3. `develop` 대상으로 PR을 엽니다.
4. 필수 리뷰어에게 리뷰를 요청합니다.
5. 필수 체크와 리뷰가 모두 통과한 뒤에만 머지합니다.

### 릴리스 작업

1. `develop` 에서 브랜치를 분기합니다.
2. `release/<scope>` 에서 릴리스 준비를 진행합니다.
3. `main` 대상으로 PR을 엽니다.
4. 릴리스가 끝나면 그 변경을 다시 `develop` 으로 동기화합니다.

### 핫픽스 작업

1. `main` 에서 브랜치를 분기합니다.
2. `hotfix/<scope>` 에서 운영 이슈를 수정합니다.
3. `main` 대상으로 PR을 엽니다.
4. 머지 후에는 같은 핫픽스를 `develop` 에도 다시 반영합니다.

## 필수 리뷰와 보호 규칙

현재 GitHub의 `main`, `develop` 보호 규칙은 아래를 요구합니다.

- 머지 전에 브랜치를 최신 상태로 맞출 것
- 필수 상태 체크 통과:
  - `test (3.10)`
  - `test (3.12)`
  - `validate-target`
- 리뷰 대화 스레드 해결
- 최소 1개의 승인 리뷰
- CODEOWNERS 리뷰

현재 `CODEOWNERS` 에서 지정한 AI 리뷰어 계정은 아래와 같습니다.

- `@2tpghkk8yp-stack`

즉 보호 브랜치를 대상으로 하는 모든 PR은 머지 전에 이 계정의 리뷰를 반드시 받아야 합니다.

## 로컬 브랜치 정리 가이드

feature 또는 hotfix가 머지된 뒤에는 아래 순서로 로컬 브랜치를 정리합니다.

1. `git fetch --all --prune`
2. 로컬 `main`, `develop` fast-forward
3. 머지된 로컬 작업 브랜치 삭제

예시:

```powershell
git checkout main
git pull --ff-only origin main
git checkout develop
git pull --ff-only origin develop
git branch -d feature/my-change
```
