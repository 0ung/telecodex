//go:build !windows

package codex

import "os/exec"

func applyPlatformCommandOptions(cmd *exec.Cmd) {}
