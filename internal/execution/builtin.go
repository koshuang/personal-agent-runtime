package execution

import (
	"context"
	"errors"
	"strings"
)

// EchoWorker is a deterministic zero-cost worker used to prove the runtime
// execution contract without invoking an external model or paid API.
type EchoWorker struct{}

func (EchoWorker) Run(_ context.Context, input WorkerInput) (WorkerResult, error) {
	prompt := strings.TrimSpace(input.Prompt)
	if prompt == "" {
		return WorkerResult{}, errors.New("prompt is required")
	}
	return WorkerResult{
		Status:     "success",
		Summary:    "Processed task without external model: " + prompt,
		Changes:    []string{},
		Checks:     map[string]any{"worker": "echo", "max_cost_usd": 0},
		Artifacts:  []string{},
		Confidence: 1,
		Blockers:   []string{},
	}, nil
}

// DeterministicVerifier rejects incomplete or internally inconsistent worker
// output. More domain-specific verifiers can be added without changing the Task API.
type DeterministicVerifier struct{}

func (DeterministicVerifier) Verify(_ context.Context, _ WorkerInput, result WorkerResult) error {
	if result.Status != "success" {
		return errors.New("worker status is not success")
	}
	if strings.TrimSpace(result.Summary) == "" {
		return errors.New("worker summary is required")
	}
	if result.Confidence < 0 || result.Confidence > 1 {
		return errors.New("confidence must be between 0 and 1")
	}
	if len(result.Blockers) > 0 {
		return errors.New("worker reported blockers")
	}
	return nil
}
