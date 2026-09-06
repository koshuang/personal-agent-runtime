package task

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"strings"
	"time"
)

const MaxPromptBytes = 16 * 1024

var (
	ErrResultNotReady = errors.New("task result not ready")
	ErrPromptTooLarge = errors.New("prompt too large")
)

type Service struct {
	store *Store
}

func NewService(store *Store) *Service {
	return &Service{store: store}
}

func (s *Service) Create(ctx context.Context, prompt string) (Task, error) {
	prompt = strings.TrimSpace(prompt)
	if prompt == "" {
		return Task{}, errors.New("prompt is required")
	}
	if len(prompt) > MaxPromptBytes {
		return Task{}, ErrPromptTooLarge
	}
	now := time.Now().UTC()
	t := Task{
		ID:        newID(),
		Prompt:    prompt,
		Status:    "queued",
		Stage:     "queued",
		Progress:  0,
		CreatedAt: now,
		UpdatedAt: now,
	}
	if err := s.store.Create(ctx, t); err != nil {
		return Task{}, err
	}
	return t, nil
}

func (s *Service) Get(ctx context.Context, id string) (Task, error) {
	return s.store.Get(ctx, id)
}

func (s *Service) Cancel(ctx context.Context, id string) error {
	return s.store.Cancel(ctx, id)
}

func (s *Service) UpdateState(ctx context.Context, id, status, stage string, progress int, result *string) error {
	return s.store.UpdateState(ctx, id, status, stage, progress, result)
}

func (s *Service) Result(ctx context.Context, id string) (Task, error) {
	t, err := s.store.Get(ctx, id)
	if err != nil {
		return Task{}, err
	}
	if t.Status != "completed" || t.Result == nil {
		return t, ErrResultNotReady
	}
	return t, nil
}

func newID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return "tsk_" + hex.EncodeToString(b)
}
