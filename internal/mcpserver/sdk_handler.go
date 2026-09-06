package mcpserver

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strings"

	"github.com/koshuang/personal-agent-runtime/internal/task"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// NewSDK builds an MCP Streamable HTTP handler backed by the official MCP Go SDK.
//
// It intentionally reuses the same task.Service as the legacy adapter so the
// transport can be swapped without changing task lifecycle semantics.
func NewSDK(tasks *task.Service) http.Handler {
	server := mcp.NewServer(&mcp.Implementation{
		Name:    "personal-agent-runtime",
		Version: "0.1.0",
	}, nil)

	registerSDKTools(server, tasks)

	transport := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server {
		return server
	}, &mcp.StreamableHTTPOptions{
		Stateless:    true,
		JSONResponse: true,
	})

	return sdkOriginGuard(transport)
}

type sdkSubmitArgs struct {
	Prompt string `json:"prompt" jsonschema:"The goal or work request to queue."`
}

type sdkTaskIDArgs struct {
	TaskID string `json:"task_id" jsonschema:"Durable task identifier returned by submit_task."`
}

type sdkSubmitResult struct {
	TaskID string `json:"task_id"`
	Status string `json:"status"`
	Stage  string `json:"stage"`
}

type sdkTaskResult struct {
	TaskID    string  `json:"task_id"`
	Prompt    string  `json:"prompt"`
	Status    string  `json:"status"`
	Stage     string  `json:"stage"`
	Progress  int     `json:"progress"`
	Result    *string `json:"result,omitempty"`
	CreatedAt string  `json:"created_at"`
	UpdatedAt string  `json:"updated_at"`
}

type sdkGetResult struct {
	TaskID string `json:"task_id"`
	Status string `json:"status"`
	Ready  bool   `json:"ready"`
	Result any    `json:"result,omitempty"`
}

type sdkCancelResult struct {
	TaskID string `json:"task_id"`
	Status string `json:"status"`
}

func boolPtr(v bool) *bool { return &v }

func registerSDKTools(server *mcp.Server, tasks *task.Service) {
	mcp.AddTool(server, &mcp.Tool{
		Name:        "submit_task",
		Description: "Queue a new asynchronous task in Personal Agent Runtime. Returns a durable task_id immediately; use get_task to inspect progress later.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: false, DestructiveHint: boolPtr(false), IdempotentHint: false, OpenWorldHint: boolPtr(false)},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, args sdkSubmitArgs) (*mcp.CallToolResult, sdkSubmitResult, error) {
		t, err := tasks.Create(ctx, args.Prompt)
		if err != nil {
			return nil, sdkSubmitResult{}, err
		}
		return nil, sdkSubmitResult{TaskID: t.ID, Status: t.Status, Stage: t.Stage}, nil
	})

	mcp.AddTool(server, &mcp.Tool{
		Name:        "get_task",
		Description: "Read the persisted status, stage, progress, and metadata for a task by task_id.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, DestructiveHint: boolPtr(false), IdempotentHint: true, OpenWorldHint: boolPtr(false)},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, args sdkTaskIDArgs) (*mcp.CallToolResult, sdkTaskResult, error) {
		t, err := tasks.Get(ctx, strings.TrimSpace(args.TaskID))
		if err != nil {
			return nil, sdkTaskResult{}, normalizeSDKTaskError(err)
		}
		return nil, sdkTaskResult{
			TaskID: t.ID, Prompt: t.Prompt, Status: t.Status, Stage: t.Stage,
			Progress: t.Progress, Result: t.Result,
			CreatedAt: t.CreatedAt.Format("2006-01-02T15:04:05.999999999Z07:00"),
			UpdatedAt: t.UpdatedAt.Format("2006-01-02T15:04:05.999999999Z07:00"),
		}, nil
	})

	mcp.AddTool(server, &mcp.Tool{
		Name:        "get_task_result",
		Description: "Read the final result for a completed task. If it is not complete yet, returns ready=false and the current status.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, DestructiveHint: boolPtr(false), IdempotentHint: true, OpenWorldHint: boolPtr(false)},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, args sdkTaskIDArgs) (*mcp.CallToolResult, sdkGetResult, error) {
		t, err := tasks.Result(ctx, strings.TrimSpace(args.TaskID))
		if err != nil {
			if errors.Is(err, task.ErrResultNotReady) {
				return nil, sdkGetResult{TaskID: t.ID, Status: t.Status, Ready: false}, nil
			}
			return nil, sdkGetResult{}, normalizeSDKTaskError(err)
		}
		var result any
		if t.Result != nil {
			if err := json.Unmarshal([]byte(*t.Result), &result); err != nil {
				result = *t.Result
			}
		}
		return nil, sdkGetResult{TaskID: t.ID, Status: t.Status, Ready: true, Result: result}, nil
	})

	mcp.AddTool(server, &mcp.Tool{
		Name:        "cancel_task",
		Description: "Cancel a queued or running task. Use only when the user explicitly wants that task stopped.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: false, DestructiveHint: boolPtr(true), IdempotentHint: false, OpenWorldHint: boolPtr(false)},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, args sdkTaskIDArgs) (*mcp.CallToolResult, sdkCancelResult, error) {
		id := strings.TrimSpace(args.TaskID)
		if err := tasks.Cancel(ctx, id); err != nil {
			return nil, sdkCancelResult{}, normalizeSDKTaskError(err)
		}
		return nil, sdkCancelResult{TaskID: id, Status: "canceled"}, nil
	})
}

func normalizeSDKTaskError(err error) error {
	switch {
	case errors.Is(err, task.ErrNotFound):
		return errors.New("task not found")
	case errors.Is(err, task.ErrTerminal):
		return errors.New("task is already terminal")
	default:
		return err
	}
}

func sdkOriginGuard(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if !sdkApprovedOrigin(origin) {
			writeHTTPJSON(w, http.StatusForbidden, map[string]any{"error": "origin not allowed"})
			return
		}
		if origin != "" {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Add("Vary", "Origin")
		}
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id")
		w.Header().Set("Access-Control-Allow-Methods", "POST, GET, DELETE, OPTIONS")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func sdkApprovedOrigin(origin string) bool {
	if origin == "" {
		return true
	}
	u, err := url.Parse(origin)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") {
		return false
	}
	switch u.Hostname() {
	case "localhost", "127.0.0.1", "::1":
		return true
	default:
		return false
	}
}
