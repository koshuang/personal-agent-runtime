from __future__ import annotations

import json
from pathlib import Path

from par.commands import resolve_command
from par.db import claim_task, complete_task, connect, create_task, fail_task, init_db
from par.portability import export_state, restore_state


def _counts(db: Path) -> tuple[int, int, int]:
    with connect(db) as conn:
        return (
            conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        )


def test_chk_is_deterministic_and_zero_mutation(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    create_task(goal="Inspect durable state", mode="read-only", path=db)
    before = _counts(db)
    first = resolve_command("chk", path=db)
    second = resolve_command("CHK", path=db)
    assert first == second
    assert first["decision"] == "checked"
    assert first["scheduler"]["decision"] == "execute"
    assert first["mutated"] is False
    assert _counts(db) == before


def test_continue_projects_existing_durable_work_without_mutation(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="Continue persisted work", mode="read-only", path=db)
    before = _counts(db)
    result = resolve_command("continue", path=db)
    assert result["decision"] == "next_action_available"
    assert result["next"]["decision"] == "execute"
    assert result["next"]["task_id"] == task["id"]
    assert result["mutated"] is False
    assert _counts(db) == before


def test_continue_idle_does_not_manufacture_backlog(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    before = _counts(db)
    result = resolve_command("continue", path=db)
    assert result["decision"] == "idle"
    assert result["scheduler"]["decision"] == "idle"
    assert _counts(db) == before


def test_fix_without_explicit_durable_target_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="Fail me", mode="write", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker", path=db)
    fail_task(task_id=task["id"], run_id=claimed["run_id"], worker="worker", summary="failed", path=db)
    before = _counts(db)
    result = resolve_command("fix", path=db)
    assert result["decision"] == "needs_input"
    assert result["mutated"] is False
    assert _counts(db) == before


def test_fix_with_exact_finding_only_proposes_existing_gated_action(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="Write-like task", mode="write", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker", path=db)
    fail_task(task_id=task["id"], run_id=claimed["run_id"], worker="worker", summary="failed", path=db)
    before = _counts(db)
    result = resolve_command("fix", finding_type="failed_work", task_id=task["id"], path=db)
    assert result["decision"] == "fix_proposed"
    assert result["finding"]["task_id"] == task["id"]
    assert result["proposed_action"] == "classify_retry"
    assert result["requires_existing_gates"] is True
    assert result["mutated"] is False
    assert _counts(db) == before


def test_fix_target_not_found_does_not_infer_from_command_wording(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    result = resolve_command("fix", finding_type="failed_work", task_id="missing", path=db)
    assert result["decision"] == "target_not_found"
    assert _counts(db) == (0, 0, 0)


def test_continue_uses_persisted_next_action_after_fresh_restore(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    artifact = tmp_path / "state.parstate"
    init_db(source)
    task = create_task(goal="Parent", mode="read-only", path=source)
    claimed = claim_task(task_id=task["id"], worker="worker", path=source)
    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker",
        summary="done",
        evidence=[{"kind": "test"}],
        next_action="Inspect successor",
        path=source,
    )
    export_state(source, artifact)
    restore_state(artifact, restored)
    before = _counts(restored)
    result = resolve_command("continue", path=restored)
    assert result["decision"] == "next_action_available"
    assert result["next"]["decision"] == "materialize_next"
    assert result["next"]["task_id"] == task["id"]
    assert result["next"]["next_action"] == "Inspect successor"
    assert _counts(restored) == before
