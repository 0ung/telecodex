# Python Telecodex 재구성

이 문서는 Telegram 전용 `telecodex` 배포를 위한 새로운 Python 런타임 구조를 설명합니다.

## 런타임 분리

- Gateway 노드: Telegram long polling 클라이언트와 명령 라우터
- Worker 노드: Gemini/Codex 오케스트레이션을 수행하는 비공개 FastAPI 서비스
- 네트워크: Gateway가 WireGuard로 보호된 private 주소를 통해 Worker에 접근

이 split-node 구조는 여전히 격리 측면에서 더 강한 선택지입니다.
다만 이제는 유일한 배포 방식이 아니라, 개인용 또는 단일 서버 설치를 위해 gateway와 worker를 같은 호스트에 두는 single-node bootstrap도 지원합니다.
이 경우 `codex` 와 `gemini` 는 호스트에 직접 설치된 전제 조건으로 유지됩니다.

## Python 패키지 구성

- `telecodex.gateway`: Telegram polling 프로세스와 worker API 클라이언트
- `telecodex.worker`: private API, 작업 관리자, 오케스트레이션 엔진, 파일 기반 실행 저장소
- `telecodex.shared`: 계약 모델, 설정 로더, CLI 어댑터 유틸리티

## 배포 메모

- Worker는 외부에 공개하지 않는 것이 원칙입니다.
- Gateway는 WireGuard로 전달된 worker private 주소를 사용해야 합니다.
- Gateway와 Worker는 JSON 계약을 공유하지만 상태 저장소는 공유하지 않습니다.
- 실행 산출물은 worker 호스트의 `runs_dir` 아래에 저장됩니다.
- single-node 모드는 외부 AI CLI 인증 상태를 호스트가 관리하므로 Docker보다 `systemd + venv` 구성이 더 적합합니다.

## 현재 v1 범위

- 텍스트 기반 Telegram 요청
- Telegram 사진/문서 첨부 ingestion
- 첨부 MIME allowlist와 크기 제한
- Telegram long polling 기반 gateway
- Gemini planner/reviewer + Codex executor 오케스트레이션
- 파일 기반 실행 산출물과 최종 리포트 생성
- gateway/worker 분리용 Dockerfile 제공

## 현재 v1 범위 밖

- 공개 webhook 기반 봇 수신
- 다중 테넌트 접근 제어
- DB 기반 실행 상태 저장
