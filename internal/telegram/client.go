package telegram

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func NewClient(botToken string) *Client {
	return &Client{
		baseURL: "https://api.telegram.org/bot" + botToken,
		http: &http.Client{
			Timeout: 70 * time.Second,
		},
	}
}

type apiResponse[T any] struct {
	OK          bool   `json:"ok"`
	Description string `json:"description"`
	Result      T      `json:"result"`
}

type Update struct {
	UpdateID int64    `json:"update_id"`
	Message  *Message `json:"message"`
}

type Message struct {
	MessageID int64  `json:"message_id"`
	Date      int64  `json:"date"`
	Text      string `json:"text"`
	Chat      Chat   `json:"chat"`
	From      *User  `json:"from"`
}

type Chat struct {
	ID   int64  `json:"id"`
	Type string `json:"type"`
}

type User struct {
	ID        int64  `json:"id"`
	IsBot     bool   `json:"is_bot"`
	Username  string `json:"username"`
	FirstName string `json:"first_name"`
	LastName  string `json:"last_name"`
}

type sendMessageRequest struct {
	ChatID                int64  `json:"chat_id"`
	Text                  string `json:"text"`
	DisableWebPagePreview bool   `json:"disable_web_page_preview,omitempty"`
}

type editMessageTextRequest struct {
	ChatID                int64  `json:"chat_id"`
	MessageID             int64  `json:"message_id"`
	Text                  string `json:"text"`
	DisableWebPagePreview bool   `json:"disable_web_page_preview,omitempty"`
}

func (c *Client) GetUpdates(ctx context.Context, offset int64, timeoutSec int) ([]Update, error) {
	reqBody := map[string]any{
		"offset":          offset,
		"timeout":         timeoutSec,
		"allowed_updates": []string{"message"},
	}

	var resp apiResponse[[]Update]
	if err := c.call(ctx, "getUpdates", reqBody, &resp); err != nil {
		return nil, err
	}
	return resp.Result, nil
}

func (c *Client) SendMessage(ctx context.Context, chatID int64, text string) (*Message, error) {
	req := sendMessageRequest{
		ChatID:                chatID,
		Text:                  normalizeText(text),
		DisableWebPagePreview: true,
	}

	var resp apiResponse[Message]
	if err := c.call(ctx, "sendMessage", req, &resp); err != nil {
		return nil, err
	}
	return &resp.Result, nil
}

func (c *Client) EditMessageText(ctx context.Context, chatID, messageID int64, text string) error {
	req := editMessageTextRequest{
		ChatID:                chatID,
		MessageID:             messageID,
		Text:                  normalizeText(text),
		DisableWebPagePreview: true,
	}

	var resp apiResponse[Message]
	return c.call(ctx, "editMessageText", req, &resp)
}

func (c *Client) call(ctx context.Context, method string, payload any, out any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("telegram %s marshal: %w", method, err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/"+method, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("telegram %s request: %w", method, err)
	}
	req.Header.Set("Content-Type", "application/json")

	res, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("telegram %s do: %w", method, err)
	}
	defer res.Body.Close()

	if err := json.NewDecoder(res.Body).Decode(out); err != nil {
		return fmt.Errorf("telegram %s decode: %w", method, err)
	}

	switch typed := out.(type) {
	case *apiResponse[[]Update]:
		if !typed.OK {
			return fmt.Errorf("telegram %s failed: %s", method, typed.Description)
		}
	case *apiResponse[Message]:
		if !typed.OK {
			return fmt.Errorf("telegram %s failed: %s", method, typed.Description)
		}
	}
	return nil
}

func normalizeText(s string) string {
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = strings.TrimSpace(s)
	if s == "" {
		return "."
	}
	if len([]rune(s)) <= 4096 {
		return s
	}
	runes := []rune(s)
	return string(runes[:4093]) + "..."
}
