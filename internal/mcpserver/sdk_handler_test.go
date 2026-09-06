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

func newSDKTestHandler(t *testing.T) (http.Handler, *task.Store) {
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
	return NewSDK(task.NewService(store)), store
}

func sdkRPCCall(t *testing.T, h http.Handler, body string) map[string]any {
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

func TestOfficialSDKInitializeAndToolsList(t *testing.T) {
	h, _ := newSDKTestHandler(t)

	init := sdkRPCCall(t, h, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","clientInfo":{"name":"test","version":"1"},"capabilities":{}}}`)
	result := init["result"].(map[string]any)
	if result["protocolVersion"] != "2025-06-18" {
		t.Fatalf("unexpected protocol version: %#v", result["protocolVersion"])
	}
	if result["serverInfo"].(map[string]any)["name"] != "personal-agent-runtime" {
		t.Fatalf("unexpected server info: %#v", result["serverInfo"])
	}

	listed := sdkRPCCall(t, h, `{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}`)
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

func TestOfficialSDKTaskLifecycle(t *testing.T) {
	h, store := newSDKTestHandler(t)

	submit := sdkRPCCall(t, h, `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"submit_task","arguments":{"prompt":"verify official sdk bridge"}}}`)
	submitResult := submit["result"].(map[string]any)
	if submitResult["isError"] == true {
		t.Fatalf("submit returned tool error: %#v", submitResult)
	}
	structured := submitResult["structuredContent"].(map[string]any)
	taskID := structured["task_id"].(string)
	if taskID == "" || structured["status"] != "queued" {
		t.Fatalf("unexpected submit result: %#v", structured)
	}

	get := sdkRPCCall(t, h, `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_task","arguments":{"task_id":"`+taskID+`"}}}`)
	getStructured := get["result"].(map[string]any)["structuredContent"].(map[string]any)
	if getStructured["task_id"] != taskID || getStructured["status"] != "queued" {
		t.Fatalf("unexpected get result: %#v", getStructured)
	}

	pending := sdkRPCCall(t, h, `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_task_result","arguments":{"task_id":"`+taskID+`"}}}`)
	pendingStructured := pending["result"].(map[string]any)["structuredContent"].(map[string]any)
	if pendingStructured["ready"] != false {
		t.Fatalf("expected pending result: %#v", pendingStructured)
	}

	cancel := sdkRPCCall(t, h, `{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"cancel_task","arguments":{"task_id":"`+taskID+`"}}}`)
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

func TestOfficialSDKUnapprovedOriginIsForbidden(t *testing.T) {
	h, _ := newSDKTestHandler(t)
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewBufferString(`{"jsonrpc":"2.0","id":1,"method":"ping"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", "https://attacker.example")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusForbidden {
		t.Fatalf("status=%d want=%d", res.Code, http.StatusForbidden)
	}
}

func TestOfficialSDKRejectsOversizedRequestBody(t *testing.T) {
	h, _ := newSDKTestHandler(t)
	body := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"submit_task","arguments":{"prompt":"` + strings.Repeat("x", maxMCPRequestBytes) + `"}}}`
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code == http.StatusOK {
		t.Fatalf("oversized request unexpectedly succeeded: %s", res.Body.String())
	}
}
