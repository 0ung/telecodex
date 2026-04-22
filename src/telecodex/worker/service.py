from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status

from telecodex.shared.config import WorkerConfig
from telecodex.shared.models import (
    AIStatusResponse,
    CancelResponse,
    HealthResponse,
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
from telecodex.worker.ai_status import AiRuntimeStatusService
from telecodex.worker.orchestrator import SessionManager


def create_worker_app(cfg: WorkerConfig) -> FastAPI:
    app = FastAPI(title="telecodex-worker", version="0.2.0")
    manager = SessionManager(cfg)
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

    @app.post("/sessions", response_model=SessionCreateResponse, status_code=status.HTTP_201_CREATED)
    def create_session(request: SessionRequest, _: None = Depends(authorize)) -> SessionCreateResponse:
        try:
            summary = manager.create_session(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return SessionCreateResponse(session_id=summary.session_id, state=summary.state, verdict=summary.verdict)

    @app.get("/sessions", response_model=SessionListResponse)
    def list_sessions(
        channel: str | None = Query(default=None),
        conversation_id: str | None = Query(default=None),
        active_only: bool = Query(default=False),
        _: None = Depends(authorize),
    ) -> SessionListResponse:
        return SessionListResponse(
            sessions=manager.list_sessions(channel=channel, conversation_id=conversation_id, active_only=active_only)
        )

    @app.get("/sessions/{session_id}", response_model=SessionDetail)
    def get_session(session_id: str, _: None = Depends(authorize)) -> SessionDetail:
        try:
            return manager.get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from exc

    @app.post("/sessions/{session_id}/continue", response_model=SessionContinueResponse)
    def continue_session(session_id: str, request: SessionContinueRequest, _: None = Depends(authorize)) -> SessionContinueResponse:
        try:
            summary = manager.continue_session(session_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from exc
        return SessionContinueResponse(session_id=summary.session_id, state=summary.state, verdict=summary.verdict)

    @app.post("/sessions/{session_id}/cancel", response_model=CancelResponse)
    def cancel_session(session_id: str, _: None = Depends(authorize)) -> CancelResponse:
        try:
            summary = manager.cancel_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found") from exc
        return CancelResponse(accepted=True, session_id=summary.session_id, session_state=summary.state)

    @app.post("/jobs", response_model=JobCreateResponse, status_code=status.HTTP_201_CREATED)
    def create_job(request: JobRequest, _: None = Depends(authorize)) -> JobCreateResponse:
        summary = manager.create_job(request)
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
        return CancelResponse(job_id=summary.job_id, state=summary.state, accepted=True, session_id=summary.session_id)

    return app
