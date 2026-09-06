package execution

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"github.com/koshuang/personal-agent-runtime/internal/artifact"
	"github.com/koshuang/personal-agent-runtime/internal/task"
)

func TestRunnerPersistsVerifiedWorkerArtifact(t *testing.T) {
	store, err := task.Open(filepath.Join(t.TempDir(), "runtime.db"))
	if err != nil {
		t.Fatalf("open task store: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Errorf("close task store: %v", err)
		}
	})
	artifacts, err := artifact.NewStore(filepath.Join(t.TempDir(), "artifacts"))
	if err != nil {
		t.Fatalf("new artifact store: %v", err)
	}
	service := task.NewService(store)
	created, err := service.Create(t.Context(), "persist verified evidence")
	if err != nil {
		t.Fatalf("create task: %v", err)
	}

	runner := NewRunner(service, EchoWorker{}, DeterministicVerifier{}, WithArtifactWriter(artifacts))
	if err := runner.Run(t.Context(), created.ID); err != nil {
		t.Fatalf("run: %v", err)
	}
	completed, err := service.Result(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("result: %v", err)
	}
	var result WorkerResult
	if err := json.Unmarshal([]byte(*completed.Result), &result); err != nil {
		t.Fatalf("decode result: %v", err)
	}
	if len(result.Artifacts) != 1 {
		t.Fatalf("artifacts=%v", result.Artifacts)
	}
	payload, err := artifacts.Read(t.Context(), result.Artifacts[0])
	if err != nil {
		t.Fatalf("read artifact: %v", err)
	}
	var evidence WorkerResult
	if err := json.Unmarshal(payload, &evidence); err != nil {
		t.Fatalf("decode artifact: %v", err)
	}
	if evidence.Summary == "" || evidence.Status != "success" {
		t.Fatalf("unexpected evidence: %+v", evidence)
	}
}
