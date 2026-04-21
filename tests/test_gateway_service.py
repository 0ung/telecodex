from __future__ import annotations

from telecodex.gateway.interfaces import IncomingMessage
from telecodex.gateway.service import GatewayService
from telecodex.shared.config import GatewayConfig
from telecodex.shared.models import AIStatusResponse, JobAttachment, JobCreateResponse, JobDetail, JobListResponse, JobRequest, JobState, JobSummary, ProviderQuota, ProviderRuntimeStatus


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def poll_messages(self, timeout_sec):  # noqa: ANN001, D401
        return []

    def send_message(self, conversation_id: str, text: str) -> None:
        self.messages.append((conversation_id, text))


class FakeWorker:
    def create_job(self, request: JobRequest) -> JobCreateResponse:
        return JobCreateResponse(job_id="job-1", state=JobState.QUEUED)

    def list_jobs(self) -> JobListResponse:
        return JobListResponse(jobs=[JobSummary(job_id="job-1", state=JobState.COMPLETED, goal="goal")])

    def get_job(self, job_id: str) -> JobDetail:
        summary = JobSummary(job_id=job_id, state=JobState.COMPLETED, goal="goal")
        return JobDetail(summary=summary, request=JobRequest(goal="goal", requester_id=1, workspace_path="."))

    def cancel_job(self, job_id: str):  # noqa: ANN001
        raise NotImplementedError

    def ai_status(self) -> AIStatusResponse:
        return AIStatusResponse(
            codex=ProviderRuntimeStatus(provider="codex", auth_ok=True, auth_message="Logged in", configured_model="default"),
            gemini=ProviderRuntimeStatus(
                provider="gemini",
                auth_ok=True,
                auth_message="Gemini API key is configured.",
                configured_model="gemini-2.5-flash-lite",
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
    assert chat.messages[-1][1] == "Send text, a photo, or both."


def test_gateway_service_starts_job_from_plain_text() -> None:
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
    service._handle_message(IncomingMessage(channel="telegram", conversation_id="10", sender_id=1, text="build this"))
    assert "job-1" in chat.messages[-1][1]


def test_gateway_service_starts_job_from_photo_caption() -> None:
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
    service._handle_message(
        IncomingMessage(
            channel="telegram",
            conversation_id="10",
            sender_id=1,
            text="analyze this image",
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


def test_gateway_service_shows_ai_status() -> None:
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
    assert "AI Runtime Status" in chat.messages[-1][1]
    assert "gemini-2.5-flash-lite" in chat.messages[-1][1]
