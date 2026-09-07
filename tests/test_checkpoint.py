from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from par.checkpoint import latest_checkpoint, resume_context, write_checkpoint
from par.db import claim_task, complete_task, create_task, init_db


def test_checkpoint_persists_structured_resume_context(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="resume me", idempotency_key="resume-me", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-1", path=db)

    checkpoint = write_checkpoint(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-1",
        summary="implemented parser",
        completed_steps=["parser"],
        remaining_steps=["tests"],
        evidence=[{"type": "diff", "path": "par/example.py"}],
        blockers=[],
        resume_hint="run focused tests next",
        metadata={"provider": "local"},
        path=db,
    )

    assert checkpoint["completed_steps"] == ["parser"]
    assert checkpoint["remaining_steps"] == ["tests"]
    assert checkpoint["resume_hint"] == "run focused tests next"

    fresh = resume_context(task_id=task["id"], path=db)
    assert fresh["task"]["id"] == task["id"]
    assert fresh["resume_available"] is True
    assert fresh["checkpoint"]["id"] == checkpoint["id"]
    assert fresh["checkpoint"]["evidence"] == [{"type": "diff", "path": "par/example.py"}]


def test_checkpoint_is_append_only_and_latest_wins(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="multi checkpoint", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-1", path=db)

    first = write_checkpoint(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-1",
        summary="first",
        remaining_steps=["second"],
        path=db,
    )
    second = write_checkpoint(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-1",
        summary="second",
        completed_steps=["first"],
        path=db,
    )

    assert first["id"] != second["id"]
    assert latest_checkpoint(task_id=task["id"], path=db)["id"] == second["id"]


def test_checkpoint_rejects_wrong_worker_and_inactive_run(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="ownership", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-1", path=db)

    with pytest.raises(RuntimeError, match="actively owned"):
        write_checkpoint(
            task_id=task["id"],
            run_id=claimed["run_id"],
            worker="worker-2",
            summary="bad",
            path=db,
        )

    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-1",
        summary="done",
        evidence=["test"],
        path=db,
    )
    with pytest.raises(RuntimeError, match="actively owned"):
        write_checkpoint(
            task_id=task["id"],
            run_id=claimed["run_id"],
            worker="worker-1",
            summary="too late",
            path=db,
        )


def test_checkpoint_rejects_expired_lease(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="expired ownership", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-1", path=db)

    from par.db import connect

    with connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (task["id"],),
        )

    with pytest.raises(RuntimeError, match="actively owned"):
        write_checkpoint(
            task_id=task["id"],
            run_id=claimed["run_id"],
            worker="worker-1",
            summary="stale worker must not write",
            path=db,
        )
    assert latest_checkpoint(task_id=task["id"], path=db) is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("completed_steps", "not-a-list", "completed_steps must be a list"),
        ("remaining_steps", {"step": 1}, "remaining_steps must be a list"),
        ("evidence", "not-a-list", "evidence must be a list"),
        ("blockers", {"reason": "x"}, "blockers must be a list"),
        ("metadata", ["not-an-object"], "metadata must be an object"),
    ],
)
def test_checkpoint_rejects_invalid_structured_shapes(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="shape validation", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-1", path=db)
    kwargs = {
        "task_id": task["id"],
        "run_id": claimed["run_id"],
        "worker": "worker-1",
        "summary": "invalid payload",
        "path": db,
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        write_checkpoint(**kwargs)  # type: ignore[arg-type]
    assert latest_checkpoint(task_id=task["id"], path=db) is None


def test_checkpoint_emits_audit_event(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="audit", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-1", path=db)
    checkpoint = write_checkpoint(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-1",
        summary="checkpointed",
        path=db,
    )

    from par.db import connect

    with connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM events WHERE task_id=? AND type='task.checkpointed'",
            (task["id"],),
        ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["checkpoint_id"] == checkpoint["id"]
    assert payload["run_id"] == claimed["run_id"]


def test_checkpoint_cli_round_trip_in_fresh_process(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="cli resume", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-1", path=db)

    checkpoint_cmd = [
        sys.executable,
        "-m",
        "par",
        "--db",
        str(db),
        "task",
        "checkpoint",
        task["id"],
        "--run-id",
        claimed["run_id"],
        "--worker",
        "worker-1",
        "--summary",
        "saved before handoff",
        "--completed-steps",
        '["inspect"]',
        "--remaining-steps",
        '["verify"]',
        "--resume-hint",
        "verify next",
    ]
    subprocess.run(checkpoint_cmd, check=True, capture_output=True, text=True)

    resume_cmd = [sys.executable, "-m", "par", "--db", str(db), "task", "resume", task["id"]]
    output = subprocess.run(resume_cmd, check=True, capture_output=True, text=True)
    payload = json.loads(output.stdout)

    assert payload["task"]["id"] == task["id"]
    assert payload["checkpoint"]["completed_steps"] == ["inspect"]
    assert payload["checkpoint"]["remaining_steps"] == ["verify"]
    assert payload["checkpoint"]["resume_hint"] == "verify next"
