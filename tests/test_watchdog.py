from __future__ import annotations

from pathlib import Path

from par.db import claim_task, connect, create_task, fail_task, init_db
from par.ingress import ingest_event
from par.portability import export_state, restore_state
from par.watchdog import watchdog_wake


def _safe_event(db: Path, key: str) -> dict:
    return ingest_event(
        idempotency_key=key,
        source="webhook",
        kind="github.pull_request",
        requested_action={
            "type": "task", "action": "inspect_repository", "goal": f"Inspect {key}",
            "repo": "koshuang/personal-agent-runtime", "mode": "read-only",
            "scope": {"repo": "koshuang/personal-agent-runtime"}, "context": {"key": key},
            "required_capabilities": ["repo-read"], "acceptance_criteria": ["inspection evidence exists"],
            "non_goals": ["no writes"], "risk_permission_tier": "read-only",
            "evidence_required": ["persisted task"], "expected_next_state_transition": "event -> queued task",
        }, path=db,
    )


def _counts(db: Path) -> tuple[int, int, int]:
    with connect(db) as conn:
        return (conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])


def test_watchdog_dispatches_missed_event_and_scheduler_sees_task_same_wake(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"; init_db(db); event = _safe_event(db, "missed")
    result = watchdog_wake(dispatch_limit=10, path=db)
    assert result["decision"] == "next_action_available"
    assert result["dispatch"]["materialized"] == 1
    assert result["dispatch"]["results"][0]["event_id"] == event["id"]
    assert result["scheduler"]["decision"] == "await_capable_worker"
    assert result["target_system_read_only"] is True
    assert result["runtime_state_mutated"] is True
    assert _counts(db) == (1, 0, 1)


def test_watchdog_projects_reconciliation_without_ingress(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"; init_db(db)
    task = create_task(goal="retry me", mode="read-only", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker", path=db)
    fail_task(task_id=task["id"], run_id=claimed["run_id"], worker="worker", summary="failed", path=db)
    before = _counts(db); result = watchdog_wake(path=db)
    assert result["dispatch"]["scanned"] == 0
    assert result["reconciliation"]["findings"]
    assert result["scheduler"]["decision"] == "retry"
    assert result["runtime_state_mutated"] is False
    assert _counts(db) == before


def test_repeated_idle_watchdog_is_zero_noise(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"; init_db(db); before = _counts(db)
    first = watchdog_wake(path=db); second = watchdog_wake(path=db)
    assert first["decision"] == "idle"; assert second == first; assert first["runtime_state_mutated"] is False
    assert _counts(db) == before


def test_watchdog_blocks_write_like_event_without_execution(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"; init_db(db)
    ingest_event(idempotency_key="write-like", source="human", kind="continue", requested_action={"type": "task", "mode": "write"}, path=db)
    result = watchdog_wake(path=db)
    assert result["dispatch"]["blocked"] == 1
    assert result["scheduler"]["decision"] == "idle"
    assert result["runtime_state_mutated"] is True
    assert _counts(db) == (0, 0, 0)


def test_watchdog_limit_is_bounded_and_deterministic(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"; init_db(db); events = [_safe_event(db, f"event-{index}") for index in range(3)]
    first = watchdog_wake(dispatch_limit=2, path=db)
    assert first["dispatch"]["scanned"] == 2; assert first["dispatch"]["has_more"] is True
    assert [item["event_id"] for item in first["dispatch"]["results"]] == [events[0]["id"], events[1]["id"]]
    second = watchdog_wake(dispatch_limit=2, path=db)
    assert second["dispatch"]["scanned"] == 1; assert second["dispatch"]["has_more"] is False
    assert second["dispatch"]["results"][0]["event_id"] == events[2]["id"]


def test_watchdog_does_not_report_idle_while_bounded_backlog_remains(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"; init_db(db)
    ingest_event(idempotency_key="blocked-first", source="human", kind="continue", requested_action={"type": "task", "mode": "write"}, path=db)
    _safe_event(db, "safe-second")
    result = watchdog_wake(dispatch_limit=1, path=db)
    assert result["dispatch"]["blocked"] == 1
    assert result["dispatch"]["has_more"] is True
    assert result["scheduler"]["decision"] == "idle"
    assert result["decision"] == "next_action_available"


def test_fresh_restore_watchdog_recovers_missed_ingress(tmp_path: Path) -> None:
    source = tmp_path / "source.db"; restored = tmp_path / "restored.db"; artifact = tmp_path / "state.parstate"
    init_db(source); event = _safe_event(source, "portable-missed"); export_state(source, artifact); restore_state(artifact, restored)
    result = watchdog_wake(path=restored)
    assert result["dispatch"]["materialized"] == 1
    assert result["dispatch"]["results"][0]["event_id"] == event["id"]
    assert result["scheduler"]["decision"] == "await_capable_worker"
