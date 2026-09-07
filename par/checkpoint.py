from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect, now_iso

CHECKPOINT_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  worker TEXT NOT NULL,
  summary TEXT NOT NULL,
  completed_steps_json TEXT NOT NULL DEFAULT '[]',
  remaining_steps_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  blockers_json TEXT NOT NULL DEFAULT '[]',
  resume_hint TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_task_created
ON checkpoints(task_id, created_at DESC);
"""


def _ensure_schema(conn) -> None:
    conn.executescript(CHECKPOINT_SCHEMA)


def _validate_shapes(
    *,
    completed_steps: Any,
    remaining_steps: Any,
    evidence: Any,
    blockers: Any,
    metadata: Any,
) -> None:
    for name, value in (
        ("completed_steps", completed_steps),
        ("remaining_steps", remaining_steps),
        ("evidence", evidence),
        ("blockers", blockers),
    ):
        if value is not None and not isinstance(value, list):
            raise ValueError(f"{name} must be a list")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")


def write_checkpoint(
    *,
    task_id: str,
    run_id: str,
    worker: str,
    summary: str,
    completed_steps: list[Any] | None = None,
    remaining_steps: list[Any] | None = None,
    evidence: list[Any] | None = None,
    blockers: list[Any] | None = None,
    resume_hint: str | None = None,
    metadata: dict[str, Any] | None = None,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    if not summary.strip():
        raise ValueError("summary must be non-empty")
    _validate_shapes(
        completed_steps=completed_steps,
        remaining_steps=remaining_steps,
        evidence=evidence,
        blockers=blockers,
        metadata=metadata,
    )

    checkpoint_id = str(uuid.uuid4())
    ts = now_iso()
    payload = {
        "checkpoint_id": checkpoint_id,
        "run_id": run_id,
        "summary": summary,
        "completed_steps": completed_steps or [],
        "remaining_steps": remaining_steps or [],
        "evidence": evidence or [],
        "blockers": blockers or [],
        "resume_hint": resume_hint,
        "metadata": metadata or {},
    }

    with connect(path) as conn:
        _ensure_schema(conn)
        # Acquire the SQLite write lock before checking ownership. This makes the
        # ownership/lease/run validation and checkpoint persistence one atomic
        # state transition relative to completion, expiry/reclaim, or reassignment.
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute(
            """
            SELECT * FROM tasks
            WHERE id=? AND status='claimed' AND claimed_by=?
              AND lease_expires_at IS NOT NULL AND lease_expires_at>?
            """,
            (task_id, worker, ts),
        ).fetchone()
        if not task:
            raise RuntimeError("task is not actively owned by this worker")

        run = conn.execute(
            "SELECT * FROM runs WHERE id=? AND task_id=? AND worker=? AND status='running'",
            (run_id, task_id, worker),
        ).fetchone()
        if not run:
            raise RuntimeError("run is not active for this worker/task")

        conn.execute(
            """
            INSERT INTO checkpoints
            (id, task_id, run_id, worker, summary, completed_steps_json, remaining_steps_json,
             evidence_json, blockers_json, resume_hint, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint_id,
                task_id,
                run_id,
                worker,
                summary,
                json.dumps(completed_steps or [], ensure_ascii=False),
                json.dumps(remaining_steps or [], ensure_ascii=False),
                json.dumps(evidence or [], ensure_ascii=False),
                json.dumps(blockers or [], ensure_ascii=False),
                resume_hint,
                json.dumps(metadata or {}, ensure_ascii=False),
                ts,
            ),
        )
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, 'task.checkpointed', ?, ?, ?)",
            (str(uuid.uuid4()), task_id, worker, json.dumps(payload, ensure_ascii=False), ts),
        )
        row = conn.execute("SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()

    return _decode_checkpoint(dict(row))


def latest_checkpoint(*, task_id: str, path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    with connect(path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE task_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    return _decode_checkpoint(dict(row)) if row else None


def resume_context(*, task_id: str, path: Path = DEFAULT_DB) -> dict[str, Any]:
    with connect(path) as conn:
        _ensure_schema(conn)
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise KeyError(task_id)
    checkpoint = latest_checkpoint(task_id=task_id, path=path)
    return {
        "task": dict(task),
        "checkpoint": checkpoint,
        "resume_available": checkpoint is not None,
    }


def _decode_checkpoint(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for source, target in (
        ("completed_steps_json", "completed_steps"),
        ("remaining_steps_json", "remaining_steps"),
        ("evidence_json", "evidence"),
        ("blockers_json", "blockers"),
        ("metadata_json", "metadata"),
    ):
        result[target] = json.loads(result.pop(source) or ("{}" if target == "metadata" else "[]"))
    return result
