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
  sequence INTEGER NOT NULL,
  context_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_task_sequence
ON checkpoints(task_id, sequence DESC);
"""

_SECRET_KEYS = {
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
}


def ensure_checkpoint_schema(*, path: Path = DEFAULT_DB) -> None:
    with connect(path) as conn:
        conn.executescript(CHECKPOINT_SCHEMA)


def _contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SECRET_KEYS:
                return True
            if _contains_secret_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_key(child) for child in value)
    return False


def checkpoint_run(
    *,
    task_id: str,
    run_id: str,
    worker: str,
    context: dict[str, Any],
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    if not isinstance(context, dict):
        raise TypeError("checkpoint context must be a JSON object")
    if _contains_secret_key(context):
        raise ValueError("checkpoint context contains a secret-like key")

    ts = now_iso()
    checkpoint_id = str(uuid.uuid4())
    payload = json.dumps(context, ensure_ascii=False, sort_keys=True)

    with connect(path) as conn:
        conn.executescript(CHECKPOINT_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task or task["status"] != "claimed" or task["claimed_by"] != worker:
            raise RuntimeError("task is not actively owned by this worker")
        run = conn.execute(
            "SELECT * FROM runs WHERE id=? AND task_id=? AND worker=? AND status='running'",
            (run_id, task_id, worker),
        ).fetchone()
        if not run:
            raise RuntimeError("run is not an active run owned by this worker")

        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM checkpoints WHERE run_id=?",
            (run_id,),
        ).fetchone()
        sequence = int(row["next_sequence"])
        conn.execute(
            "INSERT INTO checkpoints (id, task_id, run_id, sequence, context_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (checkpoint_id, task_id, run_id, sequence, payload, ts),
        )
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, 'run.checkpointed', ?, ?, ?)",
            (
                str(uuid.uuid4()),
                task_id,
                worker,
                json.dumps({"checkpoint_id": checkpoint_id, "run_id": run_id, "sequence": sequence}),
                ts,
            ),
        )

    return {
        "id": checkpoint_id,
        "task_id": task_id,
        "run_id": run_id,
        "sequence": sequence,
        "context": context,
        "created_at": ts,
    }


def latest_checkpoint(*, task_id: str, path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    with connect(path) as conn:
        conn.executescript(CHECKPOINT_SCHEMA)
        row = conn.execute(
            """
            SELECT * FROM checkpoints
            WHERE task_id=?
            ORDER BY created_at DESC, sequence DESC, id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["context"] = json.loads(result.pop("context_json"))
    return result


def list_checkpoints(*, task_id: str, path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    with connect(path) as conn:
        conn.executescript(CHECKPOINT_SCHEMA)
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE task_id=? ORDER BY created_at, sequence, id",
            (task_id,),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["context"] = json.loads(item.pop("context_json"))
        result.append(item)
    return result
