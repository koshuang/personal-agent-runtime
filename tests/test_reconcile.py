from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from par.db import claim_task, complete_task, create_task, init_db
from par.reconcile import reconcile


def test_reconcile_reports_stale_lease(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="stale", path=db)
    claim_task(task_id=task["id"], worker="codex", lease_minutes=1, path=db)

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET lease_expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), task["id"]),
        )

    result = reconcile(path=db)
    assert any(f["type"] == "stale_lease" and f["recoverable"] for f in result["findings"])
    assert result["healthy_idle"] is False


def test_reconcile_reports_completion_evidence_and_next_action_gaps(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="pilot", path=db)
    claimed = claim_task(task_id=task["id"], worker="codex", path=db)
    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="codex",
        summary="done",
        evidence=[],
        next_action="verify in fresh session",
        path=db,
    )

    result = reconcile(path=db)
    types = {f["type"] for f in result["findings"]}
    assert "completion_evidence_gap" in types
    assert "unmaterialized_next_action" in types


def test_reconcile_reports_older_orphan_run_after_reclaim_completes(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="recover stale work", path=db)
    first = claim_task(task_id=task["id"], worker="codex-1", lease_minutes=1, path=db)

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET lease_expires_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), task["id"]),
        )

    second = claim_task(task_id=task["id"], worker="codex-2", path=db)
    complete_task(
        task_id=task["id"],
        run_id=second["run_id"],
        worker="codex-2",
        summary="recovered",
        evidence=[{"type": "test", "result": "pass"}],
        path=db,
    )

    result = reconcile(path=db)
    assert any(
        finding["type"] == "orphan_running_run" and finding["run_id"] == first["run_id"]
        for finding in result["findings"]
    )
    assert result["healthy_idle"] is False


def test_reconcile_healthy_idle_when_completed_has_evidence_and_no_next_action(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="verified", path=db)
    claimed = claim_task(task_id=task["id"], worker="codex", path=db)
    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="codex",
        summary="done",
        evidence=[{"type": "test", "result": "pass"}],
        path=db,
    )

    result = reconcile(path=db)
    assert result == {"healthy_idle": True, "finding_count": 0, "findings": []}
