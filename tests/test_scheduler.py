from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from par.db import claim_task, complete_task, create_task, fail_task, get_task, init_db
from par.scheduler import decide


def _complete(
    db: Path,
    *,
    goal: str,
    evidence: list[str],
    next_action: str | None = None,
) -> dict[str, object]:
    task = create_task(goal=goal, path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=db)
    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        summary="done",
        evidence=evidence,
        next_action=next_action,
        path=db,
    )
    recovered = get_task(task["id"], path=db)
    assert recovered is not None
    return recovered


def test_scheduler_decides_resume_for_stale_lease_without_mutating_state(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="resume me", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )
    before = get_task(task["id"], path=db)

    decision = decide(path=db)

    after = get_task(task["id"], path=db)
    assert decision["decision"] == "resume"
    assert decision["task_id"] == task["id"]
    assert decision["finding_type"] == "stale_lease"
    assert decision["requires_human"] is False
    assert decision["safe_to_auto_execute"] is False
    assert before == after
    assert claimed["run_id"] == after["runs"][-1]["id"]


def test_scheduler_decides_retry_but_requires_human_for_failed_work(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="failed work", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=db)
    fail_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        summary="failed",
        blockers=["needs classification"],
        path=db,
    )

    decision = decide(path=db)

    assert decision["decision"] == "retry"
    assert decision["task_id"] == task["id"]
    assert decision["finding_type"] == "failed_work"
    assert decision["requires_human"] is True
    assert decision["safe_to_auto_execute"] is False


def test_scheduler_decides_verify_for_completion_evidence_gap(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = _complete(db, goal="missing evidence", evidence=[])

    decision = decide(path=db)

    assert decision["decision"] == "verify"
    assert decision["task_id"] == task["id"]
    assert decision["finding_type"] == "completion_evidence_gap"


def test_scheduler_decides_materialize_next_after_verified_completion(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = _complete(
        db,
        goal="completed",
        evidence=["check:ok"],
        next_action="Create successor task for docs",
    )

    decision = decide(path=db)

    assert decision["decision"] == "materialize_next"
    assert decision["task_id"] == task["id"]
    assert decision["finding_type"] == "unmaterialized_next_action"
    assert decision["next_action"] == "Create successor task for docs"
    assert decision["safe_to_auto_execute"] is False


def test_scheduler_decides_execute_for_pending_read_only_task(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="inspect", mode="read-only", path=db)

    decision = decide(path=db)

    assert decision == {
        "decision": "execute",
        "task_id": task["id"],
        "run_id": None,
        "reason": "Eligible queued task is available and no higher-priority reconciliation finding exists.",
        "finding_type": None,
        "requires_human": False,
        "safe_to_auto_execute": True,
    }
    recovered = get_task(task["id"], path=db)
    assert recovered is not None
    assert recovered["status"] == "pending"
    assert recovered["runs"] == []


def test_scheduler_decides_idle_for_healthy_empty_runtime(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)

    decision = decide(path=db)

    assert decision["decision"] == "idle"
    assert decision["task_id"] is None
    assert decision["finding_type"] is None
    assert decision["requires_human"] is False
    assert decision["safe_to_auto_execute"] is False


def test_scheduler_prioritizes_reconciliation_over_pending_task_deterministically(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    pending = create_task(goal="pending", path=db)
    failed = create_task(goal="failed", path=db)
    claimed = claim_task(task_id=failed["id"], worker="worker-a", path=db)
    fail_task(
        task_id=failed["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        summary="failed",
        path=db,
    )

    first = decide(path=db)
    second = decide(path=db)

    assert first == second
    assert first["decision"] == "retry"
    assert first["task_id"] == failed["id"]
    assert first["task_id"] != pending["id"]
