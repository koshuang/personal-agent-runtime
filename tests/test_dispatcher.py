from __future__ import annotations

from pathlib import Path

from par.db import connect, init_db
from par.dispatcher import dispatch_pending_events
from par.ingress import ingest_event
from par.portability import export_state, restore_state


def _task_count(db: Path) -> int:
    with connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]


def _safe_event(db: Path, key: str) -> dict:
    return ingest_event(
        idempotency_key=key,
        source="webhook",
        kind="github.pull_request",
        requested_action={
            "type": "task",
            "action": "inspect_repository",
            "goal": f"Inspect {key}",
            "repo": "koshuang/personal-agent-runtime",
            "mode": "read-only",
            "scope": {"repo": "koshuang/personal-agent-runtime"},
            "context": {"key": key},
            "required_capabilities": ["repo-read"],
            "acceptance_criteria": ["inspection evidence exists"],
            "non_goals": ["no writes"],
            "risk_permission_tier": "read-only",
            "evidence_required": ["persisted task"],
            "expected_next_state_transition": "event -> queued task",
        },
        path=db,
    )


def test_dispatcher_materializes_safe_event_exactly_once(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    _safe_event(db, "safe-1")
    first = dispatch_pending_events(path=db)
    second = dispatch_pending_events(path=db)
    assert first["materialized"] == 1
    assert second["materialized"] == 0
    assert second["already_materialized"] == 1
    assert _task_count(db) == 1


def test_dispatcher_blocks_write_like_event(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    ingest_event(
        idempotency_key="write-1",
        source="human",
        kind="continue",
        requested_action={"type": "task", "mode": "write"},
        path=db,
    )
    result = dispatch_pending_events(path=db)
    assert result["materialized"] == 0
    assert result["blocked"] == 1
    assert _task_count(db) == 0


def test_dispatcher_uses_deterministic_bounded_batch(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    events = [_safe_event(db, f"safe-{index}") for index in range(3)]
    result = dispatch_pending_events(limit=2, path=db)
    assert result["scanned"] == 2
    assert [item["event_id"] for item in result["results"]] == [events[0]["id"], events[1]["id"]]
    assert _task_count(db) == 2


def test_fresh_runtime_recovers_ingested_but_unmaterialized_event(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    artifact = tmp_path / "state.parstate"
    init_db(source)
    event = _safe_event(source, "crash-boundary")
    export_state(source, artifact)
    restore_state(artifact, restored)
    result = dispatch_pending_events(path=restored)
    assert result["materialized"] == 1
    assert result["results"][0]["event_id"] == event["id"]
    assert _task_count(restored) == 1
    again = dispatch_pending_events(path=restored)
    assert again["materialized"] == 0
    assert again["already_materialized"] == 1
