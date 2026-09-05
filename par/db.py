from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_DB = Path(".par/runtime.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  repo TEXT,
  mode TEXT NOT NULL DEFAULT 'read-only',
  context_json TEXT NOT NULL DEFAULT '{}',
  priority INTEGER NOT NULL DEFAULT 100,
  claimed_by TEXT,
  lease_expires_at TEXT,
  next_action TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_claimable
ON tasks(status, priority, created_at);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  worker TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  blockers_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  actor TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  uri TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: Path = DEFAULT_DB) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def create_task(
    *,
    goal: str,
    repo: str | None = None,
    mode: str = "read-only",
    context: dict[str, Any] | None = None,
    priority: int = 100,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    task_id = str(uuid.uuid4())
    ts = now_iso()
    payload = json.dumps(context or {}, ensure_ascii=False)
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO tasks
            (id, goal, status, repo, mode, context_json, priority, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (task_id, goal, repo, mode, payload, priority, ts, ts),
        )
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), task_id, "task.created", "human", payload, ts),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row)


def next_task(*, path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    ts = now_iso()
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending'
               OR (status = 'claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
            """,
            (ts,),
        ).fetchone()
    return dict(row) if row else None


def claim_task(*, task_id: str, worker: str, lease_minutes: int = 30, path: Path = DEFAULT_DB) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(minutes=lease_minutes)).isoformat()
    ts = now.isoformat()
    run_id = str(uuid.uuid4())
    with connect(path) as conn:
        cur = conn.execute(
            """
            UPDATE tasks
            SET status='claimed', claimed_by=?, lease_expires_at=?, updated_at=?
            WHERE id=? AND (
                status='pending' OR
                (status='claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
            )
            """,
            (worker, expires, ts, task_id, ts),
        )
        if cur.rowcount != 1:
            raise RuntimeError("task is not claimable")
        conn.execute(
            "INSERT INTO runs (id, task_id, worker, status, started_at) VALUES (?, ?, ?, 'running', ?)",
            (run_id, task_id, worker, ts),
        )
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, 'task.claimed', ?, ?, ?)",
            (str(uuid.uuid4()), task_id, worker, json.dumps({"run_id": run_id, "lease_expires_at": expires}), ts),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    result = dict(row)
    result["run_id"] = run_id
    return result


def heartbeat(*, task_id: str, worker: str, lease_minutes: int = 30, path: Path = DEFAULT_DB) -> None:
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(minutes=lease_minutes)).isoformat()
    with connect(path) as conn:
        cur = conn.execute(
            "UPDATE tasks SET lease_expires_at=?, updated_at=? WHERE id=? AND status='claimed' AND claimed_by=?",
            (expires, now.isoformat(), task_id, worker),
        )
        if cur.rowcount != 1:
            raise RuntimeError("task is not owned by this worker")


def complete_task(
    *,
    task_id: str,
    run_id: str,
    worker: str,
    summary: str,
    evidence: list[Any] | None = None,
    blockers: list[Any] | None = None,
    next_action: str | None = None,
    metadata: dict[str, Any] | None = None,
    path: Path = DEFAULT_DB,
) -> None:
    ts = now_iso()
    with connect(path) as conn:
        conn.execute(
            """
            UPDATE runs
            SET status='completed', summary=?, evidence_json=?, blockers_json=?, metadata_json=?, finished_at=?
            WHERE id=? AND task_id=? AND worker=?
            """,
            (
                summary,
                json.dumps(evidence or [], ensure_ascii=False),
                json.dumps(blockers or [], ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
                ts,
                run_id,
                task_id,
                worker,
            ),
        )
        conn.execute(
            """
            UPDATE tasks
            SET status='completed', next_action=?, lease_expires_at=NULL, updated_at=?
            WHERE id=? AND claimed_by=?
            """,
            (next_action, ts, task_id, worker),
        )
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, 'task.completed', ?, ?, ?)",
            (str(uuid.uuid4()), task_id, worker, json.dumps({"run_id": run_id, "next_action": next_action}), ts),
        )


def fail_task(
    *, task_id: str, run_id: str, worker: str, summary: str, blockers: list[Any] | None = None, path: Path = DEFAULT_DB
) -> None:
    ts = now_iso()
    with connect(path) as conn:
        conn.execute(
            "UPDATE runs SET status='failed', summary=?, blockers_json=?, finished_at=? WHERE id=? AND task_id=? AND worker=?",
            (summary, json.dumps(blockers or [], ensure_ascii=False), ts, run_id, task_id, worker),
        )
        conn.execute(
            "UPDATE tasks SET status='failed', lease_expires_at=NULL, updated_at=? WHERE id=? AND claimed_by=?",
            (ts, task_id, worker),
        )
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, 'task.failed', ?, ?, ?)",
            (str(uuid.uuid4()), task_id, worker, json.dumps({"run_id": run_id}), ts),
        )


def get_task(task_id: str, *, path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    with connect(path) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            return None
        runs = [dict(r) for r in conn.execute("SELECT * FROM runs WHERE task_id=? ORDER BY started_at", (task_id,))]
        events = [dict(r) for r in conn.execute("SELECT * FROM events WHERE task_id=? ORDER BY created_at", (task_id,))]
        artifacts = [dict(r) for r in conn.execute("SELECT * FROM artifacts WHERE task_id=? ORDER BY created_at", (task_id,))]
    result = dict(task)
    result["runs"] = runs
    result["events"] = events
    result["artifacts"] = artifacts
    return result
