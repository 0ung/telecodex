from __future__ import annotations

import logging
from datetime import timedelta

from telecodex.gateway.interfaces import IncomingMessage
from telecodex.gateway.service import GatewayService
from telecodex.shared.config import GatewayConfig
from telecodex.shared.models import (
    AIStatusResponse,
    JobAttachment,
    CancelResponse,
    ProviderQuota,
    ProviderRuntimeStatus,
    SessionContinueRequest,
    SessionContinueResponse,
    SessionCreateResponse,
    SessionDetail,
    SessionListResponse,
    SessionRequest,
    SessionState,
    SessionSummary,
    SessionVerdict,
)


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def poll_messages(self, timeout_sec):  # noqa: ANN001, D401
        return []

    def send_message(self, conversation_id: str, text: str) -> None:
        self.messages.append((conversation_id, text))


class FakeWorker:
    def __init__(self) -> None:
        self.created_requests: list[SessionRequest] = []
        self.continue_requests: list[tuple[str, SessionContinueRequest]] = []
        self.active_detail = SessionDetail(
            summary=SessionSummary(
                session_id="session-1",
                channel="telegram",
                conversation_id="10",
                goal="이력서 작성",
                state=SessionState.WAITING_USER,
                verdict=SessionVerdict.ASK_USER,
            ),
            request=SessionRequest(goal="이력서 작성", requester_id=1, workspace_path=".", channel="telegram", conversation_id="10"),
            acceptance_criteria=["이력서 초안을 만든다", "사용자에게 결과를 요약한다"],
            completed_acceptance_criteria=["이력서 초안을 만든다"],
            gemini_review="최신 작업을 검토했고, 마무리를 위해 사용자 확인이 하나 더 필요합니다.",
            codex_execution="게이트웨이 응답 포맷을 수정해 사용자에게 더 읽기 쉬운 요약을 보여주도록 바꿨습니다.",
            next_action="이력서 초안을 완성하려면 아래 정보를 알려주세요.\n- 이름\n- 이메일\n- 주요 경력",
        )

    def create_session(self, request: SessionRequest) -> SessionCreateResponse:
        self.created_requests.append(request)
        self.active_detail.summary.goal = request.goal
        self.active_detail.summary.state = SessionState.PLANNING
        self.active_detail.summary.verdict = SessionVerdict.CONTINUE
        self.active_detail.request = request
        self.active_detail.gemini_plan = "목표를 완료 조건으로 정리하고 첫 Codex 작업 지시를 준비하고 있습니다."
        self.active_detail.gemini_review = "요청을 반영해 바로 구현 가능한 작업 지시로 다듬는 중입니다."
        self.active_detail.codex_execution = ""
        self.active_detail.next_action = "Gemini가 첫 번째 Codex 작업 지시를 정리하고 있습니다."
        return SessionCreateResponse(session_id="session-1", state=SessionState.PLANNING, verdict=SessionVerdict.CONTINUE)

    def list_sessions(self, channel=None, conversation_id=None, active_only=False):  # noqa: ANN001
        if active_only:
            return SessionListResponse(sessions=[self.active_detail.summary])
        return SessionListResponse(sessions=[self.active_detail.summary])

    def get_session(self, session_id: str) -> SessionDetail:
        return self.active_detail

    def continue_session(self, session_id: str, request: SessionContinueRequest) -> SessionContinueResponse:
        self.continue_requests.append((session_id, request))
        return SessionContinueResponse(session_id=session_id, state=SessionState.WAITING_USER, verdict=SessionVerdict.ASK_USER)

    def cancel_session(self, session_id: str) -> CancelResponse:
        return CancelResponse(accepted=True, session_id=session_id, session_state=SessionState.CANCELED)

    def ai_status(self) -> AIStatusResponse:
        return AIStatusResponse(
            codex=ProviderRuntimeStatus(provider="codex", auth_ok=True, auth_message="Logged in", configured_model="default"),
            gemini=ProviderRuntimeStatus(
                provider="gemini",
                auth_ok=True,
                auth_message="Gemini API key is configured.",
                configured_model="gemini-2.5-flash",
                quota=ProviderQuota(requests_per_minute=15, requests_per_day=1000, tokens_per_minute=250000),
            ),
        )


def test_gateway_service_rejects_non_text() -> None:
    chat = FakeChat()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=FakeWorker(),
    )
    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1))
    assert chat.messages[-1][1] == "텍스트나 사진을 함께 보내주세요."


def test_gateway_service_starts_session_from_plain_text() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.summary.state = SessionState.COMPLETED
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )
    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/run build this"))
    assert "session-1" in chat.messages[-1][1]
    assert "진행 요약" in chat.messages[-1][1]
    assert "다음 단계" in chat.messages[-1][1]
    assert worker.created_requests[-1].goal == "build this"


def test_gateway_service_requires_goal_for_bare_run_command() -> None:
    chat = FakeChat()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=FakeWorker(),
    )
    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/run"))
    assert chat.messages[-1][1] == "사용법: `/run <목표>` 또는 설명이 붙은 사진을 보내주세요."


def test_gateway_service_starts_session_from_photo_caption() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.summary.state = SessionState.COMPLETED
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )
    service._handle_message(
        IncomingMessage(
            channel="telegram",
            conversation_id="10",
            sender_id=1,
            text="/run analyze this image",
            attachments=[
                JobAttachment(
                    kind="photo",
                    file_name="image.jpg",
                    mime_type="image/jpeg",
                    content_base64="aW1hZ2UtYnl0ZXM=",
                )
            ],
        )
    )
    assert "첨부 1개" in chat.messages[-1][1]


def test_gateway_service_reports_attachment_validation_errors() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(
        IncomingMessage(
            channel="telegram",
            conversation_id="10",
            sender_id=1,
            text="/run analyze this image",
            attachment_errors=["`archive.zip` 파일 형식 `application/zip` 은(는) 아직 지원하지 않습니다."],
        )
    )

    assert "첨부를 처리할 수 없습니다." in chat.messages[-1][1]
    assert "application/zip" in chat.messages[-1][1]
    assert worker.created_requests == []


def test_gateway_service_continues_waiting_session() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )
    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="use main"))
    assert worker.continue_requests[-1][0] == "session-1"
    assert "최신 입력을 반영" in chat.messages[-1][1]
    assert "필요한 정보" in chat.messages[-1][1]
    assert "이름" in chat.messages[-1][1]


def test_gateway_service_status_shows_summary_sections() -> None:
    chat = FakeChat()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=FakeWorker(),
    )
    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/status"))
    body = chat.messages[-1][1]
    assert "진행 요약" in body
    assert "계획" in body
    assert "실행" in body
    assert "필요한 정보" in body


def test_gateway_service_reports_ai_runtime_status() -> None:
    chat = FakeChat()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=FakeWorker(),
    )
    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/ai status"))
    body = chat.messages[-1][1]
    assert "AI 런타임 상태" in body
    assert "Codex: 준비됨" in body
    assert "Gemini: 준비됨" in body


def test_gateway_service_pushes_session_updates_once_per_change() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
            session_push_interval_sec=1,
        ),
        chat=chat,
        worker=worker,
    )

    service._push_session_updates_once()
    assert len(chat.messages) == 1
    assert "추가 정보가 필요합니다" in chat.messages[-1][1]

    service._push_session_updates_once()
    assert len(chat.messages) == 1

    worker.active_detail.summary.updated_at = worker.active_detail.summary.updated_at + timedelta(seconds=1)
    worker.active_detail.summary.state = SessionState.COMPLETED
    worker.active_detail.summary.verdict = SessionVerdict.DONE
    worker.active_detail.final_outcome = "The work is complete."
    worker.active_detail.next_action = ""
    service._push_session_updates_once()
    assert len(chat.messages) == 2
    assert "완료되었습니다" in chat.messages[-1][1]


def test_gateway_service_primes_existing_sessions_before_starting_watcher() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.summary.state = SessionState.CANCELED
    worker.active_detail.summary.verdict = SessionVerdict.FAIL
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
            session_push_interval_sec=1,
        ),
        chat=chat,
        worker=worker,
    )

    service._prime_push_state()
    service._push_session_updates_once()

    assert chat.messages == []

    worker.active_detail.summary.updated_at = worker.active_detail.summary.updated_at + timedelta(seconds=1)
    worker.active_detail.summary.state = SessionState.COMPLETED
    worker.active_detail.summary.verdict = SessionVerdict.DONE
    worker.active_detail.final_outcome = "The work is complete."
    worker.active_detail.next_action = ""
    service._push_session_updates_once()

    assert len(chat.messages) == 1


def test_gateway_service_surfaces_runtime_error_in_summary() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.summary.state = SessionState.FAILED
    worker.active_detail.summary.verdict = SessionVerdict.FAIL
    worker.active_detail.error = "gemini adapter returned empty stdout; stderr: rate limit exceeded"
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/status"))

    body = chat.messages[-1][1]
    assert "오류" in body
    assert "rate limit exceeded" in body


def test_gateway_service_logs_status_failures(caplog) -> None:  # noqa: ANN001
    chat = FakeChat()
    worker = FakeWorker()

    def raising_latest_session(channel, conversation_id):  # noqa: ANN001
        raise RuntimeError("worker unavailable")

    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )
    service._latest_session = raising_latest_session  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/status"))

    assert "status_command_failed" in caplog.text
    assert "conversation_id=10" in caplog.text
    assert "worker unavailable" in chat.messages[-1][1]


def test_gateway_service_logs_watcher_failures(caplog) -> None:  # noqa: ANN001
    chat = FakeChat()
    worker = FakeWorker()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    def raising_push_once() -> None:
        raise RuntimeError("push loop failed")

    service._push_session_updates_once = raising_push_once  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        service._watcher_stop.set()
        service._watcher_stop.clear()
        service._watch_session_updates = GatewayService._watch_session_updates.__get__(service, GatewayService)
        try:
            service._push_session_updates_once()
        except RuntimeError:
            service._log_exception("session_update_push_failed", RuntimeError("push loop failed"), interval=1)

    assert "session_update_push_failed" in caplog.text
    assert "push loop failed" in caplog.text


def test_gateway_service_run_message_seeds_push_cache() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.summary.state = SessionState.COMPLETED
    worker.active_detail.summary.verdict = SessionVerdict.DONE
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
            session_push_interval_sec=1,
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/run build this"))
    assert len(chat.messages) == 1

    service._push_session_updates_once()
    assert len(chat.messages) == 1


def test_gateway_service_ignores_non_direct_messages() -> None:
    chat = FakeChat()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=FakeWorker(),
    )
    service._handle_message(IncomingMessage(channel="slack", conversation_id="C1", sender_id=1, text="build this", is_direct_message=False))
    assert chat.messages == []


def test_gateway_service_prompts_instead_of_starting_meta_session() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.summary.state = SessionState.COMPLETED

    def fake_list_sessions(channel=None, conversation_id=None, active_only=False):  # noqa: ANN001
        if active_only:
            return SessionListResponse(sessions=[])
        return SessionListResponse(sessions=[worker.active_detail.summary])

    worker.list_sessions = fake_list_sessions  # type: ignore[method-assign]
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="다시 질문할게"))

    assert chat.messages[-1][1] == "좋아요. 질문이나 요청을 한 문장으로 보내주세요."
    assert worker.created_requests == []


def test_gateway_service_strips_raw_json_from_summary() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.summary.state = SessionState.COMPLETED
    worker.active_detail.summary.verdict = SessionVerdict.DONE
    worker.active_detail.final_outcome = (
        '{"status":"done","summary_for_user":"Gemini, Codex, MCP 각각의 역할을 설명합니다.","next_action":""}'
    )
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/status"))

    body = chat.messages[-1][1]
    assert "Gemini, Codex, MCP 각각의 역할을 설명합니다." in body
    assert '"status"' not in body


def test_gateway_service_routes_status_like_message_to_status() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="끝이야?"))

    assert worker.continue_requests == []
    assert "최근 세션" in chat.messages[-1][1]


def test_gateway_service_routes_progress_nudge_to_status() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="진행중이야?"))

    assert worker.continue_requests == []
    assert "최근 세션" in chat.messages[-1][1]


def test_gateway_service_starts_new_session_for_korean_goal_redirect() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    text = "아니야 이제 부산 관광사이트 깃허브 연결해서 세팅하자"
    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text=text))

    assert worker.continue_requests == []
    assert worker.created_requests[-1].goal == text
    assert "목표: 아니야 이제 부산 관광사이트 깃허브 연결해서 세팅하자" in chat.messages[-1][1]


def test_gateway_service_starts_new_session_for_capability_question_during_active_session() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(
        IncomingMessage(
            channel="telegram",
            conversation_id="10",
            sender_id=1,
            text="지금 gemini, codex, mcp가 각각 뭘 할 수 있어?",
        )
    )

    assert worker.continue_requests == []
    assert worker.created_requests[-1].goal == "지금 gemini, codex, mcp가 각각 뭘 할 수 있어?"


def test_gateway_service_localizes_known_english_planner_text() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.gemini_plan = (
        "The user's goal is too general to proceed. I need to ask for more specific details about the development task they wish to undertake."
    )
    worker.active_detail.gemini_review = ""
    worker.active_detail.next_action = "어떤 개발 작업을 시작하고 싶으신가요?"
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/status"))

    body = chat.messages[-1][1]
    assert "사용자 목표가 아직 너무 넓어서 바로 진행할 수 없습니다." in body


def test_gateway_service_strips_bulletized_json_from_user_visible_sections() -> None:
    chat = FakeChat()
    worker = FakeWorker()
    worker.active_detail.gemini_plan = """
{
- "status": "continue",
- "summary_for_user": "부산 관광사이트의 GitHub 연결과 배포 설정을 확인하는 단계입니다.",
- "instruction_for_codex": "print(codebase_investigator.investigate(objective='secret'))",
- "acceptance_criteria": [
- "Gitflow 전략을 적용한다."
- ],
- "next_action": "프로젝트의 Git 설정과 배포 구성을 확인합니다."
}
"""
    worker.active_detail.gemini_review = ""
    worker.active_detail.next_action = worker.active_detail.gemini_plan
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        chat=chat,
        worker=worker,
    )

    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="/status"))

    body = chat.messages[-1][1]
    assert "부산 관광사이트의 GitHub 연결과 배포 설정을 확인하는 단계입니다." in body
    assert '"status"' not in body
    assert "instruction_for_codex" not in body
    assert "codebase_investigator" not in body
