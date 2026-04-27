from __future__ import annotations

from datetime import datetime

from telecodex.shared.models import (
    AIStatusResponse,
    CancelResponse,
    JobCreateResponse,
    JobDetail,
    JobListResponse,
    JobRequest,
    SessionContinueRequest,
    SessionContinueResponse,
    SessionCreateResponse,
    SessionDetail,
    SessionListResponse,
    SessionRequest,
)
from telecodex.shared.http_client import ResilientHttpClient


class WorkerClient:
    def __init__(
        self,
        base_url: str,
        worker_token: str = "",
        timeout_sec: int = 30,
        http_client: ResilientHttpClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_token = worker_token
        self.timeout_sec = timeout_sec
        self.http = http_client

    def create_session(self, request: SessionRequest) -> SessionCreateResponse:
        response = self._http().post(
            f"{self.base_url}/sessions",
            headers=self._headers(),
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        return SessionCreateResponse.model_validate(response.json())

    def list_sessions(
        self,
        channel: str | None = None,
        conversation_id: str | None = None,
        active_only: bool = False,
        updated_after: datetime | None = None,
    ) -> SessionListResponse:
        params = {}
        if channel:
            params["channel"] = channel
        if conversation_id:
            params["conversation_id"] = conversation_id
        if active_only:
            params["active_only"] = "true"
        if updated_after:
            params["updated_after"] = updated_after.isoformat()
        response = self._http().get(
            f"{self.base_url}/sessions",
            headers=self._headers(),
            params=params,
            retryable=True,
        )
        response.raise_for_status()
        return SessionListResponse.model_validate(response.json())

    def get_session(self, session_id: str) -> SessionDetail:
        response = self._http().get(f"{self.base_url}/sessions/{session_id}", headers=self._headers(), retryable=True)
        response.raise_for_status()
        return SessionDetail.model_validate(response.json())

    def continue_session(self, session_id: str, request: SessionContinueRequest) -> SessionContinueResponse:
        response = self._http().post(
            f"{self.base_url}/sessions/{session_id}/continue",
            headers=self._headers(),
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        return SessionContinueResponse.model_validate(response.json())

    def cancel_session(self, session_id: str) -> CancelResponse:
        response = self._http().post(f"{self.base_url}/sessions/{session_id}/cancel", headers=self._headers())
        response.raise_for_status()
        return CancelResponse.model_validate(response.json())

    def ai_status(self) -> AIStatusResponse:
        response = self._http().get(f"{self.base_url}/ai/status", headers=self._headers(), retryable=True)
        response.raise_for_status()
        return AIStatusResponse.model_validate(response.json())

    def create_job(self, request: JobRequest) -> JobCreateResponse:
        response = self._http().post(
            f"{self.base_url}/jobs",
            headers=self._headers(),
            json=request.model_dump(mode="json"),
        )
        response.raise_for_status()
        return JobCreateResponse.model_validate(response.json())

    def list_jobs(self) -> JobListResponse:
        response = self._http().get(f"{self.base_url}/jobs", headers=self._headers(), retryable=True)
        response.raise_for_status()
        return JobListResponse.model_validate(response.json())

    def get_job(self, job_id: str) -> JobDetail:
        response = self._http().get(f"{self.base_url}/jobs/{job_id}", headers=self._headers(), retryable=True)
        response.raise_for_status()
        return JobDetail.model_validate(response.json())

    def cancel_job(self, job_id: str) -> CancelResponse:
        response = self._http().post(f"{self.base_url}/jobs/{job_id}/cancel", headers=self._headers())
        response.raise_for_status()
        return CancelResponse.model_validate(response.json())

    def _headers(self) -> dict[str, str]:
        headers = {}
        if self.worker_token:
            headers["X-Worker-Token"] = self.worker_token
        return headers

    def _http(self) -> ResilientHttpClient:
        if self.http is None:
            raise RuntimeError("worker client requires an http client")
        return self.http
