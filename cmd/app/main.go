package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"telegram-codex/internal/config"
	"telegram-codex/internal/render"
	"telegram-codex/internal/session"
	"telegram-codex/internal/telegram"
)

func main() {
	configPath := flag.String("config", "config.yaml", "path to config file")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if err != nil {
		showFatalError("Telecodex", fmt.Sprintf("Failed to load config.\n\n%v", err))
		os.Exit(1)
	}

	logger, closeLog, err := newLogger(cfg.LogFile)
	if err != nil {
		showFatalError("Telecodex", fmt.Sprintf("Failed to open log file.\n\n%v", err))
		os.Exit(1)
	}
	defer closeLog()

	unlock, err := acquireSingleInstanceLock(filepath.Join(filepath.Dir(cfg.LogFile), "telecodex.lock"))
	if err != nil {
		logger.Printf("single instance lock failed: %v", err)
		showFatalError("Telecodex", err.Error())
		os.Exit(1)
	}
	defer func() {
		if err := unlock(); err != nil {
			logger.Printf("remove lock failed: %v", err)
		}
	}()

	rootCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	logger.Printf("starting telecodex with workspace=%s", cfg.WorkspaceDir)

	messageRenderer := render.New(cfg.Language, cfg.Model)

	statusWindow, err := startStatusUI(
		"Telecodex",
		cfg.WorkspaceDir,
		cfg.LogFile,
		statusText(cfg.Language, readyHeadline(cfg.Language), cfg.WorkspaceDir, cfg.LogFile, cfg.Model, waitingDetail(cfg.Language)),
	)
	if err != nil {
		logger.Printf("status ui start failed: %v", err)
	} else {
		defer statusWindow.Close()
		go func() {
			<-statusWindow.Done()
			stop()
		}()
	}

	tg := telegram.NewClient(cfg.BotToken)
	manager := session.NewManager(cfg.WorkspaceDir, cfg.CodexPath, cfg.Model, logger)
	defer manager.Shutdown()

	progressUI := newTelegramUI(tg, logger, messageRenderer)
	go progressUI.run(rootCtx, manager.Updates(), time.Duration(cfg.ProgressUpdateSec)*time.Second)
	go watchSession(rootCtx, statusWindow, manager, cfg.Language, cfg.WorkspaceDir, cfg.LogFile, cfg.Model)

	if err := pollLoop(rootCtx, cfg, tg, manager, progressUI, logger, statusWindow, messageRenderer); err != nil && !errors.Is(err, context.Canceled) {
		logger.Printf("poll loop ended with error: %v", err)
		if statusWindow != nil {
			statusWindow.SetStatus(statusText(cfg.Language, errorHeadline(cfg.Language), cfg.WorkspaceDir, cfg.LogFile, cfg.Model, err.Error()))
		}
	}
}

func pollLoop(
	ctx context.Context,
	cfg config.Config,
	tg *telegram.Client,
	manager *session.Manager,
	progressUI *telegramUI,
	logger *log.Logger,
	statusWindow statusUI,
	messageRenderer render.Renderer,
) error {
	var offset int64

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		updates, err := tg.GetUpdates(ctx, offset, cfg.PollTimeoutSec)
		if err != nil {
			logger.Printf("telegram getUpdates failed: %v", err)
			if statusWindow != nil {
				statusWindow.SetStatus(statusText(cfg.Language, reconnectingHeadline(cfg.Language), cfg.WorkspaceDir, cfg.LogFile, cfg.Model, err.Error()))
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(2 * time.Second):
			}
			continue
		}

		for _, update := range updates {
			offset = update.UpdateID + 1
			if update.Message == nil {
				continue
			}
			if statusWindow != nil {
				statusWindow.SetStatus(statusText(cfg.Language, readyHeadline(cfg.Language), cfg.WorkspaceDir, cfg.LogFile, cfg.Model, waitingDetail(cfg.Language)))
			}
			handleMessage(ctx, cfg, tg, manager, progressUI, update.Message, logger, messageRenderer)
		}
	}
}

func handleMessage(
	ctx context.Context,
	cfg config.Config,
	tg *telegram.Client,
	manager *session.Manager,
	progressUI *telegramUI,
	msg *telegram.Message,
	logger *log.Logger,
	messageRenderer render.Renderer,
) {
	if msg.From == nil {
		return
	}
	if msg.Chat.Type != "private" {
		logger.Printf("ignored non-private chat %d", msg.Chat.ID)
		return
	}
	if msg.From.ID != cfg.AllowedUserID {
		logger.Printf("ignored unauthorized user %d", msg.From.ID)
		return
	}

	text := strings.TrimSpace(msg.Text)
	if text == "" {
		return
	}

	switch text {
	case "/help":
		sendBestEffort(ctx, tg, msg.Chat.ID, messageRenderer.HelpMessage(), logger)
		return
	case "/status":
		snap := manager.Snapshot()
		if snap.State == session.StateIdle {
			sendBestEffort(ctx, tg, msg.Chat.ID, messageRenderer.StatusMessage(snap), logger)
			return
		}
		sendBestEffort(ctx, tg, msg.Chat.ID, messageRenderer.ActiveStatusMessage(snap.State), logger)
		return
	case "/cancel":
		if err := manager.Cancel(ctx); err != nil {
			sendBestEffort(ctx, tg, msg.Chat.ID, localize(cfg.Language, "지금 취소할 수 있는 작업이 없습니다.\n\n"+err.Error(), "No active task can be cancelled right now.\n\n"+err.Error()), logger)
			return
		}
		sendBestEffort(ctx, tg, msg.Chat.ID, localize(cfg.Language, "취소 요청을 보냈습니다. /status로 상태를 확인하세요.", "Cancel request sent. Use /status to watch progress."), logger)
		return
	}

	snap := manager.Snapshot()
	if snap.State != session.StateIdle {
		sendBestEffort(ctx, tg, msg.Chat.ID, messageRenderer.BusyMessage(snap.State), logger)
		return
	}

	progressUI.prepareTask(msg.Chat.ID)
	if err := manager.StartTask(ctx, text); err != nil {
		progressUI.clearTask()
		sendBestEffort(ctx, tg, msg.Chat.ID, localize(cfg.Language, "작업 시작 실패: "+err.Error(), "Failed to start task: "+err.Error()), logger)
		return
	}

	startSnap := manager.Snapshot()
	progressUI.attachTask(msg.Chat.ID, startSnap.StartedAt)
}

func sendBestEffort(ctx context.Context, tg *telegram.Client, chatID int64, text string, logger *log.Logger) {
	if _, err := tg.SendMessage(ctx, chatID, text); err != nil {
		logger.Printf("telegram send message failed: %v", err)
	}
}

func newLogger(path string) (*log.Logger, func() error, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, nil, fmt.Errorf("create log dir: %w", err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, nil, fmt.Errorf("open log file: %w", err)
	}
	logger := log.New(f, "", log.LstdFlags|log.Lmicroseconds)
	return logger, f.Close, nil
}

func watchSession(ctx context.Context, statusWindow statusUI, manager *session.Manager, language, workspaceDir, logFile, model string) {
	if statusWindow == nil {
		return
	}
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			snap := manager.Snapshot()
			statusWindow.SetStatus(statusText(language, sessionHeadline(language, snap), workspaceDir, logFile, model, sessionDetail(language, snap)))
		}
	}
}

func sessionHeadline(language string, s session.Snapshot) string {
	switch s.State {
	case session.StateRunning:
		return localize(language, "작업 실행 중", "Task Running")
	case session.StateCancelling:
		return localize(language, "작업 취소 중", "Cancelling Task")
	default:
		if !s.EndedAt.IsZero() {
			if s.LastError != "" {
				return localize(language, "대기 중 - 이전 오류", "Ready - Previous Error")
			}
			return localize(language, "대기 중 - 마지막 작업 완료", "Ready - Last Task Complete")
		}
		return readyHeadline(language)
	}
}

func sessionDetail(language string, s session.Snapshot) string {
	switch s.State {
	case session.StateRunning, session.StateCancelling:
		if s.Prompt != "" {
			return localize(language, "현재 요청: ", "Current prompt: ") + truncateSingleLine(s.Prompt, 90)
		}
		return localize(language, "작업 준비 중...", "Preparing task...")
	default:
		if s.LastError != "" {
			return localize(language, "마지막 오류: ", "Last error: ") + truncateSingleLine(s.LastError, 90)
		}
		if s.CompletionText != "" {
			return localize(language, "마지막 결과: ", "Last result: ") + truncateSingleLine(s.CompletionText, 90)
		}
		if s.AssistantText != "" {
			return localize(language, "마지막 결과: ", "Last result: ") + truncateSingleLine(s.AssistantText, 90)
		}
		return waitingDetail(language)
	}
}

func statusText(language, headline, workspaceDir, logFile, model, detail string) string {
	lines := []string{
		headline,
		"",
		localize(language, "작업 폴더: ", "Workspace: ") + workspaceDir,
		localize(language, "로그 파일: ", "Log file: ") + logFile,
	}
	if strings.TrimSpace(model) != "" {
		lines = append(lines, localize(language, "모델: ", "Model: ")+model)
	}
	if strings.TrimSpace(detail) != "" {
		lines = append(lines, "", detail)
	}
	return strings.Join(lines, "\n")
}

func truncateSingleLine(s string, limit int) string {
	s = strings.ReplaceAll(s, "\r\n", " ")
	s = strings.ReplaceAll(s, "\n", " ")
	s = strings.TrimSpace(s)
	runes := []rune(s)
	if len(runes) <= limit {
		return s
	}
	return string(runes[:limit-3]) + "..."
}

func localize(language, ko, en string) string {
	if strings.EqualFold(language, "ko") {
		return ko
	}
	return en
}

func readyHeadline(language string) string {
	return localize(language, "준비됨", "Ready")
}

func reconnectingHeadline(language string) string {
	return localize(language, "재연결 중", "Reconnecting")
}

func errorHeadline(language string) string {
	return localize(language, "오류", "Error")
}

func waitingDetail(language string) string {
	return localize(language, "Telegram 메시지를 기다리는 중", "Waiting for Telegram messages")
}

func acquireSingleInstanceLock(path string) (func() error, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		if errors.Is(err, os.ErrExist) {
			return nil, fmt.Errorf("another Telecodex instance is already running; stop the existing process or remove %s if it is stale", path)
		}
		return nil, err
	}

	if _, err := fmt.Fprintf(f, "pid=%d\nstarted_at=%s\n", os.Getpid(), time.Now().Format(time.RFC3339)); err != nil {
		_ = f.Close()
		_ = os.Remove(path)
		return nil, err
	}

	return func() error {
		if err := f.Close(); err != nil {
			return err
		}
		return os.Remove(path)
	}, nil
}

type telegramUI struct {
	client *telegram.Client
	logger *log.Logger

	mu sync.Mutex

	taskChatID    int64
	taskStartedAt time.Time
	finalizedFor  time.Time
	renderer      render.Renderer
}

func newTelegramUI(client *telegram.Client, logger *log.Logger, renderer render.Renderer) *telegramUI {
	return &telegramUI{client: client, logger: logger, renderer: renderer}
}

func (u *telegramUI) prepareTask(chatID int64) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.taskChatID = chatID
	u.taskStartedAt = time.Time{}
	u.finalizedFor = time.Time{}
}

func (u *telegramUI) attachTask(chatID int64, startedAt time.Time) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.taskChatID = chatID
	u.taskStartedAt = startedAt
	u.finalizedFor = time.Time{}
}

func (u *telegramUI) clearTask() {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.taskChatID = 0
	u.taskStartedAt = time.Time{}
	u.finalizedFor = time.Time{}
}

func (u *telegramUI) run(ctx context.Context, updates <-chan session.Update, _ time.Duration) {
	for {
		select {
		case <-ctx.Done():
			return
		case update, ok := <-updates:
			if !ok {
				return
			}
			u.handleUpdate(ctx, update.Snapshot)
		}
	}
}

func (u *telegramUI) handleUpdate(ctx context.Context, snap session.Snapshot) {
	if snap.State != session.StateIdle || snap.EndedAt.IsZero() {
		return
	}
	u.sendCompletion(ctx, snap)
}

func (u *telegramUI) sendCompletion(ctx context.Context, snap session.Snapshot) {
	u.mu.Lock()
	if u.taskChatID == 0 {
		u.mu.Unlock()
		return
	}
	if !u.finalizedFor.IsZero() && snap.EndedAt.Equal(u.finalizedFor) {
		u.mu.Unlock()
		return
	}
	if u.taskStartedAt.IsZero() {
		u.taskStartedAt = snap.StartedAt
	}
	if !u.taskStartedAt.IsZero() && !snap.StartedAt.IsZero() && !snap.StartedAt.Equal(u.taskStartedAt) {
		u.mu.Unlock()
		return
	}

	chatID := u.taskChatID
	text := u.renderer.CompletionMessage(snap)
	u.mu.Unlock()

	if _, err := u.client.SendMessage(ctx, chatID, text); err != nil {
		u.logger.Printf("send completion message failed: %v", err)
		return
	}

	u.mu.Lock()
	u.finalizedFor = snap.EndedAt
	u.taskChatID = 0
	u.taskStartedAt = time.Time{}
	u.mu.Unlock()
}
