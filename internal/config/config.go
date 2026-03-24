package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

type Config struct {
	BotToken          string `yaml:"bot_token"`
	AllowedUserID     int64  `yaml:"allowed_user_id"`
	Language          string `yaml:"language"`
	Model             string `yaml:"model"`
	WorkspaceDir      string `yaml:"workspace_dir"`
	PollTimeoutSec    int    `yaml:"poll_timeout_sec"`
	ProgressUpdateSec int    `yaml:"progress_update_sec"`
	LogFile           string `yaml:"log_file"`
}

func Load(path string) (Config, error) {
	absPath, err := filepath.Abs(path)
	if err != nil {
		return Config{}, fmt.Errorf("resolve config path: %w", err)
	}

	raw, err := os.ReadFile(absPath)
	if err != nil {
		return Config{}, fmt.Errorf("read config: %w", err)
	}

	cfg := Config{
		Language:          "en",
		PollTimeoutSec:    30,
		ProgressUpdateSec: 2,
		LogFile:           "./app.log",
	}
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		return Config{}, fmt.Errorf("parse config yaml: %w", err)
	}

	baseDir := filepath.Dir(absPath)
	if cfg.LogFile == "" {
		cfg.LogFile = "./app.log"
	}
	if !filepath.IsAbs(cfg.LogFile) {
		cfg.LogFile = filepath.Clean(filepath.Join(baseDir, cfg.LogFile))
	}
	cfg.Language = normalizeLanguage(cfg.Language)
	cfg.Model = strings.TrimSpace(cfg.Model)
	cfg.WorkspaceDir = filepath.Clean(cfg.WorkspaceDir)

	if err := cfg.Validate(); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

func (c Config) Validate() error {
	switch {
	case c.BotToken == "":
		return errors.New("config: bot_token is required")
	case c.AllowedUserID == 0:
		return errors.New("config: allowed_user_id is required")
	case c.WorkspaceDir == "":
		return errors.New("config: workspace_dir is required")
	case !filepath.IsAbs(c.WorkspaceDir):
		return errors.New("config: workspace_dir must be an absolute path")
	}

	info, err := os.Stat(c.WorkspaceDir)
	if err != nil {
		return fmt.Errorf("config: workspace_dir check failed: %w", err)
	}
	if !info.IsDir() {
		return errors.New("config: workspace_dir must be a directory")
	}

	if c.PollTimeoutSec <= 0 {
		return errors.New("config: poll_timeout_sec must be > 0")
	}
	if c.ProgressUpdateSec <= 0 {
		return errors.New("config: progress_update_sec must be > 0")
	}
	if c.Language != "en" && c.Language != "ko" {
		return errors.New("config: language must be 'en' or 'ko'")
	}
	return nil
}

func normalizeLanguage(v string) string {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "", "en", "english":
		return "en"
	case "ko", "kr", "korean", "hangul":
		return "ko"
	default:
		return strings.ToLower(strings.TrimSpace(v))
	}
}
