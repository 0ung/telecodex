package telegram

import (
	"strings"
	"testing"
)

func TestNormalizeText(t *testing.T) {
	if got := normalizeText(" \r\n hello\r\nworld \n"); got != "hello\nworld" {
		t.Fatalf("unexpected normalized text: %q", got)
	}

	if got := normalizeText("   "); got != "." {
		t.Fatalf("empty message should become dot, got %q", got)
	}
}

func TestNormalizeTextTruncatesToTelegramLimit(t *testing.T) {
	input := strings.Repeat("가", 5000)
	got := normalizeText(input)

	if len([]rune(got)) != 4096 {
		t.Fatalf("expected 4096 runes, got %d", len([]rune(got)))
	}
	if !strings.HasSuffix(got, "...") {
		t.Fatalf("expected truncated text to end with ellipsis")
	}
}
