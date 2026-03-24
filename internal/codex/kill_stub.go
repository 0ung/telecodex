//go:build !windows

package codex

import "syscall"

func killProcessTree(pid int) error {
	return syscall.Kill(pid, syscall.SIGKILL)
}
