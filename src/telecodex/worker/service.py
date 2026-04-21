from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, status

from telecodex.shared.config import WorkerConfig
from telecodex.shared.models import AIStatusResponse, CancelResponse, HealthResponse, JobCreateResponse, JobDetail, JobListResponse, JobRequest
from telecodex.worker.ai_status import AiRuntimeStatusService
from telecodex.worker.orchestrator import JobManager


def create_worker_app(cfg: WorkerConfig) -> FastAPI:
    app = FastAPI(title="telecodex-worker", version="0.1.0")
    manager = JobManager(cfg)
    ai_status = AiRuntimeStatusService(cfg)

    def authorize(x_worker_token: str | None = Header(default=None)) -> None:
        if cfg.worker_token and x_worker_token != cfg.worker_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid worker token")

    @app.get("/health", response_model=HealthResponse)
    def health(_: None = Depends(authorize)) -> HealthResponse:
        return HealthResponse(**manager.health())

    @app.get("/ai/status", response_model=AIStatusResponse)
    def get_ai_status(_: None = Depends(authorize)) -> AIStatusResponse:
        return ai_status.build()

    @app.post("/jobs", response_model=JobCreateResponse, status_code=status.HTTP_201_CREATED)
    def create_job(request: JobRequest, _: None = Depends(authorize)) -> JobCreateResponse:
        try:
            summary = manager.create_job(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return JobCreateResponse(job_id=summary.job_id, state=summary.state)

    @app.get("/jobs", response_model=JobListResponse)
    def list_jobs(_: None = Depends(authorize)) -> JobListResponse:
        return JobListResponse(jobs=manager.list_jobs())

    @app.get("/jobs/{job_id}", response_model=JobDetail)
    def get_job(job_id: str, _: None = Depends(authorize)) -> JobDetail:
        try:
            return manager.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from exc

    @app.post("/jobs/{job_id}/cancel", response_model=CancelResponse)
    def cancel_job(job_id: str, _: None = Depends(authorize)) -> CancelResponse:
        try:
            summary = manager.cancel_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from exc
        return CancelResponse(job_id=summary.job_id, state=summary.state, accepted=True)

    return app
