package codex

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
)

type Event struct {
	Method         string
	Params         map[string]any
	ServerRequest  bool
	Summary        string
	BlockingReason string
}

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type responseEnvelope struct {
	Result json.RawMessage
	Err    *RPCError
}

type commandSpec struct {
	name string
	args []string
}

type Client struct {
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	logger *log.Logger

	events chan Event
	done   chan error

	closeOnce sync.Once

	pendingMu sync.Mutex
	pending   map[int64]chan responseEnvelope
	nextID    int64

	writeMu sync.Mutex
}

func Start(ctx context.Context, logger *log.Logger, configuredPath, model string) (*Client, error) {
	spec, err := resolveCodexCommand(configuredPath)
	if err != nil {
		return nil, err
	}

	args := append([]string{}, spec.args...)
	args = append(args, "app-server", "--listen", "stdio://", "-c", "analytics.enabled=false")
	if model = strings.TrimSpace(model); model != "" {
		args = append(args, "-c", "model="+strconv.Quote(model))
	}
	cmd := exec.CommandContext(ctx, spec.name, args...)
	applyPlatformCommandOptions(cmd)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("codex stdin pipe: %w", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("codex stdout pipe: %w", err)
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, fmt.Errorf("codex stderr pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start codex app-server: %w", err)
	}

	c := &Client{
		cmd:     cmd,
		stdin:   stdin,
		logger:  logger,
		events:  make(chan Event, 128),
		done:    make(chan error, 1),
		pending: make(map[int64]chan responseEnvelope),
	}

	go c.readStdout(stdout)
	go c.readStderr(stderr)
	go c.wait()
	return c, nil
}

func (c *Client) Events() <-chan Event { return c.events }
func (c *Client) Done() <-chan error   { return c.done }

func (c *Client) Close() error {
	var closeErr error
	c.closeOnce.Do(func() {
		c.writeMu.Lock()
		if c.stdin != nil {
			_ = c.stdin.Close()
		}
		c.writeMu.Unlock()

		if c.cmd != nil && c.cmd.Process != nil {
			if err := killProcessTree(c.cmd.Process.Pid); err != nil {
				c.logger.Printf("kill codex process failed: %v", err)
				closeErr = err
			}
		}
	})
	return closeErr
}

func (c *Client) Initialize(ctx context.Context) error {
	params := map[string]any{
		"clientInfo": map[string]any{
			"name":    "telecodex",
			"title":   "Telecodex",
			"version": "0.1.0",
		},
		"capabilities": map[string]any{
			"experimentalApi": false,
		},
	}
	if _, err := c.request(ctx, "initialize", params); err != nil {
		return err
	}
	return c.notify("initialized", nil)
}

func (c *Client) StartThread(ctx context.Context, workspaceDir string) (string, error) {
	result, err := c.request(ctx, "thread/start", map[string]any{
		"cwd":               workspaceDir,
		"approvalPolicy":    "never",
		"approvalsReviewer": "user",
		"sandbox":           "workspace-write",
		"personality":       "pragmatic",
		"serviceName":       "telecodex",
		"ephemeral":         false,
	})
	if err != nil {
		return "", err
	}
	threadID, _ := getString(result, "thread", "id")
	if threadID == "" {
		return "", errors.New("thread/start response missing thread.id")
	}
	return threadID, nil
}

func (c *Client) StartTurn(ctx context.Context, threadID, workspaceDir, prompt string) (string, error) {
	result, err := c.request(ctx, "turn/start", map[string]any{
		"threadId":       threadID,
		"cwd":            workspaceDir,
		"approvalPolicy": "never",
		"input": []map[string]any{
			{
				"type":          "text",
				"text":          prompt,
				"text_elements": []any{},
			},
		},
	})
	if err != nil {
		return "", err
	}
	turnID, _ := getString(result, "turn", "id")
	if turnID == "" {
		return "", errors.New("turn/start response missing turn.id")
	}
	return turnID, nil
}

func (c *Client) InterruptTurn(ctx context.Context, threadID, turnID string) error {
	_, err := c.request(ctx, "turn/interrupt", map[string]any{
		"threadId": threadID,
		"turnId":   turnID,
	})
	return err
}

func (c *Client) request(ctx context.Context, method string, params any) (map[string]any, error) {
	id := atomic.AddInt64(&c.nextID, 1)
	ch := make(chan responseEnvelope, 1)

	c.pendingMu.Lock()
	c.pending[id] = ch
	c.pendingMu.Unlock()

	if err := c.writeJSON(map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  method,
		"params":  params,
	}); err != nil {
		c.pendingMu.Lock()
		delete(c.pending, id)
		c.pendingMu.Unlock()
		return nil, err
	}

	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case res := <-ch:
		if res.Err != nil {
			return nil, fmt.Errorf("%s failed (%d): %s", method, res.Err.Code, res.Err.Message)
		}
		if len(res.Result) == 0 || string(res.Result) == "null" {
			return map[string]any{}, nil
		}

		var decoded map[string]any
		if err := json.Unmarshal(res.Result, &decoded); err != nil {
			return nil, fmt.Errorf("%s result decode: %w", method, err)
		}
		return decoded, nil
	}
}

func (c *Client) notify(method string, params any) error {
	msg := map[string]any{
		"jsonrpc": "2.0",
		"method":  method,
	}
	if params != nil {
		msg["params"] = params
	}
	return c.writeJSON(msg)
}

func (c *Client) writeJSON(v any) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()

	payload, err := json.Marshal(v)
	if err != nil {
		return fmt.Errorf("marshal json-rpc: %w", err)
	}
	payload = append(payload, '\n')
	if _, err := c.stdin.Write(payload); err != nil {
		return fmt.Errorf("write json-rpc: %w", err)
	}
	return nil
}

func (c *Client) readStdout(r io.Reader) {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := scanner.Bytes()
		if len(strings.TrimSpace(string(line))) == 0 {
			continue
		}
		c.handleIncoming(append([]byte(nil), line...))
	}

	if err := scanner.Err(); err != nil {
		c.logger.Printf("codex stdout scanner error: %v", err)
	}
}

func (c *Client) readStderr(r io.Reader) {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 0, 16*1024), 512*1024)
	for scanner.Scan() {
		c.logger.Printf("codex stderr: %s", scanner.Text())
	}
	if err := scanner.Err(); err != nil {
		c.logger.Printf("codex stderr scanner error: %v", err)
	}
}

func (c *Client) wait() {
	err := c.cmd.Wait()
	c.done <- err
	close(c.done)
	close(c.events)
}

func (c *Client) handleIncoming(line []byte) {
	var envelope struct {
		ID     *json.RawMessage `json:"id"`
		Method string           `json:"method"`
		Params json.RawMessage  `json:"params"`
		Result json.RawMessage  `json:"result"`
		Error  *RPCError        `json:"error"`
	}
	if err := json.Unmarshal(line, &envelope); err != nil {
		c.logger.Printf("decode app-server line failed: %v | %s", err, string(line))
		return
	}

	if envelope.Method != "" && envelope.ID != nil {
		params := decodeParams(envelope.Params)
		_ = c.writeJSON(map[string]any{
			"jsonrpc": "2.0",
			"id":      json.RawMessage(*envelope.ID),
			"error": map[string]any{
				"code":    -32000,
				"message": "interactive approvals and user input are not supported by telecodex",
			},
		})
		c.emit(Event{
			Method:         envelope.Method,
			Params:         params,
			ServerRequest:  true,
			Summary:        "interactive approval/input requested: " + envelope.Method,
			BlockingReason: "approval_or_input_not_supported",
		})
		return
	}

	if envelope.Method != "" {
		c.emit(Event{
			Method:  envelope.Method,
			Params:  decodeParams(envelope.Params),
			Summary: envelope.Method,
		})
		return
	}

	if envelope.ID != nil {
		var id int64
		if err := json.Unmarshal(*envelope.ID, &id); err != nil {
			c.logger.Printf("decode response id failed: %v", err)
			return
		}

		c.pendingMu.Lock()
		ch := c.pending[id]
		delete(c.pending, id)
		c.pendingMu.Unlock()
		if ch != nil {
			ch <- responseEnvelope{Result: envelope.Result, Err: envelope.Error}
		}
	}
}

func (c *Client) emit(event Event) {
	select {
	case c.events <- event:
	default:
		c.logger.Printf("dropping codex event due to full channel: %s", event.Method)
	}
}

func decodeParams(raw json.RawMessage) map[string]any {
	if len(raw) == 0 || string(raw) == "null" {
		return map[string]any{}
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return map[string]any{"_raw": string(raw)}
	}
	return m
}

func getString(root map[string]any, path ...string) (string, bool) {
	var current any = root
	for _, key := range path {
		asMap, ok := current.(map[string]any)
		if !ok {
			return "", false
		}
		current, ok = asMap[key]
		if !ok {
			return "", false
		}
	}
	s, ok := current.(string)
	return s, ok
}

func resolveCodexCommand(configuredPath string) (commandSpec, error) {
	if configuredPath != "" {
		if _, err := os.Stat(configuredPath); err == nil {
			return buildCommandSpec(configuredPath), nil
		}
		return commandSpec{}, fmt.Errorf("configured codex_path not found: %s", configuredPath)
	}

	if runtime.GOOS != "windows" {
		return commandSpec{name: "codex"}, nil
	}

	if cmdPath, err := exec.LookPath("codex.cmd"); err == nil {
		return buildCommandSpec(cmdPath), nil
	}

	if localAppData := os.Getenv("LOCALAPPDATA"); localAppData != "" {
		candidate := filepath.Join(localAppData, "Microsoft", "WindowsApps", "codex.cmd")
		if _, statErr := os.Stat(candidate); statErr == nil {
			return buildCommandSpec(candidate), nil
		}
	}

	if appData := os.Getenv("APPDATA"); appData != "" {
		candidate := filepath.Join(appData, "npm", "codex.cmd")
		if _, statErr := os.Stat(candidate); statErr == nil {
			return buildCommandSpec(candidate), nil
		}
	}

	execPath, err := exec.LookPath("codex.exe")
	if err == nil {
		return buildCommandSpec(execPath), nil
	}

	if localAppData := os.Getenv("LOCALAPPDATA"); localAppData != "" {
		candidate := filepath.Join(localAppData, "Microsoft", "WindowsApps", "codex.exe")
		if _, statErr := os.Stat(candidate); statErr == nil {
			return buildCommandSpec(candidate), nil
		}
	}

	if execPath, err = exec.LookPath("codex"); err == nil && strings.EqualFold(filepath.Ext(execPath), ".exe") {
		return buildCommandSpec(execPath), nil
	}

	return commandSpec{}, errors.New("could not locate a runnable codex command")
}

func buildCommandSpec(path string) commandSpec {
	if runtime.GOOS == "windows" {
		ext := strings.ToLower(filepath.Ext(path))
		if ext == ".cmd" || ext == ".bat" {
			return commandSpec{
				name: "cmd.exe",
				args: []string{"/d", "/c", path},
			}
		}
	}
	return commandSpec{name: path}
}
