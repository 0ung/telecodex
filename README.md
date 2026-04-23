# Telecodex

`telecodex` 는 Telegram에서 실제 코딩 세션을 계속 밀어갈 수 있게 만든 개인용 AI 개발 도구입니다.
Gemini는 planner와 reviewer 역할을 맡고, Codex는 executor 역할을 맡으며, 공유 세션 상태 계층을 통해 사용자가 매 턴마다 목표를 다시 설명하지 않아도 하나의 작업을 계속 이어갈 수 있습니다.

## 왜 만들었는가

- Codex는 구현은 강하지만, 실제 기능 개발은 여전히 사람이 반복해서 방향을 잡아줘야 합니다.
- Gemini가 목표를 유지하고, 다음 단계를 정하고, Codex 결과를 검토하다가 꼭 필요할 때만 나를 다시 호출하는 개인 도구가 있으면 좋겠다고 생각했습니다.
- Telegram은 어디서든 같은 개발 세션을 이어갈 수 있는 가벼운 원격 인터페이스가 되어줍니다.

## 아키텍처

- `gateway`: provider adapter를 가진 대화형 진입점
- `worker`: Gemini/Codex 오케스트레이션 루프를 돌리는 private FastAPI 런타임
- `shared`: 계약 모델, 설정, CLI 래퍼, 리포트 생성, 실행 산출물 유틸리티

네트워크 흐름:

```text
Telegram user -> Gateway -> Worker -> Gemini CLI + Codex App Server
```

## 현재 v1 범위

- 텍스트 중심 대화형 세션
- Telegram 사진/문서 첨부 ingestion
- 첨부 MIME allowlist, 파일별 최대 크기, 총첨부 용량 제한
- 허용 사용자 allowlist
- Telegram long polling 기반 gateway
- 세션 생성, 이어쓰기, 상태 조회, 목록 조회, 취소를 위한 private worker API
- worker `runs_dir` 아래에 저장되는 파일 기반 실행 산출물
- 채팅 대화 단위로 유지되는 Codex `threadId`
- 개인용 단일 노드 부트스트랩
- 이후 확장을 위한 gateway/worker 분리 배포 자산

현재 v1 범위 밖:

- Slack, Discord 런타임 adapter
- public worker ingress
- DB 기반 상태 저장
- 외부 CLI 전체 설치까지 책임지는 완전 자동 설치기

## 프로젝트 구조

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

## 권장 런타임

현재 기준 기본 배포 스토리는 **단일 노드 우선**입니다.

- Linux 서버 한 대
- 호스트가 관리하는 `codex`, `gemini` CLI
- `venv + systemd` 로 설치한 `telecodex`
- localhost에만 바인딩된 `worker`
- Telegram polling을 수행하고 로컬 worker를 호출하는 `gateway`

이 방식이 실제 사용 목적에 가장 잘 맞습니다. 이 프로젝트는 다중 테넌트 SaaS가 아니라 개인 개발 도구이기 때문입니다.

## 빠른 시작

가상환경에 프로젝트를 설치합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .[dev]
```

worker 실행:

```powershell
.\.venv\Scripts\python -m telecodex.worker.main --config config/worker.example.yaml
```

gateway 실행:

```powershell
.\.venv\Scripts\python -m telecodex.gateway.main --config config/gateway.example.yaml
```

## 단일 노드 부트스트랩

개인용 또는 단일 서버 배포에서는 다음 순서를 권장합니다.

- 먼저 호스트에 `codex` 를 설치하고 인증
- 먼저 호스트에 `gemini` 를 설치하고 인증
- 그다음 `telecodex` 만 부트스트랩

설치 스크립트는 외부 CLI 설치까지 맡지 않고, 그 전제 조건이 충족됐는지만 검증합니다.

```bash
sudo bash ./deploy/install-single-node.sh \
  --telegram-token "123456:replace-me" \
  --allowed-user-id "123456789"
```

상세 문서:

- [docs/deploy-single-node.md](docs/deploy-single-node.md)
- [docs/personal-tool-overview.md](docs/personal-tool-overview.md)
- [config/worker.single-node.example.yaml](config/worker.single-node.example.yaml)
- [config/gateway.single-node.example.yaml](config/gateway.single-node.example.yaml)

## 예시 개발 흐름

1. `/run Telegram 응답 문구를 더 자연스럽고 짧게 바꿔줘.` 를 보냅니다.
2. Gemini가 그 목표를 세션 계획과 acceptance criteria로 정리합니다.
3. Codex가 코드를 수정하고 검증을 수행하고 변경 사항을 보고합니다.
4. Gemini가 결과를 리뷰하고 아래 셋 중 하나를 결정합니다.
   - 계속 진행
   - 추가 정보 요청
   - 완료 처리
5. `/status`, `/runs`, 또는 일반 답장으로 세션을 계속 이어갑니다.

이 흐름 덕분에 이 프로젝트는 “정적인 프로토타입”이 아니라 실제로 사용하는 도구처럼 데모하기 쉽습니다.

## 대화형 명령

- `/run <goal>`: 새 공유 목표 세션 시작
- 세션이 활성화된 상태에서 일반 메시지 전송: 사용자 메모 추가 또는 Gemini 질문에 응답
- `/status`: 최신 세션 상태, verdict, 기준 진행 상황, 최근 대화 표시
- `/ai status`: Codex/Gemini 런타임과 인증 상태 표시
- `/runs`: 현재 대화의 최근 세션 목록 표시
- `/show <session_id>`: 특정 세션 상세 표시
- `/stop <session_id>`: 중단 요청

공유 세션 문서가 바뀌면 활성 세션은 요약 업데이트를 채팅에 다시 푸시합니다.

기존 `/jobs` API는 각 세션의 최신 내부 실행을 감싸는 호환 래퍼로 남아 있습니다.

## 테스트

```powershell
.\.venv\Scripts\python -m pytest tests
```

## CI/CD

- `.github/workflows/python-ci.yml`: Python `3.10`, `3.12` 테스트 워크플로
- `.github/workflows/pr-policy.yml`: `작업 브랜치 -> develop -> main` 흐름 검증
- `.github/workflows/deploy.yml`: `main` push 또는 수동 실행 시 worker/gateway 배포
- [docs/github-actions-deploy.md](docs/github-actions-deploy.md): 필요한 저장소 시크릿, 변수, 원격 배포 방식
- [docs/branch-protection.md](docs/branch-protection.md): 브랜치 보호 전략과 로컬/원격 가드레일

## 배포 자산

- `deploy/install-single-node.sh`: 호스트에 설치된 Codex/Gemini CLI를 전제로 단일 서버를 부트스트랩하는 스크립트
- `deploy/remote-release.sh`: 분리된 gateway/worker 노드용 `systemd` 중심 SSH 배포 스크립트
- `deploy/compose.private.yaml`: 선택적으로 남겨둔 두 서비스 Docker 예시
- `docs/python-replatform.md`: Python 런타임 구조와 배포 메모
- `docs/deploy-single-node.md`: 단일 노드 부트스트랩과 사전 요구사항

## 레거시 Go 아카이브

이전 Go 구현은 활성 트리에서 제거했고, 아래 경로에 로컬 아카이브로 보관하고 있습니다.

`archive/telecodex-go-legacy-20260421.zip`
