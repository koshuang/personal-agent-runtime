from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from par.db import create_task, get_task, init_db


def test_same_key_returns_same_task_and_single_created_event(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    first = create_task(goal="first", idempotency_key="trigger:123", path=db)
    second = create_task(goal="different payload should dedupe", idempotency_key="trigger:123", path=db)

    assert first["id"] == second["id"]
    assert second["goal"] == "first"
    restored = get_task(first["id"], path=db)
    assert restored is not None
    created = [event for event in restored["events"] if event["type"] == "task.created"]
    assert len(created) == 1


def test_missing_key_preserves_create_new_semantics(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    first = create_task(goal="same", path=db)
    second = create_task(goal="same", path=db)
    assert first["id"] != second["id"]


def test_init_db_migrates_pre_idempotency_schema_without_identity_loss(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE tasks (
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
            CREATE TABLE runs (
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
            CREATE TABLE events (
              id TEXT PRIMARY KEY,
              task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
              type TEXT NOT NULL,
              actor TEXT,
              payload_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE TABLE artifacts (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
              run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
              kind TEXT NOT NULL,
              uri TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO tasks (id, goal, status, mode, created_at, updated_at) VALUES ('legacy-task', 'keep me', 'pending', 'read-only', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
    finally:
        conn.close()

    init_db(db)
    legacy = get_task("legacy-task", path=db)
    assert legacy is not None
    assert legacy["id"] == "legacy-task"
    assert legacy["idempotency_key"] is None

    keyed = create_task(goal="new", idempotency_key="new-key", path=db)
    assert keyed["idempotency_key"] == "new-key"


def test_cli_idempotency_key_deduplicates(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    base = [sys.executable, "-m", "par", "--db", str(db), "task", "create", "--goal", "cli-task", "--idempotency-key", "cli:1"]
    first = subprocess.check_output(base, text=True)
    second = subprocess.check_output(base, text=True)
    import json

    assert json.loads(first)["id"] == json.loads(second)["id"]
