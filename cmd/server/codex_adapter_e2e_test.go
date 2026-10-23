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

	"github.com/koshuang/personal-agent-runtime/internal/artifact"
	"github.com/koshuang/personal-agent-runtime/internal/execution"
	"github.com/koshuang/personal-agent-runtime/internal/mcpserver"
	"github.com/koshuang/personal-agent-runtime/internal/task"
)

func TestCodexAdapterPreservesVerifiedEvidenceThroughHTTPAndMCP(t *testing.T) {
	root := t.TempDir()
	executable := filepath.Join(root, "codex-fixture")
	fixture := "#!/bin/sh\n" +
		"while [ $# -gt 0 ]; do\n" +
		"  if [ \"$1\" = \"--output-last-message\" ]; then shift; output=$1; fi\n" +
		"  shift\n" +
		"done\n" +
		"printf 'verified trusted-local Codex task' > \"$output\"\n"
	if err := os.WriteFile(executable, []byte(fixture), 0o755); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	newRuntime := func(t *testing.T) (*task.Store, *task.Service, *execution.Runner) {
		t.Helper()
		store, err := task.Open(filepath.Join(t.TempDir(), "runtime.db"))
		if err != nil {
			t.Fatalf("open store: %v", err)
		}
		artifacts, err := artifact.NewStore(filepath.Join(t.TempDir(), "artifacts"))
		if err != nil {
			_ = store.Close()
			t.Fatalf("open artifacts: %v", err)
		}
		worker, err := execution.NewCodexWorker(execution.CodexWorkerConfig{Executable: executable, WorkspaceRoot: root})
		if err != nil {
			_ = store.Close()
			t.Fatalf("new codex worker: %v", err)
		}
		router, err := execution.NewRouter(0, execution.RouteCandidate{Name: "codex", Worker: worker, EstimatedCostUSD: 0})
		if err != nil {
			_ = store.Close()
			t.Fatalf("new router: %v", err)
		}
		service := task.NewService(store)
		runner := execution.NewRunner(service, router, execution.DeterministicVerifier{}, execution.WithArtifactWriter(artifacts))
		return store, service, runner
	}

	t.Run("HTTP", func(t *testing.T) {
		store, service, runner := newRuntime(t)
		defer store.Close()
		s := &server{tasks: service}
		create := httptest.NewRecorder()
		testMux(s).ServeHTTP(create, httptest.NewRequest(http.MethodPost, "/v1/tasks", bytes.NewBufferString(`{"prompt":"summarize fixture"}`)))
		if create.Code != http.StatusAccepted {
			t.Fatalf("create status=%d body=%s", create.Code, create.Body.String())
		}
		var created struct {
			TaskID string `json:"task_id"`
		}
		if err := json.NewDecoder(create.Body).Decode(&created); err != nil {
			t.Fatalf("decode create: %v", err)
		}
		if err := runner.Run(context.Background(), created.TaskID); err != nil {
			t.Fatalf("run: %v", err)
		}
		result := httptest.NewRecorder()
		testMux(s).ServeHTTP(result, httptest.NewRequest(http.MethodGet, "/v1/tasks/"+created.TaskID+"/result", nil))
		if result.Code != http.StatusOK {
			t.Fatalf("result status=%d body=%s", result.Code, result.Body.String())
		}
		assertCodexHTTPResult(t, result.Body.Bytes())
	})

	t.Run("MCP", func(t *testing.T) {
		store, service, runner := newRuntime(t)
		defer store.Close()
		handler := mcpserver.NewSDK(service)
		submitted := mcpE2ECall(t, handler, 1, "submit_task", map[string]any{"prompt": "summarize fixture"})
		taskID, _ := submitted["task_id"].(string)
		if taskID == "" {
			t.Fatalf("unexpected submitted task: %#v", submitted)
		}
		if err := runner.Run(context.Background(), taskID); err != nil {
			t.Fatalf("run: %v", err)
		}
		result := mcpE2ECall(t, handler, 2, "get_task_result", map[string]any{"task_id": taskID})
		workerResult := decodeWorkerResult(t, result["result"])
		assertCodexWorkerResult(t, workerResult)
	})
}

func assertCodexHTTPResult(t *testing.T, body []byte) {
	t.Helper()
	var payload struct {
		Result execution.WorkerResult `json:"result"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		t.Fatalf("decode HTTP result: %v", err)
	}
	assertCodexWorkerResult(t, payload.Result)
}

func assertCodexWorkerResult(t *testing.T, result execution.WorkerResult) {
	t.Helper()
	if result.Status != "success" || result.Summary != "verified trusted-local Codex task" {
		t.Fatalf("unexpected result: %+v", result)
	}
	if result.Checks["provider"] != "codex" || result.Checks["workspace_policy"] != "trusted-local-read-only" || result.Checks["exit_status"] != float64(0) {
		t.Fatalf("missing durable execution evidence: %#v", result.Checks)
	}
	if len(result.Artifacts) != 1 || result.Artifacts[0] == "" {
		t.Fatalf("verified result missing artifact: %#v", result.Artifacts)
	}
}
