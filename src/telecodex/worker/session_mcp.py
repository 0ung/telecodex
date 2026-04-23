from __future__ import annotations

import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from telecodex.shared.models import JobAttachment, SessionMcpConfig, SessionRequest, SessionState, SessionVerdict
from telecodex.worker.session_docs import SessionDocumentStore, merge_checklist, merge_text


class SessionMcpService:
    def __init__(self, store: SessionDocumentStore) -> None:
        self.store = store

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "session_read": self.session_read,
            "session_create_or_resume": self.session_create_or_resume,
            "session_update_user_input": self.session_update_user_input,
            "session_write_gemini_sections": self.session_write_gemini_sections,
            "session_write_codex_sections": self.session_write_codex_sections,
            "session_set_verdict": self.session_set_verdict,
            "session_list_recent": self.session_list_recent,
        }
        if method not in handlers:
            raise KeyError(f"unsupported session MCP method: {method}")
        return handlers[method](**params)

    def session_read(self, session_id: str) -> dict[str, Any]:
        request = self.store.load_request(session_id)
        document = self.store.load_document(session_id)
        summary = document.to_summary(request, str(self.store.shared_goal_path(session_id)))
        return {
            "summary": summary.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
            "shared_goal_path": str(self.store.shared_goal_path(session_id)),
            "shared_goal_markdown": self.store.shared_goal_path(session_id).read_text(encoding="utf-8"),
            "sections": {
                "goal": document.goal,
                "constraints": list(document.constraints),
                "acceptance_criteria": list(document.acceptance_criteria),
                "completed_acceptance_criteria": list(document.completed_acceptance_criteria),
                "user_notes": list(document.user_notes),
                "gemini_plan": document.gemini_plan,
                "codex_plan": document.codex_plan,
                "codex_execution": document.codex_execution,
                "codex_verification": document.codex_verification,
                "gemini_review": document.gemini_review,
                "next_action": document.next_action,
                "final_outcome": document.final_outcome,
            },
        }

    def session_create_or_resume(self, session_id: str, request: dict[str, Any], gemini_model: str) -> dict[str, Any]:
        path = self.store.shared_goal_path(session_id)
        parsed_request = SessionRequest.model_validate(request)
        if path.exists():
            document = self.store.load_document(session_id)
            self.store.save_request(session_id, parsed_request)
            self.store.save_document(document, parsed_request)
        else:
            document = self.store.create_session(session_id, parsed_request, gemini_model)
        return self.session_read(session_id)

    def session_update_user_input(self, session_id: str, text: str = "", attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        request = self.store.load_request(session_id)
        document = self.store.load_document(session_id)
        cleaned = text.strip()
        if cleaned:
            timestamp = document.updated_at or ""
            document.user_notes.append(f"{timestamp} {cleaned}".strip())
            request.user_notes = list(document.user_notes)
        if attachments:
            request.attachments.extend(JobAttachment.model_validate(item) for item in attachments)
        self.store.save_request(session_id, request)
        self.store.save_document(document, request)
        return self.session_read(session_id)

    def session_write_gemini_sections(
        self,
        session_id: str,
        goal: str = "",
        gemini_plan: str = "",
        gemini_review: str = "",
        next_action: str = "",
        final_outcome: str = "",
        acceptance_criteria: list[str] | None = None,
        completed_acceptance_criteria: list[str] | None = None,
        verdict: str = "",
        status: str = "",
        replace_goal_context: bool = False,
    ) -> dict[str, Any]:
        request = self.store.load_request(session_id)
        document = self.store.load_document(session_id)
        if goal.strip():
            document.goal = goal.strip()
            request.goal = document.goal
        if replace_goal_context:
            document.gemini_plan = ""
            document.codex_plan = ""
            document.codex_execution = ""
            document.codex_verification = ""
            document.gemini_review = ""
            document.next_action = ""
            document.final_outcome = ""
            document.acceptance_criteria = [item.strip() for item in (acceptance_criteria or []) if item.strip()]
            document.completed_acceptance_criteria = [item.strip() for item in (completed_acceptance_criteria or []) if item.strip()]
            request.acceptance_criteria = list(document.acceptance_criteria)
        if gemini_plan:
            document.gemini_plan = merge_text(document.gemini_plan, gemini_plan)
        if gemini_review:
            document.gemini_review = merge_text(document.gemini_review, gemini_review)
        if next_action:
            document.next_action = next_action.strip()
        if final_outcome:
            document.final_outcome = final_outcome.strip()
        if not replace_goal_context:
            merge_checklist(document, acceptance_criteria or [], completed_acceptance_criteria or [])
            request.acceptance_criteria = list(document.acceptance_criteria)
        if verdict:
            document.verdict = SessionVerdict(verdict)
        if status:
            document.status = SessionState(status)
        self.store.save_document(document, request)
        return self.session_read(session_id)

    def session_write_codex_sections(
        self,
        session_id: str,
        codex_plan: str = "",
        codex_execution: str = "",
        codex_verification: str = "",
        completed_acceptance_criteria: list[str] | None = None,
    ) -> dict[str, Any]:
        request = self.store.load_request(session_id)
        document = self.store.load_document(session_id)
        if codex_plan:
            document.codex_plan = merge_text(document.codex_plan, codex_plan)
        if codex_execution:
            document.codex_execution = merge_text(document.codex_execution, codex_execution)
        if codex_verification:
            document.codex_verification = merge_text(document.codex_verification, codex_verification)
        merge_checklist(document, [], completed_acceptance_criteria or [])
        self.store.save_document(document, request)
        return self.session_read(session_id)

    def session_set_verdict(
        self,
        session_id: str,
        verdict: str,
        status: str,
        active_run_id: str = "",
    ) -> dict[str, Any]:
        request = self.store.load_request(session_id)
        document = self.store.load_document(session_id)
        document.verdict = SessionVerdict(verdict)
        document.status = SessionState(status)
        if active_run_id:
            document.active_run_id = active_run_id
        self.store.save_document(document, request)
        if document.status.is_terminal:
            self.store.set_active_session(document.channel, document.conversation_id, None)
        return self.session_read(session_id)

    def session_list_recent(self, limit: int = 20) -> dict[str, Any]:
        summaries = self.store.list_recent(limit=limit)
        return {"sessions": [item.model_dump(mode="json") for item in summaries]}


class SessionMcpServer:
    def __init__(self, service: SessionMcpService) -> None:
        self.service = service
        self.token = secrets.token_hex(16)
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def start(self) -> SessionMcpConfig:
        if self._server is None:
            handler = self._build_handler()
            self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self._thread = Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        assert self._server is not None
        return SessionMcpConfig(base_url=f"http://127.0.0.1:{self._server.server_port}", token=self.token)

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def _build_handler(self):
        service = self.service
        expected_token = self.token

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/call":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if self.headers.get("Authorization", "") != f"Bearer {expected_token}":
                    self.send_error(HTTPStatus.UNAUTHORIZED)
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                    result = service.call(str(payload.get("method", "")), payload.get("params") or {})
                except Exception as exc:  # noqa: BLE001
                    self.send_response(HTTPStatus.BAD_REQUEST)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"result": result}, ensure_ascii=False).encode("utf-8"))

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

        return Handler
