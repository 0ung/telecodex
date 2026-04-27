from __future__ import annotations

import httpx

from telecodex.shared.models import SessionRequest, SessionState, SessionVerdict
from telecodex.worker.session_docs import SessionDocumentStore
from telecodex.worker.session_mcp import SessionMcpServer, SessionMcpService


def test_session_mcp_server_reads_and_updates_shared_goal(tmp_path) -> None:
    store = SessionDocumentStore(str(tmp_path))
    request = SessionRequest(
        goal="Ship the feature",
        requester_id=1,
        workspace_path=str(tmp_path),
        channel="telegram",
        conversation_id="chat-1",
    )
    store.create_session("session-1", request, "gemini-2.5-flash")
    service = SessionMcpService(store)
    server = SessionMcpServer(service)
    config = server.start()

    try:
        response = httpx.post(
            f"{config.base_url}/call",
            headers={"Authorization": f"Bearer {config.token}"},
            json={
                "method": "session_write_gemini_sections",
                "params": {
                    "session_id": "session-1",
                    "gemini_plan": "Plan the feature.",
                    "acceptance_criteria": ["Implement the feature", "Verify the change"],
                    "verdict": SessionVerdict.CONTINUE.value,
                    "status": SessionState.PLANNING.value,
                },
            },
            timeout=10,
        )
        response.raise_for_status()

        response = httpx.post(
            f"{config.base_url}/call",
            headers={"Authorization": f"Bearer {config.token}"},
            json={
                "method": "session_write_codex_sections",
                "params": {
                    "session_id": "session-1",
                    "codex_execution": "Implemented the feature and ran pytest.",
                    "codex_verification": "pytest: exit=0",
                    "completed_acceptance_criteria": ["Implement the feature"],
                },
            },
            timeout=10,
        )
        response.raise_for_status()

        loaded = service.session_read("session-1")
        markdown = loaded["shared_goal_markdown"]
        assert "Plan the feature." in markdown
        assert "- [x] Implement the feature" in markdown
        assert "- [ ] Verify the change" in markdown
        assert "Implemented the feature and ran pytest." in markdown
    finally:
        server.stop()


def test_session_mcp_clears_stale_question_when_user_answers(tmp_path) -> None:
    store = SessionDocumentStore(str(tmp_path))
    request = SessionRequest(
        goal="새 개발 폴더를 만든다",
        requester_id=1,
        workspace_path=str(tmp_path),
        channel="telegram",
        conversation_id="chat-1",
    )
    store.create_session("session-1", request, "gemini-2.5-flash")
    service = SessionMcpService(store)

    service.session_write_gemini_sections(
        "session-1",
        next_action="새로운 개발 폴더의 이름을 무엇으로 하시겠습니까?",
        verdict=SessionVerdict.ASK_USER.value,
        status=SessionState.WAITING_USER.value,
    )
    service.session_update_user_input("session-1", text="BusanTour")

    loaded = service.session_read("session-1")
    assert loaded["sections"]["next_action"] == ""
    assert any("BusanTour" in item for item in loaded["sections"]["user_notes"])
