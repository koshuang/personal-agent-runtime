from __future__ import annotations

from pathlib import Path

from par.advancement import advance_once, state_counts, successor_idempotency_key
from par.db import claim_task, complete_task, create_task, get_task, init_db
from par.scheduler import decide


def _complete(task: dict, *, worker: str, next_action: str | None, path: Path) -> None:
    claimed = claim_task(task_id=task["id"], worker=worker, path=path)
    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker=worker,
        summary="objective read-only fixture completed",
        evidence=["fixture:evidence"],
        next_action=next_action,
        path=path,
    )


def test_advance_materializes_exactly_one_idempotent_successor(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    parent = create_task(goal="step one", mode="read-only", path=db)
    _complete(parent, worker="worker-a", next_action="step two", path=db)

    before = state_counts(path=db)
    result = advance_once(worker="scheduler-a", path=db)

    assert result["changed"] is True
    assert result["action"] == "materialized_next"
    assert result["counts_after"]["tasks"] == before["tasks"] + 1
    assert result["counts_after"]["runs"] == before["runs"]
    assert result["counts_after"]["events"] == before["events"] + 1

    successor = get_task(result["successor_task_id"], path=db)
    assert successor is not None
    assert successor["goal"] == "step two"
    assert successor["idempotency_key"] == successor_idempotency_key(parent["id"], "step two")

    stable = state_counts(path=db)
    second = advance_once(worker="scheduler-a", path=db)
    assert second["changed"] is False
    assert second["action"] == "execute"
    assert state_counts(path=db) == stable


def test_completed_successor_keeps_parent_next_action_materialized_and_reaches_idle(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    parent = create_task(goal="step one", mode="read-only", path=db)
    _complete(parent, worker="worker-a", next_action="step two", path=db)

    materialized = advance_once(worker="scheduler-a", path=db)
    successor = get_task(materialized["successor_task_id"], path=db)
    assert successor is not None
    _complete(successor, worker="worker-b", next_action=None, path=db)

    before = state_counts(path=db)
    idle = advance_once(worker="scheduler-c", path=db)
    assert idle["changed"] is False
    assert idle["action"] == "idle"
    assert idle["counts_before"] == before
    assert idle["counts_after"] == before
    assert state_counts(path=db) == before


def test_write_mode_next_action_is_not_automatically_materialized(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    parent = create_task(goal="write step", mode="write", path=db)
    _complete(parent, worker="worker-a", next_action="another write step", path=db)

    assert decide(path=db)["decision"] == "materialize_next"
    before = state_counts(path=db)
    result = advance_once(worker="scheduler-a", path=db)

    assert result["changed"] is False
    assert result["action"] == "await_human"
    assert state_counts(path=db) == before
