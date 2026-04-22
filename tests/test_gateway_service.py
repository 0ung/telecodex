from __future__ import annotations

from datetime import timedelta

from telecodex.gateway.interfaces import IncomingMessage
from telecodex.gateway.service import GatewayService
from telecodex.shared.config import GatewayConfig
from telecodex.shared.models import (
    JobAttachment,
    CancelResponse,
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
                goal="goal",
                state=SessionState.WAITING_USER,
                verdict=SessionVerdict.ASK_USER,
            ),
            request=SessionRequest(goal="goal", requester_id=1, workspace_path=".", channel="telegram", conversation_id="10"),
            acceptance_criteria=["Implement the requested change", "Summarize the outcome for the user"],
            completed_acceptance_criteria=["Implement the requested change"],
            gemini_review="Reviewed the latest work and one user confirmation is still needed before closing.",
            codex_execution="Updated the gateway response formatting to expose a compact summary to the user.",
            next_action="Need one more answer from the user.",
        )

    def create_session(self, request: SessionRequest) -> SessionCreateResponse:
        self.created_requests.append(request)
        self.active_detail.summary.goal = request.goal
        self.active_detail.summary.state = SessionState.PLANNING
        self.active_detail.summary.verdict = SessionVerdict.CONTINUE
        self.active_detail.request = request
        self.active_detail.gemini_plan = "Planner is turning the goal into acceptance criteria and the first Codex task."
        self.active_detail.gemini_review = "Planner captured the request and is preparing a concise implementation brief."
        self.active_detail.codex_execution = ""
        self.active_detail.next_action = "Gemini is preparing the first Codex instruction."
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
    assert chat.messages[-1][1] == "Send text, a photo, or both."


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
    assert "Summary" in chat.messages[-1][1]
    assert "Current focus" in chat.messages[-1][1]
    assert worker.created_requests[-1].goal == "build this"


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
    assert "attachment" in chat.messages[-1][1]


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
    assert "Resumed session" in chat.messages[-1][1]
    assert "Needs from you" in chat.messages[-1][1]
    assert "Need one more answer from the user." in chat.messages[-1][1]


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
    assert "Summary" in body
    assert "Gemini:" in body
    assert "Codex:" in body
    assert "Needs from you" in body


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
    assert "needs your input" in chat.messages[-1][1]

    service._push_session_updates_once()
    assert len(chat.messages) == 1

    worker.active_detail.summary.updated_at = worker.active_detail.summary.updated_at + timedelta(seconds=1)
    worker.active_detail.summary.state = SessionState.COMPLETED
    worker.active_detail.summary.verdict = SessionVerdict.DONE
    worker.active_detail.final_outcome = "The work is complete."
    worker.active_detail.next_action = ""
    service._push_session_updates_once()
    assert len(chat.messages) == 2
    assert "completed" in chat.messages[-1][1]


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
