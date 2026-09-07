from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from par.db import claim_task, create_task, fail_task, get_task, init_db, retry_task
from par.reconcile import reconcile
from par.scheduler import decide


def _fail_once(db: Path, task_id: str, worker: str) -> None:
    claimed = claim_task(task_id=task_id, worker=worker, path=db)
    fail_task(
        task_id=task_id,
        run_id=claimed["run_id"],
        worker=worker,
        summary="synthetic failure",
        blockers=["retry-test"],
        path=db,
    )


def test_claim_increments_attempt_and_retry_requeues_within_budget(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="retry me", max_attempts=3, path=db)

    _fail_once(db, task["id"], "worker-1")
    failed = get_task(task["id"], path=db)
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["attempt_count"] == 1
    assert failed["max_attempts"] == 3

    reconciliation = reconcile(path=db)
    failed_finding = next(f for f in reconciliation["findings"] if f["type"] == "failed_work")
    assert failed_finding["retryable"] is True
    assert failed_finding["attempt_count"] == 1
    assert failed_finding["max_attempts"] == 3

    decision = decide(path=db)
    assert decision["decision"] == "retry"
    assert decision["requires_human"] is True
    assert decision["safe_to_auto_execute"] is False

    retried = retry_task(task_id=task["id"], path=db)
    assert retried["status"] == "pending"
    assert retried["attempt_count"] == 1

    claimed_again = claim_task(task_id=task["id"], worker="worker-2", path=db)
    assert claimed_again["attempt_count"] == 2


def test_exhausted_budget_transitions_to_dead_letter(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="fail terminally", max_attempts=1, path=db)

    _fail_once(db, task["id"], "worker-1")
    reconciliation = reconcile(path=db)
    exhausted = next(f for f in reconciliation["findings"] if f["type"] == "retry_budget_exhausted")
    assert exhausted["attempt_count"] == 1
    assert exhausted["max_attempts"] == 1
    assert exhausted["retryable"] is False

    decision = decide(path=db)
    assert decision["decision"] == "dead_letter"
    assert decision["requires_human"] is True
    assert decision["safe_to_auto_execute"] is False

    dead = retry_task(task_id=task["id"], path=db)
    assert dead["status"] == "dead_letter"

    findings = reconcile(path=db)["findings"]
    terminal = next(f for f in findings if f["type"] == "dead_letter")
    assert terminal["automatic_retry"] is False

    with pytest.raises(RuntimeError, match="only failed tasks"):
        retry_task(task_id=task["id"], path=db)
    with pytest.raises(RuntimeError, match="not claimable or retry budget is exhausted"):
        claim_task(task_id=task["id"], worker="worker-2", path=db)


def test_init_db_migrates_retry_columns_without_identity_loss(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE tasks (
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
    migrated = get_task("legacy-task", path=db)
    assert migrated is not None
    assert migrated["id"] == "legacy-task"
    assert migrated["attempt_count"] == 0
    assert migrated["max_attempts"] == 3


def test_retry_cli_transitions_failed_task(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="cli retry", max_attempts=2, path=db)
    _fail_once(db, task["id"], "worker-1")

    raw = subprocess.check_output(
        [sys.executable, "-m", "par", "--db", str(db), "task", "retry", task["id"], "--actor", "operator"],
        text=True,
    )
    result = json.loads(raw)
    assert result["status"] == "pending"
