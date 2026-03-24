package session

import (
	"testing"

	"telegram-codex/internal/codex"
)

func TestSummarizeEvent(t *testing.T) {
	if got := summarizeEvent(codex.Event{Method: "item/plan/delta"}); got != "plan updated" {
		t.Fatalf("unexpected summary: %q", got)
	}

	if got := summarizeEvent(codex.Event{Method: "custom/event", Summary: "custom summary"}); got != "custom summary" {
		t.Fatalf("expected custom summary override, got %q", got)
	}
}

func TestJoinAny(t *testing.T) {
	got := joinAny([]any{"first", "", "second", 123})
	if got != "first\nsecond" {
		t.Fatalf("unexpected joined summary: %q", got)
	}
}

func TestExtractTurnError(t *testing.T) {
	fromString := extractTurnError(map[string]any{
		"turn": map[string]any{
			"error": "plain error",
		},
	})
	if fromString != "plain error" {
		t.Fatalf("unexpected string error: %q", fromString)
	}

	fromObject := extractTurnError(map[string]any{
		"turn": map[string]any{
			"error": map[string]any{"message": "structured error"},
		},
	})
	if fromObject != "structured error" {
		t.Fatalf("unexpected object error: %q", fromObject)
	}
}
