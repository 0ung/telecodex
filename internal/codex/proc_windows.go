//go:build windows

package codex

import (
	"os/exec"
	"syscall"
)

const createNoWindow = 0x08000000

func applyPlatformCommandOptions(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: createNoWindow,
	}
}
