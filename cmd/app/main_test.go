package main

import (
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestTruncateSingleLine(t *testing.T) {
	got := truncateSingleLine("hello\nworld", 20)
	if got != "hello world" {
		t.Fatalf("unexpected single line result: %q", got)
	}

	long := strings.Repeat("a", 20)
	got = truncateSingleLine(long, 10)
	if got != "aaaaaaa..." {
		t.Fatalf("unexpected truncated result: %q", got)
	}
}

func TestStatusText(t *testing.T) {
	got := statusText("en", "Ready", "C:/work", "./app.log", "gpt-5-codex", "Waiting for Telegram messages")
	if !strings.Contains(got, "Workspace: C:/work") {
		t.Fatalf("missing workspace line: %q", got)
	}
	if !strings.Contains(got, "Log file: ./app.log") {
		t.Fatalf("missing log line: %q", got)
	}
	if !strings.Contains(got, "Model: gpt-5-codex") {
		t.Fatalf("missing model line: %q", got)
	}
	if !strings.Contains(got, "Waiting for Telegram messages") {
		t.Fatalf("missing detail line: %q", got)
	}
}

func TestAcquireSingleInstanceLock(t *testing.T) {
	lockPath := filepath.Join(t.TempDir(), "telecodex.lock")

	unlock, err := acquireSingleInstanceLock(lockPath)
	if err != nil {
		t.Fatalf("first lock acquisition failed: %v", err)
	}

	if _, err := acquireSingleInstanceLock(lockPath); err == nil {
		t.Fatalf("expected second lock acquisition to fail")
	}

	if err := unlock(); err != nil {
		t.Fatalf("unlock failed: %v", err)
	}

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if unlockAgain, err := acquireSingleInstanceLock(lockPath); err == nil {
			if err := unlockAgain(); err != nil {
				t.Fatalf("second unlock failed: %v", err)
			}
			return
		}
		time.Sleep(25 * time.Millisecond)
	}

	t.Fatalf("lock file was not released in time")
}
