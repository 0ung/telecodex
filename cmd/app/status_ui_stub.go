//go:build !windows

package main

import "fmt"

func startStatusUI(title, workspacePath, logPath, initialText string) (statusUI, error) {
	return nil, nil
}

func showFatalError(title, message string) {
	fmt.Printf("%s\n\n%s\n", title, message)
}
