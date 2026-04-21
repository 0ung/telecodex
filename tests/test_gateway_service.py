from __future__ import annotations

from telecodex.gateway.service import GatewayService
from telecodex.shared.config import GatewayConfig
from telecodex.shared.models import JobCreateResponse, JobDetail, JobListResponse, JobRequest, JobState, JobSummary


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    def get_updates(self, offset, timeout_sec):  # noqa: ANN001, D401
        return []

    def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


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


def test_gateway_service_rejects_non_text() -> None:
    telegram = FakeTelegram()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        telegram=telegram,
        worker=FakeWorker(),
    )
    service._handle_message({"chat": {"id": 10, "type": "private"}, "from": {"id": 1}})
    assert telegram.messages[-1][1] == "Only text requests are supported."


def test_gateway_service_starts_job_from_plain_text() -> None:
    telegram = FakeTelegram()
    service = GatewayService(
        cfg=GatewayConfig(
            telegram_token="token",
            allowed_user_ids=[1],
            worker_base_url="http://worker",
        ),
        telegram=telegram,
        worker=FakeWorker(),
    )
    service._handle_message({"chat": {"id": 10, "type": "private"}, "from": {"id": 1}, "text": "build this"})
    assert "job-1" in telegram.messages[-1][1]
