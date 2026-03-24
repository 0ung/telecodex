//go:build windows

package codex

import (
	"fmt"
	"os/exec"
)

func killProcessTree(pid int) error {
	cmd := exec.Command("taskkill", "/PID", fmt.Sprintf("%d", pid), "/T", "/F")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("taskkill pid %d: %w", pid, err)
	}
	return nil
}
