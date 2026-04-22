from __future__ import annotations

import httpx

from telecodex.shared.models import (
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


class WorkerClient:
    def __init__(self, base_url: str, worker_token: str = "", timeout_sec: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_token = worker_token
        self.timeout_sec = timeout_sec

    def create_session(self, request: SessionRequest) -> SessionCreateResponse:
        response = httpx.post(
            f"{self.base_url}/sessions",
            headers=self._headers(),
            json=request.model_dump(mode="json"),
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return SessionCreateResponse.model_validate(response.json())

    def list_sessions(
        self,
        channel: str | None = None,
        conversation_id: str | None = None,
        active_only: bool = False,
    ) -> SessionListResponse:
        params = {}
        if channel:
            params["channel"] = channel
        if conversation_id:
            params["conversation_id"] = conversation_id
        if active_only:
            params["active_only"] = "true"
        response = httpx.get(
            f"{self.base_url}/sessions",
            headers=self._headers(),
            params=params,
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return SessionListResponse.model_validate(response.json())

    def get_session(self, session_id: str) -> SessionDetail:
        response = httpx.get(f"{self.base_url}/sessions/{session_id}", headers=self._headers(), timeout=self.timeout_sec)
        response.raise_for_status()
        return SessionDetail.model_validate(response.json())

    def continue_session(self, session_id: str, request: SessionContinueRequest) -> SessionContinueResponse:
        response = httpx.post(
            f"{self.base_url}/sessions/{session_id}/continue",
            headers=self._headers(),
            json=request.model_dump(mode="json"),
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return SessionContinueResponse.model_validate(response.json())

    def cancel_session(self, session_id: str) -> CancelResponse:
        response = httpx.post(f"{self.base_url}/sessions/{session_id}/cancel", headers=self._headers(), timeout=self.timeout_sec)
        response.raise_for_status()
        return CancelResponse.model_validate(response.json())

    def create_job(self, request: JobRequest) -> JobCreateResponse:
        response = httpx.post(
            f"{self.base_url}/jobs",
            headers=self._headers(),
            json=request.model_dump(mode="json"),
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        return JobCreateResponse.model_validate(response.json())

    def list_jobs(self) -> JobListResponse:
        response = httpx.get(f"{self.base_url}/jobs", headers=self._headers(), timeout=self.timeout_sec)
        response.raise_for_status()
        return JobListResponse.model_validate(response.json())

    def get_job(self, job_id: str) -> JobDetail:
        response = httpx.get(f"{self.base_url}/jobs/{job_id}", headers=self._headers(), timeout=self.timeout_sec)
        response.raise_for_status()
        return JobDetail.model_validate(response.json())

    def cancel_job(self, job_id: str) -> CancelResponse:
        response = httpx.post(f"{self.base_url}/jobs/{job_id}/cancel", headers=self._headers(), timeout=self.timeout_sec)
        response.raise_for_status()
        return CancelResponse.model_validate(response.json())

    def _headers(self) -> dict[str, str]:
        headers = {}
        if self.worker_token:
            headers["X-Worker-Token"] = self.worker_token
        return headers
