package execution

import (
	"context"
	"errors"
	"log"
	"time"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

const (
	defaultDispatchInterval = 250 * time.Millisecond
	defaultDispatchBatch    = 16
)

type TaskRunner interface {
	Run(context.Context, string) error
}

type Dispatcher struct {
	tasks    *task.Service
	runner   TaskRunner
	interval time.Duration
	batch    int
}

func NewDispatcher(tasks *task.Service, runner TaskRunner) *Dispatcher {
	return &Dispatcher{
		tasks:    tasks,
		runner:   runner,
		interval: defaultDispatchInterval,
		batch:    defaultDispatchBatch,
	}
}

func (d *Dispatcher) Run(ctx context.Context) {
	d.dispatchOnce(ctx)

	ticker := time.NewTicker(d.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			d.dispatchOnce(ctx)
		}
	}
}

func (d *Dispatcher) dispatchOnce(ctx context.Context) {
	queued, err := d.tasks.ListQueued(ctx, d.batch)
	if err != nil {
		if !errors.Is(err, context.Canceled) {
			log.Printf("dispatcher: list queued tasks: %v", err)
		}
		return
	}
	for _, t := range queued {
		if err := d.runner.Run(ctx, t.ID); err != nil {
			if errors.Is(err, task.ErrStateConflict) || errors.Is(err, context.Canceled) {
				continue
			}
			log.Printf("dispatcher: run task %s: %v", t.ID, err)
		}
	}
}
