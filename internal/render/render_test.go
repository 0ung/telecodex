package render

import (
	"strings"
	"testing"
	"time"

	"telegram-codex/internal/session"
)

func TestCurrentSummaryPriority(t *testing.T) {
	r := New("en", "")
	s := session.Snapshot{
		Prompt:        "prompt text",
		ReasoningText: "reasoning text",
		PlanText:      "plan text",
		AssistantText: "assistant text",
	}

	if got := currentSummary(s); got != "assistant text" {
		t.Fatalf("expected assistant text priority, got %q", got)
	}
	if got := r.HelpMessage(); !strings.Contains(got, "/status") {
		t.Fatalf("expected help message to mention /status")
	}
}

func TestCompletionMessageIncludesCapturedResult(t *testing.T) {
	r := New("en", "gpt-5-codex")
	started := time.Date(2026, 3, 24, 0, 0, 0, 0, time.UTC)
	ended := started.Add(8 * time.Second)
	s := session.Snapshot{
		State:           session.StateIdle,
		StartedAt:       started,
		EndedAt:         ended,
		CompletionState: "completed",
		CompletionText:  "final answer",
	}

	got := r.CompletionMessage(s)
	for _, want := range []string{"Task complete", "Completed | 8s", "Model: gpt-5-codex", "final answer"} {
		if !strings.Contains(got, want) {
			t.Fatalf("expected %q in completion message: %q", want, got)
		}
	}
	if strings.Contains(got, "thread-1") || strings.Contains(got, "turn-1") {
		t.Fatalf("completion message should not expose thread/turn ids by default: %q", got)
	}
}

func TestExcerptFallback(t *testing.T) {
	if got := excerpt("   ", 10); got != "-" {
		t.Fatalf("expected dash fallback, got %q", got)
	}
}

func TestKoreanStatusMessage(t *testing.T) {
	r := New("ko", "")
	got := r.StatusMessage(session.Snapshot{
		State:     session.StateIdle,
		LastEvent: "turn completed",
	})
	if !strings.Contains(got, "\uD604\uC7AC \uC0C1\uD0DC") {
		t.Fatalf("expected korean heading, got %q", got)
	}
	if !strings.Contains(got, "\uCD5C\uADFC \uC774\uBCA4\uD2B8: Turn \uC644\uB8CC") {
		t.Fatalf("expected localized event label, got %q", got)
	}
}
