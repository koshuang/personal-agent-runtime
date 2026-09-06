package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/koshuang/personal-agent-runtime/internal/artifact"
	"github.com/koshuang/personal-agent-runtime/internal/execution"
	"github.com/koshuang/personal-agent-runtime/internal/mcpserver"
	"github.com/koshuang/personal-agent-runtime/internal/task"
)

func TestMCPWorkspaceExecutionPersistsVerifiedResultAcrossRestart(t *testing.T) {
	root := t.TempDir()
	workspace := filepath.Join(root, "workspace")
	if err := os.MkdirAll(workspace, 0o755); err != nil {
		t.Fatalf("mkdir workspace: %v", err)
	}
	if err := os.WriteFile(filepath.Join(workspace, "README.md"), []byte("personal agent runtime e2e\n"), 0o644); err != nil {
		t.Fatalf("write workspace fixture: %v", err)
	}

	dbPath := filepath.Join(root, "runtime.db")
	artifactRoot := filepath.Join(root, "artifacts")

	store1, err := task.Open(dbPath)
	if err != nil {
		t.Fatalf("open first store: %v", err)
	}
	artifacts1, err := artifact.NewStore(artifactRoot)
	if err != nil {
		t.Fatalf("open first artifact store: %v", err)
	}
	workspaceWorker, err := execution.NewReadOnlyWorkspaceWorker(workspace)
	if err != nil {
		t.Fatalf("new workspace worker: %v", err)
	}
	router, err := execution.NewRouter(0, execution.RouteCandidate{
		Name:             "workspace",
		Worker:           workspaceWorker,
		EstimatedCostUSD: 0,
	})
	if err != nil {
		t.Fatalf("new router: %v", err)
	}

	service1 := task.NewService(store1)
	runner := execution.NewRunner(
		service1,
		router,
		execution.DeterministicVerifier{},
		execution.WithArtifactWriter(artifacts1),
	)
	dispatcher := execution.NewDispatcher(service1, runner)
	dispatchCtx, cancelDispatch := context.WithCancel(context.Background())
	dispatchDone := make(chan struct{})
	go func() {
		defer close(dispatchDone)
		dispatcher.Run(dispatchCtx)
	}()

	handler1 := mcpserver.New(service1)
	submit := mcpE2ECall(t, handler1, 1, "submit_task", map[string]any{
		"prompt": "read README.md",
	})
	taskID, _ := submit["task_id"].(string)
	if taskID == "" || submit["status"] != "queued" {
		t.Fatalf("unexpected submit result: %#v", submit)
	}

	var final map[string]any
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		result := mcpE2ECall(t, handler1, 2, "get_task_result", map[string]any{
			"task_id": taskID,
		})
		if ready, _ := result["ready"].(bool); ready {
			final = result
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if final == nil {
		persisted, getErr := service1.Get(context.Background(), taskID)
		if getErr != nil {
			t.Fatalf("task %s did not complete and could not be read: %v", taskID, getErr)
		}
		t.Fatalf("task %s did not produce an MCP result before timeout: status=%s stage=%s progress=%d", taskID, persisted.Status, persisted.Stage, persisted.Progress)
	}

	workerResult := decodeWorkerResult(t, final["result"])
	if workerResult.Checks["worker"] != "readonly-workspace" || workerResult.Checks["route_worker"] != "workspace" {
		t.Fatalf("unexpected worker routing evidence: %#v", workerResult.Checks)
	}
	if workerResult.Checks["content"] != "personal agent runtime e2e\n" {
		t.Fatalf("unexpected workspace content: %#v", workerResult.Checks["content"])
	}
	if workerResult.Checks["max_cost_usd"] != float64(0) || workerResult.Checks["route_estimated_cost_usd"] != float64(0) {
		t.Fatalf("expected zero-cost routing evidence: %#v", workerResult.Checks)
	}
	if len(workerResult.Artifacts) != 1 || workerResult.Artifacts[0] == "" {
		t.Fatalf("unexpected artifact refs: %#v", workerResult.Artifacts)
	}
	artifactRef := workerResult.Artifacts[0]

	artifactBytes, err := artifacts1.Read(context.Background(), artifactRef)
	if err != nil {
		t.Fatalf("read execution artifact: %v", err)
	}
	var artifactResult execution.WorkerResult
	if err := json.Unmarshal(artifactBytes, &artifactResult); err != nil {
		t.Fatalf("decode execution artifact: %v", err)
	}
	if artifactResult.Checks["content"] != "personal agent runtime e2e\n" {
		t.Fatalf("artifact does not contain verified workspace evidence: %#v", artifactResult.Checks)
	}

	cancelDispatch()
	select {
	case <-dispatchDone:
	case <-time.After(time.Second):
		t.Fatal("dispatcher did not stop before restart")
	}
	if err := store1.Close(); err != nil {
		t.Fatalf("close first store: %v", err)
	}

	store2, err := task.Open(dbPath)
	if err != nil {
		t.Fatalf("reopen store: %v", err)
	}
	t.Cleanup(func() {
		if err := store2.Close(); err != nil {
			t.Errorf("close reopened store: %v", err)
		}
	})
	service2 := task.NewService(store2)
	handler2 := mcpserver.New(service2)

	restarted := mcpE2ECall(t, handler2, 3, "get_task_result", map[string]any{
		"task_id": taskID,
	})
	if ready, _ := restarted["ready"].(bool); !ready {
		t.Fatalf("persisted result not ready after restart: %#v", restarted)
	}
	restartedResult := decodeWorkerResult(t, restarted["result"])
	if restartedResult.Checks["content"] != "personal agent runtime e2e\n" {
		t.Fatalf("persisted result changed after restart: %#v", restartedResult.Checks)
	}

	artifacts2, err := artifact.NewStore(artifactRoot)
	if err != nil {
		t.Fatalf("reopen artifact store: %v", err)
	}
	if _, err := artifacts2.Read(context.Background(), artifactRef); err != nil {
		t.Fatalf("artifact unavailable after restart: %v", err)
	}
}

func decodeWorkerResult(t *testing.T, raw any) execution.WorkerResult {
	t.Helper()
	data, err := json.Marshal(raw)
	if err != nil {
		t.Fatalf("marshal worker result: %v", err)
	}
	var result execution.WorkerResult
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatalf("decode worker result: %v; raw=%#v", err, raw)
	}
	return result
}

func mcpE2ECall(t *testing.T, h http.Handler, id int, tool string, args map[string]any) map[string]any {
	t.Helper()
	body, err := json.Marshal(map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  "tools/call",
		"params": map[string]any{
			"name":      tool,
			"arguments": args,
		},
	})
	if err != nil {
		t.Fatalf("marshal MCP request: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/mcp", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("MCP %s status=%d body=%s", tool, res.Code, res.Body.String())
	}
	var rpc map[string]any
	if err := json.NewDecoder(res.Body).Decode(&rpc); err != nil {
		t.Fatalf("decode MCP %s response: %v; body=%s", tool, err, res.Body.String())
	}
	result, ok := rpc["result"].(map[string]any)
	if !ok {
		t.Fatalf("MCP %s missing result: %#v", tool, rpc)
	}
	if result["isError"] == true {
		t.Fatalf("MCP %s returned tool error: %#v", tool, result)
	}
	structured, ok := result["structuredContent"].(map[string]any)
	if !ok {
		t.Fatalf("MCP %s missing structuredContent: %#v", tool, result)
	}
	return structured
}
