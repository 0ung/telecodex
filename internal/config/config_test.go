package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadResolvesRelativeLogFileAgainstAbsoluteConfigDir(t *testing.T) {
	tempDir := t.TempDir()
	configPath := filepath.Join(tempDir, "config.yaml")
	workspaceDir := filepath.Join(tempDir, "workspace")

	if err := os.MkdirAll(workspaceDir, 0o755); err != nil {
		t.Fatalf("mkdir workspace: %v", err)
	}

	content := []byte(`
bot_token: "token"
allowed_user_id: 123
language: "korean"
model: "gpt-5-codex"
workspace_dir: "` + filepath.ToSlash(workspaceDir) + `"
log_file: "./app.log"
`)
	if err := os.WriteFile(configPath, content, 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}

	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	if err := os.Chdir(tempDir); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	defer func() { _ = os.Chdir(cwd) }()

	cfg, err := Load("config.yaml")
	if err != nil {
		t.Fatalf("load config: %v", err)
	}

	want := filepath.Join(tempDir, "app.log")
	if cfg.LogFile != want {
		t.Fatalf("expected absolute log path %q, got %q", want, cfg.LogFile)
	}
	if cfg.Language != "ko" {
		t.Fatalf("expected normalized language ko, got %q", cfg.Language)
	}
	if cfg.Model != "gpt-5-codex" {
		t.Fatalf("expected model to be preserved, got %q", cfg.Model)
	}
}
