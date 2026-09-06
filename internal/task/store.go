package task

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
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
		return Task{}, fmt.Errorf("task not found")
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

func (s *Store) UpdateState(ctx context.Context, id, status, stage string, progress int, result *string) error {
	res, err := s.db.ExecContext(ctx,
		`UPDATE api_tasks SET status=?,stage=?,progress=?,result=?,updated_at=? WHERE id=?`,
		status, stage, progress, result, time.Now().UTC().Format(time.RFC3339Nano), id,
	)
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return fmt.Errorf("task not found")
	}
	return nil
}
