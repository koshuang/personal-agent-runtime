from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(".par/runtime.db")
ROLE_VALUES = {"orchestrator", "worker", "critic", "auditor"}
DEFAULT_MAX_ATTEMPTS = 3

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  goal TEXT NOT NULL,
  idempotency_key TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  repo TEXT,
  mode TEXT NOT NULL DEFAULT 'read-only',
  context_json TEXT NOT NULL DEFAULT '{}',
  priority INTEGER NOT NULL DEFAULT 100,
  claimed_by TEXT,
  lease_expires_at TEXT,
  next_action TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
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


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "idempotency_key" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN idempotency_key TEXT")
    if "attempt_count" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
    if "max_attempts" not in columns:
        conn.execute(f"ALTER TABLE tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT {DEFAULT_MAX_ATTEMPTS}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency_key ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )


def init_db(path: Path = DEFAULT_DB) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def create_task(
    *,
    goal: str,
    repo: str | None = None,
    mode: str = "read-only",
    context: dict[str, Any] | None = None,
    priority: int = 100,
    idempotency_key: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    if idempotency_key is not None and not idempotency_key.strip():
        raise ValueError("idempotency_key must be non-empty when provided")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    task_id = str(uuid.uuid4())
    ts = now_iso()
    payload = json.dumps(context or {}, ensure_ascii=False)
    with connect(path) as conn:
        if idempotency_key is not None:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO tasks
                (id, goal, idempotency_key, status, repo, mode, context_json, priority, attempt_count, max_attempts, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (task_id, goal, idempotency_key, repo, mode, payload, priority, max_attempts, ts, ts),
            )
            if cur.rowcount == 0:
                row = conn.execute("SELECT * FROM tasks WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if not row:
                    raise RuntimeError("idempotent task insert was ignored without an existing task")
                return dict(row)
        else:
            conn.execute(
                """
                INSERT INTO tasks
                (id, goal, idempotency_key, status, repo, mode, context_json, priority, attempt_count, max_attempts, created_at, updated_at)
                VALUES (?, ?, NULL, 'pending', ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (task_id, goal, repo, mode, payload, priority, max_attempts, ts, ts),
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


def claim_task(
    *,
    task_id: str,
    worker: str,
    lease_minutes: int = 30,
    role: str = "worker",
    provider: str | None = None,
    model: str | None = None,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    if role not in ROLE_VALUES:
        raise ValueError(f"invalid role: {role}")
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(minutes=lease_minutes)).isoformat()
    ts = now.isoformat()
    run_id = str(uuid.uuid4())
    run_metadata = json.dumps({"role": role, "provider": provider, "model": model}, ensure_ascii=False)
    with connect(path) as conn:
        cur = conn.execute(
            """
            UPDATE tasks
            SET status='claimed', claimed_by=?, lease_expires_at=?, attempt_count=attempt_count+1, updated_at=?
            WHERE id=? AND attempt_count < max_attempts AND (
                status='pending' OR
                (status='claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
            )
            """,
            (worker, expires, ts, task_id, ts),
        )
        if cur.rowcount != 1:
            raise RuntimeError("task is not claimable or retry budget is exhausted")
        conn.execute(
            "INSERT INTO runs (id, task_id, worker, status, metadata_json, started_at) VALUES (?, ?, ?, 'running', ?, ?)",
            (run_id, task_id, worker, run_metadata, ts),
        )
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, 'task.claimed', ?, ?, ?)",
            (
                str(uuid.uuid4()),
                task_id,
                worker,
                json.dumps({"run_id": run_id, "lease_expires_at": expires, "role": role, "provider": provider, "model": model}),
                ts,
            ),
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


def retry_task(*, task_id: str, actor: str = "human", path: Path = DEFAULT_DB) -> dict[str, Any]:
    ts = now_iso()
    with connect(path) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise KeyError(task_id)
        if task["status"] != "failed":
            raise RuntimeError("only failed tasks can be retried")

        exhausted = task["attempt_count"] >= task["max_attempts"]
        next_status = "dead_letter" if exhausted else "pending"
        conn.execute(
            "UPDATE tasks SET status=?, claimed_by=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
            (next_status, ts, task_id),
        )
        event_type = "task.dead_lettered" if exhausted else "task.retry_queued"
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                task_id,
                event_type,
                actor,
                json.dumps({"attempt_count": task["attempt_count"], "max_attempts": task["max_attempts"]}),
                ts,
            ),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(row)


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
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task or task["status"] != "claimed" or task["claimed_by"] != worker:
            raise RuntimeError("task is not owned by this worker")
        run = conn.execute("SELECT * FROM runs WHERE id=? AND task_id=? AND worker=?", (run_id, task_id, worker)).fetchone()
        if not run:
            raise RuntimeError("run does not belong to this worker/task")

        existing_metadata = json.loads(run["metadata_json"] or "{}")
        merged_metadata = {**existing_metadata, **(metadata or {})}
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
                json.dumps(merged_metadata, ensure_ascii=False),
                ts,
                run_id,
                task_id,
                worker,
            ),
        )

        context = json.loads(task["context_json"] or "{}")
        requires_review = bool(context.get("requires_independent_review"))
        next_status = "review_pending" if requires_review else "completed"
        conn.execute(
            """
            UPDATE tasks
            SET status=?, next_action=?, lease_expires_at=NULL, updated_at=?
            WHERE id=? AND claimed_by=?
            """,
            (next_status, next_action, ts, task_id, worker),
        )
        event_type = "task.review_requested" if requires_review else "task.completed"
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                task_id,
                event_type,
                worker,
                json.dumps({"run_id": run_id, "next_action": next_action, "requires_independent_review": requires_review}),
                ts,
            ),
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
