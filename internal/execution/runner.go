package execution

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

var ErrVerificationFailed = errors.New("verification failed")

const terminalUpdateTimeout = 2 * time.Second

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

type ArtifactWriter interface {
	Put(context.Context, string, string, []byte) (string, error)
}

type RunnerOption func(*Runner)

func WithArtifactWriter(writer ArtifactWriter) RunnerOption {
	return func(r *Runner) { r.artifacts = writer }
}

type Runner struct {
	tasks     *task.Service
	worker    Worker
	verifier  Verifier
	artifacts ArtifactWriter
}

func NewRunner(tasks *task.Service, worker Worker, verifier Verifier, opts ...RunnerOption) *Runner {
	r := &Runner{tasks: tasks, worker: worker, verifier: verifier}
	for _, opt := range opts {
		opt(r)
	}
	return r
}

func (r *Runner) Run(ctx context.Context, taskID string) error {
	t, err := r.tasks.Get(ctx, taskID)
	if err != nil {
		return err
	}
	if err := r.tasks.UpdateState(ctx, taskID, "queued", "running", "running", 25, nil); err != nil {
		return err
	}

	input := WorkerInput{TaskID: t.ID, Prompt: t.Prompt}
	result, err := r.worker.Run(ctx, input)
	if err != nil {
		return errors.Join(err, r.persistFailure(taskID, "running", "worker"))
	}

	if err := r.tasks.UpdateState(ctx, taskID, "running", "verifying", "verifying", 75, nil); err != nil {
		return err
	}
	if err := r.verifier.Verify(ctx, input, result); err != nil {
		return errors.Join(ErrVerificationFailed, err, r.persistFailure(taskID, "verifying", "verification"))
	}

	verifiedPayload, err := json.Marshal(result)
	if err != nil {
		return errors.Join(err, r.persistFailure(taskID, "verifying", "result"))
	}
	if r.artifacts != nil {
		ref, err := r.artifacts.Put(ctx, taskID, "worker-result.json", verifiedPayload)
		if err != nil {
			return errors.Join(err, r.persistFailure(taskID, "verifying", "artifact"))
		}
		result.Artifacts = append(result.Artifacts, ref)
	}

	payload, err := json.Marshal(result)
	if err != nil {
		return errors.Join(err, r.persistFailure(taskID, "verifying", "result"))
	}
	text := string(payload)
	return r.tasks.UpdateState(ctx, taskID, "verifying", "completed", "completed", 100, &text)
}

func (r *Runner) persistFailure(taskID, expectedStatus, stage string) error {
	cleanupCtx, cancel := context.WithTimeout(context.Background(), terminalUpdateTimeout)
	defer cancel()
	return r.tasks.UpdateState(cleanupCtx, taskID, expectedStatus, "failed", stage, 100, nil)
}
