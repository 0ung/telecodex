package session

import (
	"context"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"telegram-codex/internal/codex"
)

type State string

const (
	StateIdle       State = "idle"
	StateRunning    State = "running"
	StateCancelling State = "cancelling"
)

type Snapshot struct {
	State           State
	StartedAt       time.Time
	EndedAt         time.Time
	LastEvent       string
	LastError       string
	ThreadID        string
	TurnID          string
	Prompt          string
	AssistantText   string
	PlanText        string
	ReasoningText   string
	CompletionText  string
	CompletionState string
	UpdatedAt       time.Time
}

type Update struct {
	Snapshot Snapshot
}

type Manager struct {
	workspaceDir string
	codexPath    string
	model        string
	logger       *log.Logger

	mu      sync.RWMutex
	current Snapshot
	client  *codex.Client

	updates chan Update
}

func NewManager(workspaceDir, codexPath, model string, logger *log.Logger) *Manager {
	return &Manager{
		workspaceDir: workspaceDir,
		codexPath:    codexPath,
		model:        model,
		logger:       logger,
		current: Snapshot{
			State:     StateIdle,
			UpdatedAt: time.Now(),
			LastEvent: "idle",
		},
		updates: make(chan Update, 128),
	}
}

func (m *Manager) Updates() <-chan Update { return m.updates }

func (m *Manager) Snapshot() Snapshot {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.current
}

func (m *Manager) StartTask(ctx context.Context, prompt string) error {
	m.mu.Lock()
	if m.current.State != StateIdle {
		defer m.mu.Unlock()
		return fmt.Errorf("task already active")
	}

	m.current = Snapshot{
		State:     StateRunning,
		StartedAt: time.Now(),
		UpdatedAt: time.Now(),
		LastEvent: "codex starting",
		Prompt:    strings.TrimSpace(prompt),
	}
	m.mu.Unlock()
	m.publish()

	client, err := codex.Start(ctx, m.logger, m.codexPath, m.model)
	if err != nil {
		m.fail(err)
		return err
	}

	m.mu.Lock()
	m.client = client
	m.mu.Unlock()

	go m.runTask(ctx, client)
	return nil
}

func (m *Manager) Cancel(ctx context.Context) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	if m.current.State != StateRunning {
		if m.current.State == StateCancelling {
			return nil
		}
		return fmt.Errorf("no running task")
	}
	if m.client == nil || m.current.ThreadID == "" || m.current.TurnID == "" {
		return fmt.Errorf("cancel not available yet")
	}

	if err := m.client.InterruptTurn(ctx, m.current.ThreadID, m.current.TurnID); err != nil {
		return err
	}
	m.current.State = StateCancelling
	m.current.LastEvent = "interrupt requested"
	m.current.UpdatedAt = time.Now()
	go m.publish()
	return nil
}

func (m *Manager) Shutdown() {
	m.mu.Lock()
	client := m.client
	m.mu.Unlock()
	if client != nil {
		_ = client.Close()
	}
}

func (m *Manager) runTask(ctx context.Context, client *codex.Client) {
	defer func() { _ = client.Close() }()

	if err := client.Initialize(ctx); err != nil {
		m.fail(err)
		return
	}
	m.setLastEvent("initialized")

	threadID, err := client.StartThread(ctx, m.workspaceDir)
	if err != nil {
		m.fail(err)
		return
	}
	m.setThreadID(threadID)

	turnID, err := client.StartTurn(ctx, threadID, m.workspaceDir, m.Snapshot().Prompt)
	if err != nil {
		m.fail(err)
		return
	}
	m.setTurnID(turnID)

	events := client.Events()
	done := client.Done()
	for {
		select {
		case <-ctx.Done():
			m.fail(ctx.Err())
			return
		case event, ok := <-events:
			if !ok {
				events = nil
				continue
			}
			m.handleEvent(ctx, event)
		case err, ok := <-done:
			if !ok {
				return
			}
			if err != nil && !strings.Contains(err.Error(), "file already closed") {
				m.fail(err)
				return
			}
			if snap := m.Snapshot(); snap.State != StateIdle {
				m.finish("process exited", snap.CompletionText, "")
			}
			return
		}
	}
}

func (m *Manager) handleEvent(ctx context.Context, event codex.Event) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.current.LastEvent = summarizeEvent(event)
	m.current.UpdatedAt = time.Now()

	if event.ServerRequest && event.BlockingReason != "" {
		m.current.LastError = "approval/input request is not supported in this bridge"
		if m.current.State == StateRunning && m.client != nil && m.current.ThreadID != "" && m.current.TurnID != "" {
			go func(threadID, turnID string) {
				_ = m.client.InterruptTurn(ctx, threadID, turnID)
			}(m.current.ThreadID, m.current.TurnID)
			m.current.State = StateCancelling
		}
		m.publishLocked()
		return
	}

	switch event.Method {
	case "thread/started":
		if id, ok := nestedString(event.Params, "thread", "id"); ok {
			m.current.ThreadID = id
		}
	case "turn/started":
		if id, ok := nestedString(event.Params, "turn", "id"); ok {
			m.current.TurnID = id
		}
	case "item/agentMessage/delta":
		if delta, ok := stringValue(event.Params["delta"]); ok {
			m.current.AssistantText += delta
		}
	case "item/plan/delta":
		if delta, ok := stringValue(event.Params["delta"]); ok {
			m.current.PlanText += delta
		}
	case "item/reasoning/summaryTextDelta":
		if delta, ok := stringValue(event.Params["delta"]); ok {
			m.current.ReasoningText += delta
		}
	case "item/completed":
		if item, ok := event.Params["item"].(map[string]any); ok {
			switch item["type"] {
			case "agentMessage":
				if text, ok := stringValue(item["text"]); ok && strings.TrimSpace(text) != "" {
					m.current.AssistantText = text
				}
			case "plan":
				if text, ok := stringValue(item["text"]); ok && strings.TrimSpace(text) != "" {
					m.current.PlanText = text
				}
			case "reasoning":
				if summaryList, ok := item["summary"].([]any); ok {
					m.current.ReasoningText = joinAny(summaryList)
				}
			}
		}
	case "turn/completed":
		status, _ := nestedString(event.Params, "turn", "status")
		errMsg := extractTurnError(event.Params)
		finalText := strings.TrimSpace(m.current.AssistantText)
		if finalText == "" {
			finalText = strings.TrimSpace(m.current.PlanText)
		}
		m.finishLocked(status, finalText, errMsg)
		return
	case "error":
		if msg, ok := stringValue(event.Params["message"]); ok {
			m.current.LastError = msg
		}
	}

	m.publishLocked()
}

func (m *Manager) setLastEvent(v string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.current.LastEvent = v
	m.current.UpdatedAt = time.Now()
	m.publishLocked()
}

func (m *Manager) setThreadID(threadID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.current.ThreadID = threadID
	m.current.LastEvent = "thread started"
	m.current.UpdatedAt = time.Now()
	m.publishLocked()
}

func (m *Manager) setTurnID(turnID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.current.TurnID = turnID
	m.current.LastEvent = "turn started"
	m.current.UpdatedAt = time.Now()
	m.publishLocked()
}

func (m *Manager) fail(err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.finishLocked("failed", "", err.Error())
}

func (m *Manager) finish(state, completionText, errText string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.finishLocked(state, completionText, errText)
}

func (m *Manager) finishLocked(state, completionText, errText string) {
	if strings.TrimSpace(completionText) != "" {
		m.current.CompletionText = strings.TrimSpace(completionText)
	}
	m.current.CompletionState = state
	m.current.LastError = strings.TrimSpace(errText)
	m.current.EndedAt = time.Now()
	m.current.LastEvent = "turn completed"
	m.current.UpdatedAt = time.Now()
	m.current.State = StateIdle
	m.client = nil
	m.publishLocked()
}

func (m *Manager) publish() {
	m.mu.RLock()
	defer m.mu.RUnlock()
	m.publishLocked()
}

func (m *Manager) publishLocked() {
	snap := m.current
	select {
	case m.updates <- Update{Snapshot: snap}:
	default:
	}
}

func summarizeEvent(event codex.Event) string {
	switch event.Method {
	case "thread/started":
		return "thread started"
	case "turn/started":
		return "turn started"
	case "turn/completed":
		return "turn completed"
	case "item/agentMessage/delta":
		return "assistant responding"
	case "item/plan/delta":
		return "plan updated"
	case "item/reasoning/summaryTextDelta":
		return "reasoning updated"
	case "item/completed":
		return "item completed"
	case "error":
		return "server error"
	default:
		if event.Summary != "" {
			return event.Summary
		}
		return event.Method
	}
}

func nestedString(root map[string]any, path ...string) (string, bool) {
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
	return stringValue(current)
}

func stringValue(v any) (string, bool) {
	s, ok := v.(string)
	return s, ok
}

func joinAny(values []any) string {
	parts := make([]string, 0, len(values))
	for _, value := range values {
		if s, ok := stringValue(value); ok && strings.TrimSpace(s) != "" {
			parts = append(parts, s)
		}
	}
	return strings.Join(parts, "\n")
}

func extractTurnError(params map[string]any) string {
	turn, ok := params["turn"].(map[string]any)
	if !ok {
		return ""
	}
	errValue, ok := turn["error"]
	if !ok || errValue == nil {
		return ""
	}
	switch typed := errValue.(type) {
	case string:
		return typed
	case map[string]any:
		if message, ok := typed["message"].(string); ok {
			return message
		}
		return fmt.Sprintf("%v", typed)
	default:
		return fmt.Sprintf("%v", typed)
	}
}
