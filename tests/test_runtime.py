from pathlib import Path

import pytest

from par.db import claim_task, complete_task, create_task, get_task, init_db, next_task


def test_task_can_cross_session_boundary(tmp_path: Path):
    db = tmp_path / "runtime.db"
    init_db(db)

    created = create_task(
        goal="Inspect avtime-backend repository state",
        repo="imhere-tw/avtime-backend",
        mode="read-only",
        path=db,
    )

    discovered = next_task(path=db)
    assert discovered is not None
    assert discovered["id"] == created["id"]

    claimed = claim_task(task_id=created["id"], worker="codex", path=db)
    run_id = claimed["run_id"]

    complete_task(
        task_id=created["id"],
        run_id=run_id,
        worker="codex",
        summary="No write performed; repository state inspected.",
        evidence=["https://github.com/imhere-tw/avtime-backend/issues/137"],
        blockers=[],
        next_action="Open a fresh session and read shared state only.",
        path=db,
    )

    # Simulates a new agent/session: it receives only the persistent database.
    restored = get_task(created["id"], path=db)
    assert restored is not None
    assert restored["status"] == "completed"
    assert restored["next_action"] == "Open a fresh session and read shared state only."
    assert restored["runs"][0]["status"] == "completed"


def test_claim_is_exclusive(tmp_path: Path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="Read-only check", path=db)
    claim_task(task_id=task["id"], worker="claude", path=db)

    with pytest.raises(RuntimeError, match="not claimable"):
        claim_task(task_id=task["id"], worker="codex", path=db)
