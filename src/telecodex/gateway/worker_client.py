from __future__ import annotations

from typing import Any

import httpx

from telecodex.shared.models import CancelResponse, JobCreateResponse, JobDetail, JobListResponse, JobRequest


class WorkerClient:
    def __init__(self, base_url: str, worker_token: str = "", timeout_sec: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_token = worker_token
        self.timeout_sec = timeout_sec

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
