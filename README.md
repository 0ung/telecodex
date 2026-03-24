# Telecodex

Telegram으로 로컬 Codex를 제어하는 작은 도구입니다.

```text
Telegram -> telecodex -> codex app-server -> Codex
```

---

## 특징

- 로컬 전용
- 서버 없음
- webhook 없음
- DB 없음
- 단일 사용자
- 단일 작업

---

## 설치

### 1. 권장: 직접 빌드

```bash
git clone https://github.com/0ung/telecodex.git
cd telecodex
go build -o telecodex ./cmd/app
./telecodex -config ./config.yaml
```

가장 안전하고 권장되는 방식입니다.

### 2. 바이너리 사용

👉 https://github.com/0ung/telecodex/releases

SmartScreen 경고가 표시될 수 있지만 정상 동작할 수 있습니다.

---

## 왜 SmartScreen 경고가 표시되나요?

Windows에서 공개 릴리즈를 내려받아 실행하면 Microsoft Defender SmartScreen이 실행을 차단하거나 경고를 표시할 수 있습니다.

이 경고는 주로 다음 상황에서 표시됩니다.

- 실행 파일에 Authenticode 코드 서명이 없습니다.
- 새로 배포된 파일이라 Windows가 참고할 수 있는 평판 정보가 충분하지 않습니다.

SmartScreen은 평판 기반 보호 기능이므로, 파일이 악성코드인지 아닌지를 단순히 즉시 단정하는 것이 아니라 서명 여부, 게시자 정보, 파일의 배포 및 실행 이력 등을 함께 평가합니다.

즉, 현재 경고가 표시된다는 사실만으로 파일이 악성이라는 뜻은 아닙니다. 다만 Windows가 이 실행 파일을 신뢰할 만한 근거를 아직 충분히 확보하지 못했다는 의미에 가깝습니다.

현재 릴리즈를 바로 실행하려면 다음 순서로 진행하면 됩니다.

- `추가 정보`를 클릭합니다.
- `실행`을 클릭합니다.

현재 프로젝트는 취미용 배포를 전제로 하고 있으므로, README에서는 직접 빌드를 우선 권장합니다.

---

## 준비

### 1. Telegram Bot

- BotFather로 생성합니다.
- `bot_token`을 확보합니다.

### 2. 사용자 ID 확인

아래 주소를 호출한 뒤 `message.from.id` 값을 확인합니다.

`https://api.telegram.org/bot<TOKEN>/getUpdates`

### 3. Codex 설치

```bash
npm install -g @openai/codex
```

설치 후 아래 명령으로 확인합니다.

```bash
codex --version
codex app-server --help
```

Telecodex는 설치된 `codex` 명령을 자동으로 찾습니다. 따라서 `config.yaml`에 별도의 실행 경로를 넣을 필요는 없습니다.

---

## 설정

`config.yaml` 파일을 직접 만들고 아래 내용을 넣으면 됩니다.

```yaml
# BotFather에서 받은 Telegram bot token
bot_token: "1234567890:AAEXAMPLE_REPLACE_ME"

# 본인 Telegram user id
allowed_user_id: 123456789

# 언어 설정: ko 또는 en
language: "ko"

# Codex 모델을 직접 지정할 때 사용, 비워두면 기본값 사용
model: ""

# 고정으로 사용할 로컬 작업 폴더
workspace_dir: "C:/Users/your-name/work/my-project"

# Telegram long polling 타임아웃(초)
poll_timeout_sec: 30

# 현재는 호환성 유지를 위해 남겨둔 값
progress_update_sec: 2

# 로컬 로그 파일 경로
log_file: "./app.log"
```

---

## 실행

```bash
./telecodex -config ./config.yaml
```

Windows에서는 `telecodex.exe`를 실행하면 됩니다.

---

## 사용

- 일반 메시지: 작업 실행
- `/status`: 상태 확인
- `/cancel`: 중단 요청
- `/help`: 명령 확인

---

## 구조

- Telegram → long polling
- telecodex → 로컬 실행
- Codex → workspace에서 작업

---

## 철학

플랫폼이 아닙니다. 그냥 브리지입니다.

---

## 라이선스

MIT
