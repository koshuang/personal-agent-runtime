from __future__ import annotations

from pathlib import Path

from par.db import connect, create_task, init_db
from par.dispatcher import dispatch_event, dispatch_pending_events
from par.ingress import ingest_event
from par.materialization import materialize_ingress_event
from par.portability import export_state, restore_state


def _task_count(db: Path) -> int:
    with connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]


def _status(db: Path, event_id: str) -> str:
    with connect(db) as conn:
        return conn.execute(
            "SELECT status FROM ingress_events WHERE id=?",
            (event_id,),
        ).fetchone()[0]


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
    event = _safe_event(db, "safe-1")
    first = dispatch_pending_events(path=db)
    second = dispatch_pending_events(path=db)
    assert first["materialized"] == 1
    assert second["scanned"] == 0
    assert second["materialized"] == 0
    assert second["already_materialized"] == 0
    assert _status(db, event["id"]) == "materialized"
    assert _task_count(db) == 1


def test_dispatcher_reports_reused_matching_task_as_already_materialized(
    tmp_path: Path,
) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _safe_event(db, "preexisting")
    materialized = materialize_ingress_event(event["id"], path=db)
    assert materialized["decision"] == "materialized"
    assert _status(db, event["id"]) == "received"

    result = dispatch_event(event["id"], path=db)

    assert result["decision"] == "already_materialized"
    assert result["task_created"] is False
    assert result["task_id"] == materialized["task"]["id"]
    assert _status(db, event["id"]) == "materialized"
    assert _task_count(db) == 1


def test_dispatcher_rejects_colliding_preexisting_task(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _safe_event(db, "collision")
    create_task(
        goal="unrelated task",
        idempotency_key=f"ingress-event:{event['id']}",
        path=db,
    )

    result = dispatch_event(event["id"], path=db)

    assert result["decision"] == "invalid"
    assert "collided" in result["reason"]
    assert result["task_created"] is False
    assert _status(db, event["id"]) == "invalid_auto_dispatch"
    assert _task_count(db) == 1


def test_dispatcher_blocks_write_like_event_once_without_starving_following_safe_work(
    tmp_path: Path,
) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    blocked_event = ingest_event(
        idempotency_key="write-1",
        source="human",
        kind="continue",
        requested_action={"type": "task", "mode": "write"},
        path=db,
    )
    safe_event = _safe_event(db, "safe-after-blocked")

    first = dispatch_pending_events(limit=1, path=db)
    second = dispatch_pending_events(limit=1, path=db)
    third = dispatch_pending_events(limit=1, path=db)

    assert first["blocked"] == 1
    assert _status(db, blocked_event["id"]) == "blocked_auto_dispatch"
    assert second["materialized"] == 1
    assert second["results"][0]["event_id"] == safe_event["id"]
    assert third["scanned"] == 0
    assert _task_count(db) == 1


def test_dispatcher_uses_deterministic_bounded_batch(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    events = [_safe_event(db, f"safe-{index}") for index in range(3)]
    result = dispatch_pending_events(limit=2, path=db)
    assert result["scanned"] == 2
    assert [item["event_id"] for item in result["results"]] == [
        events[0]["id"],
        events[1]["id"],
    ]
    assert _task_count(db) == 2
    follow_up = dispatch_pending_events(limit=2, path=db)
    assert follow_up["scanned"] == 1
    assert follow_up["results"][0]["event_id"] == events[2]["id"]
    assert _task_count(db) == 3


def test_fresh_runtime_recovers_ingested_but_unmaterialized_event(
    tmp_path: Path,
) -> None:
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
    assert again["scanned"] == 0
    assert again["materialized"] == 0
    assert _status(restored, event["id"]) == "materialized"
