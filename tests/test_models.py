from __future__ import annotations

from telecodex.shared.models import CodexResult, CodexStatus, GeminiResponse, GeminiStatus


def test_gemini_response_normalizes_null_optional_fields() -> None:
    parsed = GeminiResponse.model_validate(
        {
            "status": GeminiStatus.ASK_USER,
            "summary_for_user": None,
            "instruction_for_codex": None,
            "acceptance_criteria": None,
            "completed_acceptance_criteria": None,
            "revised_goal": None,
            "gemini_plan": None,
            "review_notes": None,
            "next_action": None,
            "question_for_user": None,
            "reason": None,
        }
    )

    assert parsed.summary_for_user == ""
    assert parsed.instruction_for_codex == ""
    assert parsed.acceptance_criteria == []
    assert parsed.completed_acceptance_criteria == []
    assert parsed.revised_goal == ""
    assert parsed.gemini_plan == ""
    assert parsed.review_notes == ""
    assert parsed.next_action == ""
    assert parsed.question_for_user == ""
    assert parsed.reason == ""


def test_codex_result_normalizes_null_optional_fields() -> None:
    parsed = CodexResult.model_validate(
        {
            "status": CodexStatus.COMPLETED,
            "changed_files": None,
            "commands_run": None,
            "command_results": None,
            "summary": None,
            "next_step": None,
            "raw_output": None,
            "codex_plan": None,
            "verification_notes": None,
            "verified_acceptance_criteria": None,
            "proposed_completion": True,
        }
    )

    assert parsed.changed_files == []
    assert parsed.commands_run == []
    assert parsed.command_results == []
    assert parsed.summary == ""
    assert parsed.next_step == ""
    assert parsed.raw_output == ""
    assert parsed.codex_plan == ""
    assert parsed.verification_notes == ""
    assert parsed.verified_acceptance_criteria == []
    assert parsed.proposed_completion is True
