from __future__ import annotations

from pathlib import Path

import pytest

from par.db import connect, init_db
from par.github_events import ingest_github_pr_event
from par.materialization import materialize_ingress_event
from par.portability import export_state, restore_state


def _counts(db: Path) -> tuple[int, int]:
    with connect(db) as conn:
        has_ingress = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingress_events'"
        ).fetchone()
        ingress = conn.execute("SELECT COUNT(*) FROM ingress_events").fetchone()[0] if has_ingress else 0
        tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    return ingress, tasks


def test_supported_github_event_normalizes_and_materializes_one_read_only_task(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = ingest_github_pr_event(
        event_name="pull_request_review",
        delivery_id="delivery-1",
        repository="koshuang/personal-agent-runtime",
        pull_request_number=64,
        payload={"action": "submitted"},
        path=db,
    )
    assert event["source"] == "webhook"
    assert event["kind"] == "github.pull_request_review"
    assert event["authority_is_grant"] is False

    result = materialize_ingress_event(event["id"], path=db)
    assert result["decision"] == "materialized"
    assert result["task"]["mode"] == "read-only"
    assert result["task"]["repo"] == "koshuang/personal-agent-runtime"
    assert _counts(db) == (1, 1)


def test_workflow_run_normalizes_to_same_bounded_read_only_contract(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = ingest_github_pr_event(
        event_name="workflow_run",
        delivery_id="delivery-workflow-run",
        repository="koshuang/personal-agent-runtime",
        pull_request_number=78,
        payload={"action": "completed"},
        path=db,
    )
    assert event["kind"] == "github.workflow_run"
    assert event["authority_is_grant"] is False
    result = materialize_ingress_event(event["id"], path=db)
    assert result["decision"] == "materialized"
    assert result["task"]["mode"] == "read-only"
    assert _counts(db) == (1, 1)


def test_replayed_delivery_is_idempotent_for_event_and_task(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    kwargs = dict(
        event_name="pull_request",
        delivery_id="delivery-repeat",
        repository="koshuang/personal-agent-runtime",
        pull_request_number=66,
        payload={"action": "synchronize"},
        path=db,
    )
    first = ingest_github_pr_event(**kwargs)
    second = ingest_github_pr_event(**kwargs)
    assert second["id"] == first["id"]
    first_task = materialize_ingress_event(first["id"], path=db)["task"]
    second_task = materialize_ingress_event(second["id"], path=db)["task"]
    assert second_task["id"] == first_task["id"]
    assert _counts(db) == (1, 1)


def test_conflicting_delivery_replay_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    ingest_github_pr_event(
        event_name="pull_request",
        delivery_id="delivery-conflict",
        repository="koshuang/personal-agent-runtime",
        pull_request_number=10,
        payload={"action": "opened"},
        path=db,
    )
    with pytest.raises(ValueError, match="idempotency_key was reused"):
        ingest_github_pr_event(
            event_name="pull_request",
            delivery_id="delivery-conflict",
            repository="koshuang/personal-agent-runtime",
            pull_request_number=11,
            payload={"action": "opened"},
            path=db,
        )
    assert _counts(db) == (1, 0)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"event_name": "issues"}, "unsupported GitHub event"),
        ({"delivery_id": ""}, "delivery_id"),
        ({"repository": "invalid"}, "owner/name"),
        ({"repository": "owner /repo"}, "whitespace"),
        ({"repository": "owner/ repo"}, "whitespace"),
        ({"pull_request_number": 0}, "positive integer"),
        ({"payload": []}, "payload must be a JSON object"),
        ({"payload": None}, "payload must be a JSON object"),
        ({"payload": {"action": ""}}, "payload.action"),
    ],
)
def test_malformed_or_unsupported_event_fails_closed(tmp_path: Path, kwargs: dict, message: str) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    base = {
        "event_name": "pull_request",
        "delivery_id": "delivery-invalid",
        "repository": "koshuang/personal-agent-runtime",
        "pull_request_number": 1,
        "payload": {"action": "opened"},
        "path": db,
    }
    base.update(kwargs)
    with pytest.raises(ValueError, match=message):
        ingest_github_pr_event(**base)
    assert _counts(db) == (0, 0)


def test_portability_preserves_github_event_and_materialized_task_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    artifact = tmp_path / "state.parstate"
    init_db(source)
    event = ingest_github_pr_event(
        event_name="check_run",
        delivery_id="delivery-portable",
        repository="koshuang/personal-agent-runtime",
        pull_request_number=67,
        payload={"action": "completed"},
        path=source,
    )
    task = materialize_ingress_event(event["id"], path=source)["task"]
    export_state(source, artifact)
    restore_state(artifact, restored)

    replay = ingest_github_pr_event(
        event_name="check_run",
        delivery_id="delivery-portable",
        repository="koshuang/personal-agent-runtime",
        pull_request_number=67,
        payload={"action": "completed"},
        path=restored,
    )
    replay_task = materialize_ingress_event(replay["id"], path=restored)["task"]
    assert replay["id"] == event["id"]
    assert replay_task["id"] == task["id"]
    assert _counts(restored) == (1, 1)
