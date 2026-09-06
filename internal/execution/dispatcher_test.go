package execution

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

func TestDispatcherProcessesPersistedQueuedTask(t *testing.T) {
	store, err := task.Open(filepath.Join(t.TempDir(), "runtime.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Errorf("close store: %v", err)
		}
	})

	service := task.NewService(store)
	created, err := service.Create(t.Context(), "background dispatch proof")
	if err != nil {
		t.Fatalf("create task: %v", err)
	}

	runner := NewRunner(service, EchoWorker{}, DeterministicVerifier{})
	dispatcher := NewDispatcher(service, runner)
	dispatcher.interval = 10 * time.Millisecond

	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	go dispatcher.Run(ctx)

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		got, err := service.Get(t.Context(), created.ID)
		if err != nil {
			t.Fatalf("get task: %v", err)
		}
		if got.Status == "completed" {
			if got.Result == nil {
				t.Fatal("completed task missing result")
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}

	got, err := service.Get(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("get task after timeout: %v", err)
	}
	t.Fatalf("task did not complete: status=%s stage=%s progress=%d", got.Status, got.Stage, got.Progress)
}

func TestDispatcherStartupRecoversQueuedTasks(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "runtime.db")
	store1, err := task.Open(dbPath)
	if err != nil {
		t.Fatalf("open first store: %v", err)
	}
	service1 := task.NewService(store1)
	created, err := service1.Create(t.Context(), "survive restart before dispatch")
	if err != nil {
		t.Fatalf("create task: %v", err)
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
			t.Errorf("close second store: %v", err)
		}
	})
	service2 := task.NewService(store2)
	runner := NewRunner(service2, EchoWorker{}, DeterministicVerifier{})
	dispatcher := NewDispatcher(service2, runner)

	// The initial synchronous scan in Run must recover work that was queued
	// before this dispatcher instance existed.
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		dispatcher.Run(ctx)
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		got, err := service2.Get(t.Context(), created.ID)
		if err != nil {
			cancel()
			t.Fatalf("get recovered task: %v", err)
		}
		if got.Status == "completed" {
			cancel()
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	t.Fatal("queued task was not recovered after store reopen")
}
