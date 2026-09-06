package execution

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

var ErrVerificationFailed = errors.New("verification failed")

type WorkerInput struct {
	TaskID string
	Prompt string
}

type WorkerResult struct {
	Status     string         `json:"status"`
	Summary    string         `json:"summary"`
	Changes    []string       `json:"changes"`
	Checks     map[string]any `json:"checks"`
	Artifacts  []string       `json:"artifacts"`
	Confidence float64        `json:"confidence"`
	Blockers   []string       `json:"blockers"`
}

type Worker interface {
	Run(context.Context, WorkerInput) (WorkerResult, error)
}

type Verifier interface {
	Verify(context.Context, WorkerInput, WorkerResult) error
}

type Runner struct {
	tasks    *task.Service
	worker   Worker
	verifier Verifier
}

func NewRunner(tasks *task.Service, worker Worker, verifier Verifier) *Runner {
	return &Runner{tasks: tasks, worker: worker, verifier: verifier}
}

func (r *Runner) Run(ctx context.Context, taskID string) error {
	t, err := r.tasks.Get(ctx, taskID)
	if err != nil {
		return err
	}
	if err := r.tasks.UpdateState(ctx, taskID, "running", "running", 25, nil); err != nil {
		return err
	}

	input := WorkerInput{TaskID: t.ID, Prompt: t.Prompt}
	result, err := r.worker.Run(ctx, input)
	if err != nil {
		_ = r.tasks.UpdateState(ctx, taskID, "failed", "worker", 100, nil)
		return err
	}

	if err := r.tasks.UpdateState(ctx, taskID, "verifying", "verifying", 75, nil); err != nil {
		return err
	}
	if err := r.verifier.Verify(ctx, input, result); err != nil {
		payload, marshalErr := json.Marshal(result)
		if marshalErr != nil {
			return marshalErr
		}
		text := string(payload)
		_ = r.tasks.UpdateState(ctx, taskID, "failed", "verification", 100, &text)
		return errors.Join(ErrVerificationFailed, err)
	}

	payload, err := json.Marshal(result)
	if err != nil {
		return err
	}
	text := string(payload)
	return r.tasks.UpdateState(ctx, taskID, "completed", "completed", 100, &text)
}
