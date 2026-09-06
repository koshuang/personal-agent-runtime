package execution

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

type failingVerifier struct{}

func (failingVerifier) Verify(context.Context, WorkerInput, WorkerResult) error {
	return errors.New("verification rejected result")
}

func openTaskService(t *testing.T) (*task.Service, *task.Store) {
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
	return task.NewService(store), store
}

func TestRunnerCompletesVerifiedZeroCostTask(t *testing.T) {
	service, _ := openTaskService(t)
	created, err := service.Create(t.Context(), "return a deterministic runtime summary")
	if err != nil {
		t.Fatalf("create task: %v", err)
	}

	runner := NewRunner(service, EchoWorker{}, DeterministicVerifier{})
	if err := runner.Run(t.Context(), created.ID); err != nil {
		t.Fatalf("run task: %v", err)
	}

	got, err := service.Result(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("result: %v", err)
	}
	if got.Status != "completed" || got.Stage != "completed" || got.Progress != 100 {
		t.Fatalf("unexpected terminal state: %+v", got)
	}
	if got.Result == nil {
		t.Fatal("completed task must persist worker result")
	}
}

func TestVerificationFailurePreventsCompletion(t *testing.T) {
	service, _ := openTaskService(t)
	created, err := service.Create(t.Context(), "this result should be rejected")
	if err != nil {
		t.Fatalf("create task: %v", err)
	}

	runner := NewRunner(service, EchoWorker{}, failingVerifier{})
	err = runner.Run(t.Context(), created.ID)
	if !errors.Is(err, ErrVerificationFailed) {
		t.Fatalf("error=%v want ErrVerificationFailed", err)
	}

	got, err := service.Get(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("get task: %v", err)
	}
	if got.Status == "completed" {
		t.Fatalf("verification failure must not complete task: %+v", got)
	}
	if got.Status != "failed" || got.Stage != "verification" {
		t.Fatalf("unexpected failed state: %+v", got)
	}
}
