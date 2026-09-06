package task

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

var (
	ErrNotFound      = errors.New("task not found")
	ErrTerminal      = errors.New("task is already terminal")
	ErrStateConflict = errors.New("task state changed concurrently")
)

type Task struct {
	ID        string    `json:"task_id"`
	Prompt    string    `json:"prompt"`
	Status    string    `json:"status"`
	Stage     string    `json:"stage"`
	Progress  int       `json:"progress"`
	Result    *string   `json:"result,omitempty"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

type Store struct{ db *sql.DB }

func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}

	// The v0.1 runtime is a single-process SQLite application. Serializing all
	// access through one pooled connection prevents independent database/sql
	// connections from contending on SQLite file locks while the dispatcher and
	// API read/update the same durable state.
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)

	s := &Store{db: db}
	if err := s.migrate(context.Background()); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) migrate(ctx context.Context) error {
	_, err := s.db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS api_tasks (
  id TEXT PRIMARY KEY,
  prompt TEXT NOT NULL,
  status TEXT NOT NULL,
  stage TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_tasks_status_created_at
ON api_tasks(status, created_at);
`)
	return err
}

func (s *Store) Create(ctx context.Context, t Task) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO api_tasks(id,prompt,status,stage,progress,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)`,
		t.ID, t.Prompt, t.Status, t.Stage, t.Progress, t.Result, t.CreatedAt.Format(time.RFC3339Nano), t.UpdatedAt.Format(time.RFC3339Nano),
	)
	return err
}

func (s *Store) Get(ctx context.Context, id string) (Task, error) {
	var t Task
	var created, updated string
	var result sql.NullString
	err := s.db.QueryRowContext(ctx,
		`SELECT id,prompt,status,stage,progress,result,created_at,updated_at FROM api_tasks WHERE id=?`, id,
	).Scan(&t.ID, &t.Prompt, &t.Status, &t.Stage, &t.Progress, &result, &created, &updated)
	if errors.Is(err, sql.ErrNoRows) {
		return Task{}, ErrNotFound
	}
	if err != nil {
		return Task{}, err
	}
	if result.Valid {
		t.Result = &result.String
	}
	t.CreatedAt, _ = time.Parse(time.RFC3339Nano, created)
	t.UpdatedAt, _ = time.Parse(time.RFC3339Nano, updated)
	return t, nil
}

func (s *Store) ListQueued(ctx context.Context, limit int) ([]Task, error) {
	if limit <= 0 {
		return []Task{}, nil
	}
	rows, err := s.db.QueryContext(ctx, `
SELECT id,prompt,status,stage,progress,result,created_at,updated_at
FROM api_tasks
WHERE status='queued'
ORDER BY created_at ASC
LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make([]Task, 0, limit)
	for rows.Next() {
		var t Task
		var created, updated string
		var result sql.NullString
		if err := rows.Scan(&t.ID, &t.Prompt, &t.Status, &t.Stage, &t.Progress, &result, &created, &updated); err != nil {
			return nil, err
		}
		if result.Valid {
			t.Result = &result.String
		}
		t.CreatedAt, _ = time.Parse(time.RFC3339Nano, created)
		t.UpdatedAt, _ = time.Parse(time.RFC3339Nano, updated)
		out = append(out, t)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return out, nil
}

func (s *Store) UpdateState(ctx context.Context, id, expectedStatus, status, stage string, progress int, result *string) error {
	res, err := s.db.ExecContext(ctx,
		`UPDATE api_tasks SET status=?,stage=?,progress=?,result=?,updated_at=? WHERE id=? AND status=?`,
		status, stage, progress, result, time.Now().UTC().Format(time.RFC3339Nano), id, expectedStatus,
	)
	if err != nil {
		return err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n > 0 {
		return nil
	}

	var currentStatus string
	if err := s.db.QueryRowContext(ctx, `SELECT status FROM api_tasks WHERE id=?`, id).Scan(&currentStatus); errors.Is(err, sql.ErrNoRows) {
		return ErrNotFound
	} else if err != nil {
		return fmt.Errorf("check task after state update: %w", err)
	}
	return fmt.Errorf("%w: expected %s, got %s", ErrStateConflict, expectedStatus, currentStatus)
}

func (s *Store) Cancel(ctx context.Context, id string) error {
	res, err := s.db.ExecContext(ctx, `
UPDATE api_tasks
SET status='canceled', stage='canceled', updated_at=?
WHERE id=? AND status NOT IN ('completed','failed','canceled')`,
		time.Now().UTC().Format(time.RFC3339Nano), id,
	)
	if err != nil {
		return err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n > 0 {
		return nil
	}

	var exists int
	if err := s.db.QueryRowContext(ctx, `SELECT 1 FROM api_tasks WHERE id=?`, id).Scan(&exists); errors.Is(err, sql.ErrNoRows) {
		return ErrNotFound
	} else if err != nil {
		return fmt.Errorf("check task after cancel: %w", err)
	}
	return ErrTerminal
}
