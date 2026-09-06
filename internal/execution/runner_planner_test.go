package execution

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

type plannerFunc func(context.Context, WorkerInput) (WorkerInput, error)

func (f plannerFunc) Plan(ctx context.Context, input WorkerInput) (WorkerInput, error) {
	return f(ctx, input)
}

type captureWorker struct {
	input WorkerInput
}

func (w *captureWorker) Run(_ context.Context, input WorkerInput) (WorkerResult, error) {
	w.input = input
	return WorkerResult{
		Status:     "success",
		Summary:    "planned work completed",
		Changes:    []string{},
		Checks:     map[string]any{"worker": "capture", "max_cost_usd": 0},
		Artifacts:  []string{},
		Confidence: 1,
		Blockers:   []string{},
	}, nil
}

func TestRunnerOptionalPlannerCanTransformWorkerInput(t *testing.T) {
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
	created, err := service.Create(t.Context(), "complex request")
	if err != nil {
		t.Fatalf("create task: %v", err)
	}

	worker := &captureWorker{}
	planner := plannerFunc(func(_ context.Context, input WorkerInput) (WorkerInput, error) {
		current, err := service.Get(t.Context(), input.TaskID)
		if err != nil {
			return WorkerInput{}, err
		}
		if current.Status != "planning" || current.Stage != "planning" || current.Progress != 10 {
			t.Fatalf("planner called outside planning state: %+v", current)
		}
		input.Prompt = "read README.md"
		return input, nil
	})

	runner := NewRunner(service, worker, DeterministicVerifier{}, WithPlanner(planner))
	if err := runner.Run(t.Context(), created.ID); err != nil {
		t.Fatalf("run: %v", err)
	}
	if worker.input.TaskID != created.ID || worker.input.Prompt != "read README.md" {
		t.Fatalf("worker did not receive planned input: %+v", worker.input)
	}
	completed, err := service.Get(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("get completed task: %v", err)
	}
	if completed.Status != "completed" || completed.Stage != "completed" || completed.Progress != 100 {
		t.Fatalf("unexpected completed task: %+v", completed)
	}
}

func TestRunnerPlannerFailurePreventsWorkerExecution(t *testing.T) {
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
	created, err := service.Create(t.Context(), "complex request")
	if err != nil {
		t.Fatalf("create task: %v", err)
	}

	worker := &captureWorker{}
	planErr := errors.New("planner unavailable")
	planner := plannerFunc(func(context.Context, WorkerInput) (WorkerInput, error) {
		return WorkerInput{}, planErr
	})
	runner := NewRunner(service, worker, DeterministicVerifier{}, WithPlanner(planner))
	if err := runner.Run(t.Context(), created.ID); !errors.Is(err, planErr) {
		t.Fatalf("expected planner error, got %v", err)
	}
	if worker.input.TaskID != "" {
		t.Fatalf("worker must not run after planner failure: %+v", worker.input)
	}
	failed, err := service.Get(t.Context(), created.ID)
	if err != nil {
		t.Fatalf("get failed task: %v", err)
	}
	if failed.Status != "failed" || failed.Stage != "planner" || failed.Progress != 100 {
		t.Fatalf("unexpected failed task: %+v", failed)
	}
}
