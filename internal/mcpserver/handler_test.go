package mcpserver

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

func newTestHandler(t *testing.T) (http.Handler, *task.Store) {
	t.Helper()
	store, err := task.Open(filepath.Join(t.TempDir(), "runtime.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Errorf("close store: %v", err)
		}
	})
	return New(task.NewService(store)), store
}

func rpcCall(t *testing.T, h http.Handler, body string) map[string]any {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", res.Code, res.Body.String())
	}
	var out map[string]any
	if err := json.NewDecoder(res.Body).Decode(&out); err != nil {
		t.Fatalf("decode response: %v; body=%s", err, res.Body.String())
	}
	return out
}

func TestInitializeAndToolsList(t *testing.T) {
	h, _ := newTestHandler(t)

	init := rpcCall(t, h, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"test","version":"1"},"capabilities":{}}}`)
	result := init["result"].(map[string]any)
	if result["protocolVersion"] != "2025-06-18" {
		t.Fatalf("unexpected protocol version: %#v", result["protocolVersion"])
	}
	if result["serverInfo"].(map[string]any)["name"] != "personal-agent-runtime" {
		t.Fatalf("unexpected server info: %#v", result["serverInfo"])
	}

	listed := rpcCall(t, h, `{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}`)
	tools := listed["result"].(map[string]any)["tools"].([]any)
	if len(tools) != 4 {
		t.Fatalf("tools=%d want=4", len(tools))
	}
	want := map[string]bool{"submit_task": false, "get_task": false, "get_task_result": false, "cancel_task": false}
	for _, raw := range tools {
		tool := raw.(map[string]any)
		name := tool["name"].(string)
		if _, ok := want[name]; !ok {
			t.Fatalf("unexpected tool %q", name)
		}
		want[name] = true
		if tool["inputSchema"] == nil || tool["annotations"] == nil {
			t.Fatalf("tool %q missing schema/annotations", name)
		}
	}
	for name, seen := range want {
		if !seen {
			t.Fatalf("missing tool %q", name)
		}
	}
}

func TestUnsupportedFutureProtocolFallsBack(t *testing.T) {
	h, _ := newTestHandler(t)

	init := rpcCall(t, h, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28"}}`)
	result := init["result"].(map[string]any)
	if result["protocolVersion"] != "2025-06-18" {
		t.Fatalf("protocolVersion=%v want=2025-06-18", result["protocolVersion"])
	}
}

func TestMCPTaskLifecycle(t *testing.T) {
	h, store := newTestHandler(t)

	submit := rpcCall(t, h, `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"submit_task","arguments":{"prompt":"verify MCP bridge"}}}`)
	submitResult := submit["result"].(map[string]any)
	if submitResult["isError"] == true {
		t.Fatalf("submit returned tool error: %#v", submitResult)
	}
	structured := submitResult["structuredContent"].(map[string]any)
	taskID := structured["task_id"].(string)
	if taskID == "" || structured["status"] != "queued" {
		t.Fatalf("unexpected submit result: %#v", structured)
	}

	get := rpcCall(t, h, `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_task","arguments":{"task_id":"`+taskID+`"}}}`)
	getStructured := get["result"].(map[string]any)["structuredContent"].(map[string]any)
	if getStructured["task_id"] != taskID || getStructured["status"] != "queued" {
		t.Fatalf("unexpected get result: %#v", getStructured)
	}

	pending := rpcCall(t, h, `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_task_result","arguments":{"task_id":"`+taskID+`"}}}`)
	pendingStructured := pending["result"].(map[string]any)["structuredContent"].(map[string]any)
	if pendingStructured["ready"] != false {
		t.Fatalf("expected pending result: %#v", pendingStructured)
	}

	cancel := rpcCall(t, h, `{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"cancel_task","arguments":{"task_id":"`+taskID+`"}}}`)
	cancelStructured := cancel["result"].(map[string]any)["structuredContent"].(map[string]any)
	if cancelStructured["status"] != "canceled" {
		t.Fatalf("unexpected cancel result: %#v", cancelStructured)
	}

	persisted, err := store.Get(t.Context(), taskID)
	if err != nil {
		t.Fatalf("get persisted task: %v", err)
	}
	if persisted.Status != "canceled" {
		t.Fatalf("persisted status=%q want=canceled", persisted.Status)
	}
}

func TestOversizedPromptRejectedBeforePersistence(t *testing.T) {
	h, _ := newTestHandler(t)

	prompt := strings.Repeat("x", task.MaxPromptBytes+1)
	body, err := json.Marshal(map[string]any{
		"jsonrpc": "2.0",
		"id":      1,
		"method":  "tools/call",
		"params": map[string]any{
			"name":      "submit_task",
			"arguments": map[string]any{"prompt": prompt},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	out := rpcCall(t, h, string(body))
	result := out["result"].(map[string]any)
	if result["isError"] != true {
		t.Fatalf("expected tool error: %#v", result)
	}
}

func TestUnapprovedOriginIsForbidden(t *testing.T) {
	h, _ := newTestHandler(t)

	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewBufferString(`{"jsonrpc":"2.0","id":1,"method":"ping"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "https://attacker.example")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)

	if res.Code != http.StatusForbidden {
		t.Fatalf("status=%d want=%d", res.Code, http.StatusForbidden)
	}
	if got := res.Header().Get("Access-Control-Allow-Origin"); got != "" {
		t.Fatalf("unexpected access-control-allow-origin=%q", got)
	}
}

func TestApprovedLocalOriginIsEchoed(t *testing.T) {
	h, _ := newTestHandler(t)

	req := httptest.NewRequest(http.MethodOptions, "/mcp", nil)
	req.Header.Set("Origin", "http://localhost:3000")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)

	if res.Code != http.StatusNoContent {
		t.Fatalf("status=%d want=%d", res.Code, http.StatusNoContent)
	}
	if got := res.Header().Get("Access-Control-Allow-Origin"); got != "http://localhost:3000" {
		t.Fatalf("access-control-allow-origin=%q", got)
	}
	if got := res.Header().Get("Access-Control-Allow-Headers"); !strings.Contains(got, "Authorization") {
		t.Fatalf("access-control-allow-headers=%q missing Authorization", got)
	}
}

func TestInitializedNotificationHasNoBody(t *testing.T) {
	h, _ := newTestHandler(t)

	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewBufferString(`{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}`))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusAccepted {
		t.Fatalf("status=%d want=%d", res.Code, http.StatusAccepted)
	}
	if res.Body.Len() != 0 {
		t.Fatalf("notification should have empty body: %q", res.Body.String())
	}
}
