from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from threading import Lock

import yaml
from pydantic import BaseModel, Field

from telecodex.shared.models import SessionRequest, SessionState, SessionSummary, SessionVerdict, merge_unique_items, utc_now


SECTION_ORDER = [
    "Goal",
    "Constraints",
    "Acceptance Criteria",
    "User Notes",
    "Gemini Plan",
    "Codex Plan",
    "Codex Execution",
    "Codex Verification",
    "Gemini Review",
    "Next Action",
    "Final Outcome",
]


def conversation_key(channel: str, conversation_id: str) -> str:
    return f"{channel}:{conversation_id}"


class SessionMarkdownDocument(BaseModel):
    session_id: str
    channel: str
    conversation_id: str
    status: SessionState
    verdict: SessionVerdict | None = None
    gemini_model: str
    active_run_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    goal: str = ""
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    completed_acceptance_criteria: list[str] = Field(default_factory=list)
    user_notes: list[str] = Field(default_factory=list)
    gemini_plan: str = ""
    codex_plan: str = ""
    codex_execution: str = ""
    codex_verification: str = ""
    gemini_review: str = ""
    next_action: str = ""
    final_outcome: str = ""

    def render_markdown(self) -> str:
        frontmatter = {
            "session_id": self.session_id,
            "channel": self.channel,
            "conversation_id": self.conversation_id,
            "status": self.status.value,
            "verdict": self.verdict.value if self.verdict else "",
            "gemini_model": self.gemini_model,
            "active_run_id": self.active_run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        body_sections = {
            "Goal": self.goal.strip(),
            "Constraints": _render_bullets(self.constraints),
            "Acceptance Criteria": _render_checklist(self.acceptance_criteria, self.completed_acceptance_criteria),
            "User Notes": _render_bullets(self.user_notes),
            "Gemini Plan": self.gemini_plan.strip(),
            "Codex Plan": self.codex_plan.strip(),
            "Codex Execution": self.codex_execution.strip(),
            "Codex Verification": self.codex_verification.strip(),
            "Gemini Review": self.gemini_review.strip(),
            "Next Action": self.next_action.strip(),
            "Final Outcome": self.final_outcome.strip(),
        }
        lines = ["---", yaml.safe_dump(frontmatter, sort_keys=False).strip(), "---", ""]
        for heading in SECTION_ORDER:
            lines.append(f"# {heading}")
            content = body_sections[heading]
            lines.append(content or "_None_")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def to_summary(self, request: SessionRequest, shared_goal_path: str) -> SessionSummary:
        return SessionSummary(
            session_id=self.session_id,
            channel=self.channel,
            conversation_id=self.conversation_id,
            goal=request.goal or self.goal,
            state=self.status,
            verdict=self.verdict,
            created_at=_parse_datetime(self.created_at),
            updated_at=_parse_datetime(self.updated_at),
            active_run_id=self.active_run_id,
            final_summary=self.final_outcome.strip(),
            shared_goal_path=shared_goal_path,
        )

    @classmethod
    def parse_markdown(cls, text: str) -> "SessionMarkdownDocument":
        frontmatter, body = _split_frontmatter(text)
        sections = _split_sections(body)
        criteria, completed = _parse_checklist(sections.get("Acceptance Criteria", ""))
        return cls(
            session_id=str(frontmatter.get("session_id", "")),
            channel=str(frontmatter.get("channel", "")),
            conversation_id=str(frontmatter.get("conversation_id", "")),
            status=SessionState(str(frontmatter.get("status", SessionState.PLANNING.value))),
            verdict=_parse_verdict(frontmatter.get("verdict")),
            gemini_model=str(frontmatter.get("gemini_model", "")),
            active_run_id=str(frontmatter.get("active_run_id", "")),
            created_at=str(frontmatter.get("created_at", "")),
            updated_at=str(frontmatter.get("updated_at", "")),
            goal=_clean_section(sections.get("Goal", "")),
            constraints=_parse_bullets(sections.get("Constraints", "")),
            acceptance_criteria=criteria,
            completed_acceptance_criteria=completed,
            user_notes=_parse_bullets(sections.get("User Notes", "")),
            gemini_plan=_clean_section(sections.get("Gemini Plan", "")),
            codex_plan=_clean_section(sections.get("Codex Plan", "")),
            codex_execution=_clean_section(sections.get("Codex Execution", "")),
            codex_verification=_clean_section(sections.get("Codex Verification", "")),
            gemini_review=_clean_section(sections.get("Gemini Review", "")),
            next_action=_clean_section(sections.get("Next Action", "")),
            final_outcome=_clean_section(sections.get("Final Outcome", "")),
        )


class SessionDocumentStore:
    def __init__(self, runs_root: str) -> None:
        self.runs_root = Path(runs_root)
        self.sessions_root = self.runs_root / "_sessions"
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self._active_map_path = self.sessions_root / "active_conversations.json"
        self._lock = Lock()

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_root / session_id

    def shared_goal_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "shared_goal.md"

    def index_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def request_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session_request.json"

    def attachments_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "attachments"

    def create_session(self, session_id: str, request: SessionRequest, gemini_model: str) -> SessionMarkdownDocument:
        now = utc_now().isoformat()
        document = SessionMarkdownDocument(
            session_id=session_id,
            channel=request.channel,
            conversation_id=request.conversation_id,
            status=SessionState.PLANNING,
            verdict=SessionVerdict.CONTINUE,
            gemini_model=gemini_model,
            created_at=now,
            updated_at=now,
            goal=request.goal,
            constraints=list(request.constraints),
            acceptance_criteria=list(request.acceptance_criteria),
            user_notes=list(request.user_notes),
        )
        self.save_request(session_id, request)
        self.save_document(document, request)
        self.set_active_session(request.channel, request.conversation_id, session_id)
        return document

    def save_document(self, document: SessionMarkdownDocument, request: SessionRequest | None = None) -> None:
        session_dir = self.session_dir(document.session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        document.updated_at = utc_now().isoformat()
        self.shared_goal_path(document.session_id).write_text(document.render_markdown(), encoding="utf-8")
        if request is None:
            request = self.load_request(document.session_id)
        summary = document.to_summary(request, str(self.shared_goal_path(document.session_id)))
        self.index_path(document.session_id).write_text(
            json.dumps(
                {
                    "session_id": summary.session_id,
                    "channel": summary.channel,
                    "conversation_id": summary.conversation_id,
                    "goal": summary.goal,
                    "state": summary.state.value,
                    "verdict": summary.verdict.value if summary.verdict else "",
                    "created_at": summary.created_at.isoformat(),
                    "updated_at": summary.updated_at.isoformat(),
                    "active_run_id": summary.active_run_id,
                    "shared_goal_path": summary.shared_goal_path,
                    "final_summary": summary.final_summary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load_document(self, session_id: str) -> SessionMarkdownDocument:
        return SessionMarkdownDocument.parse_markdown(self.shared_goal_path(session_id).read_text(encoding="utf-8"))

    def save_request(self, session_id: str, request: SessionRequest) -> None:
        session_dir = self.session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        self.request_path(session_id).write_text(request.model_dump_json(indent=2), encoding="utf-8")
        attachments_dir = self.attachments_dir(session_id)
        attachments_dir.mkdir(parents=True, exist_ok=True)
        for index, attachment in enumerate(request.attachments, start=1):
            if not attachment.content_base64:
                continue
            file_path = attachments_dir / f"{index:02d}-{attachment.safe_file_name}"
            if file_path.exists():
                continue
            file_path.write_bytes(__import__("base64").b64decode(attachment.content_base64))

    def load_request(self, session_id: str) -> SessionRequest:
        return SessionRequest.model_validate_json(self.request_path(session_id).read_text(encoding="utf-8"))

    def set_active_session(self, channel: str, conversation_id: str, session_id: str | None) -> None:
        with self._lock:
            payload = self._load_active_map()
            key = conversation_key(channel, conversation_id)
            if session_id:
                payload[key] = session_id
            else:
                payload.pop(key, None)
            self._active_map_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_active_session(self, channel: str, conversation_id: str) -> str:
        payload = self._load_active_map()
        return str(payload.get(conversation_key(channel, conversation_id), ""))

    def list_recent(self, limit: int = 20) -> list[SessionSummary]:
        summaries: list[SessionSummary] = []
        for index_path in self.sessions_root.glob("*/session.json"):
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            summaries.append(
                SessionSummary(
                    session_id=str(payload.get("session_id", "")),
                    channel=str(payload.get("channel", "")),
                    conversation_id=str(payload.get("conversation_id", "")),
                    goal=str(payload.get("goal", "")),
                    state=SessionState(str(payload.get("state", SessionState.PLANNING.value))),
                    verdict=_parse_verdict(payload.get("verdict")),
                    created_at=_parse_datetime(str(payload.get("created_at", ""))),
                    updated_at=_parse_datetime(str(payload.get("updated_at", ""))),
                    active_run_id=str(payload.get("active_run_id", "")),
                    final_summary=str(payload.get("final_summary", "")),
                    shared_goal_path=str(payload.get("shared_goal_path", "")),
                )
            )
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return summaries[:limit]

    def mark_superseded(self, session_id: str, request: SessionRequest, note: str) -> None:
        document = self.load_document(session_id)
        document.status = SessionState.CANCELED
        document.verdict = SessionVerdict.FAIL
        document.final_outcome = merge_text(document.final_outcome, note)
        self.save_document(document, request)
        self.set_active_session(document.channel, document.conversation_id, None)

    def _load_active_map(self) -> dict[str, str]:
        if not self._active_map_path.exists():
            return {}
        payload = json.loads(self._active_map_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return {str(key): str(value) for key, value in payload.items() if value}


def merge_text(existing: str, new_text: str) -> str:
    existing = existing.strip()
    new_text = new_text.strip()
    if not existing:
        return new_text
    if not new_text:
        return existing
    return f"{existing}\n\n{new_text}"


def merge_checklist(existing: SessionMarkdownDocument, acceptance_criteria: list[str], completed: list[str]) -> None:
    existing.acceptance_criteria = merge_unique_items(existing.acceptance_criteria, acceptance_criteria)
    existing.completed_acceptance_criteria = merge_unique_items(existing.completed_acceptance_criteria, completed)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    stripped = text.strip()
    if not stripped.startswith("---"):
        return {}, text
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, text
    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2].lstrip("\n")
    return frontmatter, body


def _split_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in body.splitlines():
        if raw_line.startswith("# "):
            current = raw_line[2:].strip()
            sections[current] = []
            continue
        if current:
            sections[current].append(raw_line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _clean_section(value: str) -> str:
    cleaned = value.strip()
    if cleaned == "_None_":
        return ""
    return cleaned


def _render_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else ""


def _parse_bullets(block: str) -> list[str]:
    items: list[str] = []
    for line in block.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned == "_None_":
            continue
        if cleaned.startswith("- "):
            cleaned = cleaned[2:].strip()
        items.append(cleaned)
    return items


def _render_checklist(items: list[str], completed_items: list[str]) -> str:
    completed = {item.casefold() for item in completed_items}
    lines = []
    for item in items:
        marker = "x" if item.casefold() in completed else " "
        lines.append(f"- [{marker}] {item}")
    return "\n".join(lines)


def _parse_checklist(block: str) -> tuple[list[str], list[str]]:
    items: list[str] = []
    completed: list[str] = []
    for line in block.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned == "_None_":
            continue
        if cleaned.startswith("- [") and len(cleaned) > 6:
            marker = cleaned[3].lower()
            value = cleaned[6:].strip()
            items.append(value)
            if marker == "x":
                completed.append(value)
            continue
        if cleaned.startswith("- "):
            items.append(cleaned[2:].strip())
    return items, completed


def _parse_datetime(value: str):
    if not value:
        return utc_now()
    return datetime.fromisoformat(value)


def _parse_verdict(value) -> SessionVerdict | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return SessionVerdict(cleaned)
