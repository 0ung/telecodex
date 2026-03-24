package render

import (
	"fmt"
	"strings"
	"time"

	"telegram-codex/internal/session"
)

type Renderer struct {
	language string
	model    string
}

func New(language, model string) Renderer {
	lang := strings.ToLower(strings.TrimSpace(language))
	if lang != "ko" {
		lang = "en"
	}
	return Renderer{
		language: lang,
		model:    strings.TrimSpace(model),
	}
}

func (r Renderer) Language() string { return r.language }

func (r Renderer) HelpMessage() string {
	if r.isKorean() {
		return strings.TrimSpace(`
명령어

/help
/status
/cancel

일반 텍스트를 보내면 새 Codex 작업을 시작합니다.
작업 중에는 /status, /cancel만 사용할 수 있습니다.
`)
	}

	return strings.TrimSpace(`
Commands

/help
/status
/cancel

Send plain text to start a new Codex task.
While a task is active, only /status and /cancel are allowed.
`)
}

func (r Renderer) StartMessage(s session.Snapshot) string {
	lines := []string{r.text("작업 시작", "Task started")}
	if r.model != "" {
		lines = append(lines, r.field("모델", "Model", r.model))
	}
	lines = append(lines, "", r.text("요청", "Request"), excerpt(s.Prompt, 700))
	return strings.Join(lines, "\n")
}

func (r Renderer) BusyMessage(state session.State) string {
	if state == session.StateCancelling {
		return r.text(
			"취소 중입니다. 지금은 /status만 사용할 수 있습니다.",
			"Cancellation is already in progress. Only /status is available right now.",
		)
	}
	return r.text(
		"작업 중입니다. /status 또는 /cancel만 사용할 수 있습니다.",
		"A task is already running. Use /status or /cancel.",
	)
}

func (r Renderer) ActiveStatusMessage(state session.State) string {
	if state == session.StateCancelling {
		return r.text("취소 중입니다.", "Cancellation in progress.")
	}
	return r.text("작업 중입니다.", "Task running.")
}

func (r Renderer) StatusMessage(s session.Snapshot) string {
	lines := []string{r.text("현재 상태", "Current status"), ""}
	lines = append(lines, r.field("상태", "State", r.stateLabel(s.State)))
	lines = append(lines, r.field("최근 이벤트", "Last event", r.eventLabel(s.LastEvent)))
	if r.model != "" {
		lines = append(lines, r.field("모델", "Model", r.model))
	}
	if !s.StartedAt.IsZero() && s.State != session.StateIdle {
		lines = append(lines, r.field("시작", "Started", formatTime(s.StartedAt)))
		lines = append(lines, r.field("경과", "Elapsed", time.Since(s.StartedAt).Round(time.Second).String()))
	}
	if s.ThreadID != "" {
		lines = append(lines, r.field("Thread", "Thread", s.ThreadID))
	}
	if s.TurnID != "" {
		lines = append(lines, r.field("Turn", "Turn", s.TurnID))
	}

	summary := currentSummary(s)
	if summary != "" {
		lines = append(lines, "", r.text("요약", "Summary"), excerpt(summary, 1400))
	}
	if s.State == session.StateIdle && !s.EndedAt.IsZero() {
		lines = append(lines, "", r.field("마지막 완료", "Last completed", formatTime(s.EndedAt)))
		if s.CompletionState != "" {
			lines = append(lines, r.field("완료 상태", "Completion state", r.completionStateLabel(s.CompletionState)))
		}
		if s.LastError != "" {
			lines = append(lines, "", r.text("오류", "Error"), excerpt(s.LastError, 500))
		}
	}
	return strings.Join(lines, "\n")
}

func (r Renderer) ProgressMessage(s session.Snapshot) string {
	lines := []string{r.text("작업 진행 중", "Task running")}
	lines = append(lines, r.field("상태", "State", r.stateLabel(s.State)))
	lines = append(lines, r.field("이벤트", "Event", r.eventLabel(s.LastEvent)))
	if r.model != "" {
		lines = append(lines, r.field("모델", "Model", r.model))
	}
	if !s.StartedAt.IsZero() {
		lines = append(lines, r.field("경과", "Elapsed", time.Since(s.StartedAt).Round(time.Second).String()))
	}

	preview := progressPreview(s)
	if preview != "" {
		lines = append(lines, "", r.text("진행 내용", "Live preview"), excerpt(preview, 1200))
	}
	if s.LastError != "" {
		lines = append(lines, "", r.text("메모", "Note"), excerpt(s.LastError, 280))
	}
	lines = append(lines, "", r.text("자세한 정보는 /status", "Use /status for details."))
	return strings.Join(lines, "\n")
}

func (r Renderer) CompletionMessage(s session.Snapshot) string {
	lines := []string{r.text("작업 완료", "Task complete")}

	meta := make([]string, 0, 2)
	if s.CompletionState != "" {
		meta = append(meta, r.completionStateLabel(s.CompletionState))
	}
	if !s.StartedAt.IsZero() && !s.EndedAt.IsZero() {
		meta = append(meta, s.EndedAt.Sub(s.StartedAt).Round(time.Second).String())
	}
	if len(meta) > 0 {
		lines = append(lines, strings.Join(meta, " | "))
	}
	if r.model != "" {
		lines = append(lines, r.field("모델", "Model", r.model))
	}
	if s.LastError != "" {
		lines = append(lines, "", r.text("오류", "Error"), excerpt(s.LastError, 500))
	}

	result := strings.TrimSpace(s.CompletionText)
	if result == "" {
		result = strings.TrimSpace(s.AssistantText)
	}
	if result == "" {
		result = r.text("최종 응답이 비어 있습니다.", "No final assistant text was captured.")
	}

	lines = append(lines, "", r.text("결과", "Result"), excerpt(result, 1600))
	lines = append(lines, "", r.text("자세한 정보는 /status", "Use /status for details."))
	return strings.Join(lines, "\n")
}

func formatTime(t time.Time) string {
	if t.IsZero() {
		return "-"
	}
	return t.Format("2006-01-02 15:04:05")
}

func currentSummary(s session.Snapshot) string {
	switch {
	case strings.TrimSpace(s.AssistantText) != "":
		return s.AssistantText
	case strings.TrimSpace(s.PlanText) != "":
		return s.PlanText
	case strings.TrimSpace(s.ReasoningText) != "":
		return s.ReasoningText
	case strings.TrimSpace(s.Prompt) != "":
		return s.Prompt
	default:
		return ""
	}
}

func progressPreview(s session.Snapshot) string {
	switch {
	case strings.TrimSpace(s.AssistantText) != "":
		return s.AssistantText
	case strings.TrimSpace(s.PlanText) != "":
		return s.PlanText
	case strings.TrimSpace(s.ReasoningText) != "":
		return s.ReasoningText
	default:
		return ""
	}
}

func excerpt(s string, limit int) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return "-"
	}
	runes := []rune(s)
	if len(runes) <= limit {
		return s
	}
	return string(runes[:limit-3]) + "..."
}

func (r Renderer) field(koLabel, enLabel, value string) string {
	label := enLabel
	if r.isKorean() {
		label = koLabel
	}
	return fmt.Sprintf("%s: %s", label, value)
}

func (r Renderer) text(ko, en string) string {
	if r.isKorean() {
		return ko
	}
	return en
}

func (r Renderer) stateLabel(state session.State) string {
	switch state {
	case session.StateRunning:
		return r.text("실행 중", "Running")
	case session.StateCancelling:
		return r.text("취소 중", "Cancelling")
	default:
		return r.text("대기 중", "Idle")
	}
}

func (r Renderer) completionStateLabel(v string) string {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "completed":
		return r.text("완료", "Completed")
	case "interrupted":
		return r.text("중단됨", "Interrupted")
	case "failed":
		return r.text("실패", "Failed")
	case "cancelled", "canceled":
		return r.text("취소됨", "Cancelled")
	case "":
		return "-"
	default:
		return v
	}
}

func (r Renderer) eventLabel(v string) string {
	switch strings.TrimSpace(v) {
	case "":
		return "-"
	case "codex starting":
		return r.text("Codex 시작 중", "Starting Codex")
	case "initialized":
		return r.text("초기화 완료", "Initialized")
	case "thread started":
		return r.text("Thread 시작", "Thread started")
	case "turn started":
		return r.text("Turn 시작", "Turn started")
	case "turn completed":
		return r.text("Turn 완료", "Turn completed")
	case "assistant responding":
		return r.text("응답 생성 중", "Generating response")
	case "plan updated":
		return r.text("계획 갱신", "Plan updated")
	case "reasoning updated":
		return r.text("추론 갱신", "Reasoning updated")
	case "item completed":
		return r.text("항목 완료", "Item completed")
	case "server error":
		return r.text("서버 오류", "Server error")
	case "interrupt requested":
		return r.text("중단 요청됨", "Interrupt requested")
	case "idle":
		return r.text("대기 중", "Idle")
	default:
		return v
	}
}

func (r Renderer) isKorean() bool {
	return r.language == "ko"
}
