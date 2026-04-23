# 단일 노드 부트스트랩

이 모드는 하나의 Linux 호스트에서 `gateway` 와 `worker` 를 함께 실행합니다.

다음과 같은 경우에 개인용 기본 배포 방식으로 권장합니다.

- 운영 구성을 가장 단순하게 가져가고 싶을 때
- 호스트에 `codex` 와 `gemini` CLI를 이미 설치해 둔 상태일 때
- 아직 WireGuard로 분리된 별도 worker 노드가 꼭 필요하지 않을 때

## 설치 스크립트가 하는 일

`deploy/install-single-node.sh` 는 전체 툴체인 설치기가 아니라 **부트스트랩 스크립트**입니다.

이 스크립트는 다음 작업을 수행합니다.

1. 선택한 런타임 사용자 기준으로 `codex` 와 `gemini` 가 이미 존재하는지 확인
2. `codex` 가 이미 로그인된 상태인지 확인
3. `gemini` 인증이 이미 준비되어 있는지 확인
4. 현재 저장소 체크아웃을 `/opt/telecodex/current` 로 복사
5. `/opt/telecodex/venv` 생성
6. 해당 가상환경에 `telecodex` 자체 설치
7. `/etc/telecodex` 아래에 단일 노드 설정 파일 생성
8. 아래 `systemd` 유닛 설치
   - `telecodex-worker.service`
   - `telecodex-gateway.service`
9. 두 서비스를 시작하고 worker health endpoint 확인

이 스크립트는 다음 작업은 하지 않습니다.

- Docker 설치
- `codex` 설치
- `gemini` 설치
- 두 CLI에 대한 대화형 로그인 진행

## 사전 요구사항

- `systemd` 가 있는 Linux
- Python `3.10+`
- 런타임 사용자 기준으로 설치된 `codex`
- 런타임 사용자 기준으로 설치된 `gemini`
- 해당 사용자에서 `codex login status` 가 성공해야 함
- 아래 둘 중 하나 충족
  - 해당 사용자 홈에 `gemini` 로그인/설정 파일 존재
  - 설치 전에 `GEMINI_API_KEY` 환경변수 export 완료

`sudo` 로 설치할 경우 런타임 사용자는 기본적으로 `SUDO_USER` 를 따릅니다.

## 최소 시스템 가이드

Gemini와 Codex 자체 추론은 원격에서 수행되므로, 이 서버는 주로 아래 역할을 담당합니다.

- 세션 오케스트레이션
- 파일 입출력
- 테스트, git, 가벼운 빌드 같은 로컬 명령 실행

현실적인 권장 스펙은 다음 정도입니다.

- 절대 최소 기동 가능 수준:
  - `1 vCPU`
  - `1 GB RAM`
  - `2 GB swap`
  - `20 GB SSD`
- 개인용 권장:
  - `2 vCPU`
  - `4 GB RAM`
  - `20-40 GB SSD`
- 로컬 검증이 더 무거운 경우:
  - `4 vCPU`
  - `8 GB RAM`
  - `40 GB+ SSD`

메모:

- `1 vCPU / 1 GB RAM` 도 개인용 봇 하나는 돌릴 수 있지만, 가벼운 작업 위주일 때만 안정적입니다.
- 무거운 `pytest`, JavaScript 빌드, Docker 빌드, 동시 세션 증가는 메모리 압박으로 느려지거나 실패할 수 있습니다.
- GPU는 필요하지 않습니다.

## 권장 순서

1. `codex` 설치 및 인증
2. `gemini` 설치 및 인증
3. 이 저장소 클론
4. 부트스트랩 스크립트 실행

예시:

```bash
sudo bash ./deploy/install-single-node.sh \
  --telegram-token "123456:replace-me" \
  --allowed-user-id "123456789"
```

런타임 사용자와 워크스페이스 경로를 명시하려면:

```bash
sudo bash ./deploy/install-single-node.sh \
  --runtime-user "ubuntu" \
  --telegram-token "123456:replace-me" \
  --allowed-user-id "123456789" \
  --workspace-root "/opt/telecodex/current"
```

## 생성 파일

- worker config: `/etc/telecodex/worker.single-node.yaml`
- gateway config: `/etc/telecodex/gateway.single-node.yaml`
- worker env: `/etc/telecodex/worker.env`
- gateway env: `/etc/telecodex/gateway.env`
- app root: `/opt/telecodex`

## 런타임 구조

- `worker` 는 `127.0.0.1:8081` 에 바인딩
- `gateway` 는 `http://127.0.0.1:8081` 로 worker 호출
- `worker` 는 외부에 공개하지 않음
- Telegram 트래픽은 gateway 프로세스에만 도달

## 서비스 관리

```bash
sudo systemctl status telecodex-worker
sudo systemctl status telecodex-gateway
```

```bash
sudo systemctl restart telecodex-worker
sudo systemctl restart telecodex-gateway
```

Worker health 확인:

```bash
source /etc/telecodex/worker.env
curl -H "X-Worker-Token: $TELECODEX_WORKER_TOKEN" http://127.0.0.1:8081/health
```

## 메모

- 이 모드는 외부 AI CLI가 호스트에 설치되고 인증 상태도 호스트가 관리하므로, Docker보다 `systemd + venv` 조합이 더 잘 맞습니다.
- 나중에 Docker 기반 단일 노드 모드를 추가할 수는 있지만, 기본 설치 경로보다는 선택 옵션으로 남겨두는 편이 맞습니다.
