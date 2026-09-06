package execution

import (
	"context"
	"errors"
	"testing"
)

type countingWorker struct {
	called int
	name   string
}

func (w *countingWorker) Run(_ context.Context, _ WorkerInput) (WorkerResult, error) {
	w.called++
	return WorkerResult{
		Status:     "success",
		Summary:    w.name,
		Checks:     map[string]any{},
		Confidence: 1,
	}, nil
}

func TestRouterSkipsPaidWorkerAtZeroBudget(t *testing.T) {
	paid := &countingWorker{name: "paid"}
	free := &countingWorker{name: "free"}
	router, err := NewRouter(0,
		RouteCandidate{Name: "paid", Worker: paid, EstimatedCostUSD: 0.01},
		RouteCandidate{Name: "free", Worker: free, EstimatedCostUSD: 0},
	)
	if err != nil {
		t.Fatalf("new router: %v", err)
	}
	result, err := router.Run(context.Background(), WorkerInput{Prompt: "work"})
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if paid.called != 0 {
		t.Fatalf("paid worker must not run under zero budget, calls=%d", paid.called)
	}
	if free.called != 1 {
		t.Fatalf("free worker calls=%d", free.called)
	}
	if result.Checks["route_worker"] != "free" || result.Checks["max_cost_usd"] != float64(0) {
		t.Fatalf("unexpected routing evidence: %+v", result.Checks)
	}
}

func TestRouterRejectsWhenOnlyPaidWorkerExists(t *testing.T) {
	paid := &countingWorker{name: "paid"}
	router, err := NewRouter(0, RouteCandidate{Name: "paid", Worker: paid, EstimatedCostUSD: 0.01})
	if err != nil {
		t.Fatalf("new router: %v", err)
	}
	_, err = router.Run(context.Background(), WorkerInput{Prompt: "work"})
	if !errors.Is(err, ErrNoAffordableWorker) {
		t.Fatalf("expected budget rejection, got %v", err)
	}
	if paid.called != 0 {
		t.Fatalf("paid worker must not run, calls=%d", paid.called)
	}
}

func TestRouterAllowsWorkerWithinBudget(t *testing.T) {
	paid := &countingWorker{name: "paid"}
	router, err := NewRouter(0.02, RouteCandidate{Name: "paid", Worker: paid, EstimatedCostUSD: 0.01})
	if err != nil {
		t.Fatalf("new router: %v", err)
	}
	if _, err := router.Run(context.Background(), WorkerInput{Prompt: "work"}); err != nil {
		t.Fatalf("run: %v", err)
	}
	if paid.called != 1 {
		t.Fatalf("paid worker calls=%d", paid.called)
	}
}
