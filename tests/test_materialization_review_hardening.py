from __future__ import annotations

import json
from pathlib import Path

import pytest

from par.approvals import decide_approval, request_approval
from par.db import connect, create_task, init_db
from par.ingress import ingest_event
from par.materialization import materialize_ingress_event


def _request(*, mode: str = "read-only", scope: dict | None = None, context: dict | None = None) -> dict:
    repo = "koshuang/personal-agent-runtime"
    return {
        "type": "task",
        "action": "inspect_repository" if mode == "read-only" else "write_repository",
        "goal": "Inspect repository state" if mode == "read-only" else "Prepare repository write task",
        "repo": repo,
        "mode": mode,
        "scope": scope if scope is not None else {"repo": repo},
        "context": context or {},
        "required_capabilities": ["repo-read"] if mode == "read-only" else ["repo-write"],
        "acceptance_criteria": ["objective evidence exists"],
        "non_goals": ["no production execution"],
        "risk_permission_tier": "low" if mode == "read-only" else "gated-write",
        "evidence_required": ["persisted state"],
        "expected_next_state_transition": "ingress -> queued task",
    }


def _event(db: Path, *, key: str, source: str = "api", request: dict | None = None) -> dict:
    return ingest_event(
        idempotency_key=key,
        source=source,
        kind="continue",
        requested_action=request or _request(),
        path=db,
    )


def test_missing_closure_fields_fail_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    request = _request()
    request.pop("acceptance_criteria")
    event = _event(db, key="missing-closure", request=request)
    with pytest.raises(ValueError, match="acceptance_criteria"):
        materialize_ingress_event(event["id"], path=db)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_approval_scope_matching_is_json_type_aware(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    repo = "koshuang/personal-agent-runtime"
    event = _event(db, key="typed-scope", request=_request(mode="write", scope={"repo": repo, "production": 1}))
    approval = request_approval(
        idempotency_key="typed-approval",
        subject_type="ingress_event",
        subject_id=event["id"],
        action="write_repository",
        requested_by="scheduler",
        scope={"repo": repo, "production": True},
        path=db,
    )
    decide_approval(approval["id"], decision="approved", decided_by="kos", reason="wrong JSON type", path=db)
    assert materialize_ingress_event(event["id"], path=db)["decision"] == "approval_required"


def test_context_cannot_inject_runtime_owned_successor_or_capabilities(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _event(
        db,
        key="context-sanitize",
        request=_request(context={"parent_task_id": "forged", "required_capabilities": [42], "safe": True}),
    )
    task = materialize_ingress_event(event["id"], path=db)["task"]
    context = json.loads(task["context_json"])
    assert "parent_task_id" not in context
    assert context["required_capabilities"] == ["repo-read"]
    assert context["safe"] is True


def test_ingress_idempotency_namespace_collision_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _event(db, key="collision")
    create_task(goal="forged", idempotency_key=f"ingress-event:{event['id']}", path=db)
    with pytest.raises(RuntimeError, match="collided"):
        materialize_ingress_event(event["id"], path=db)


def test_task_created_event_is_attributed_to_ingress_source(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _event(db, key="actor", source="webhook")
    task = materialize_ingress_event(event["id"], path=db)["task"]
    with connect(db) as conn:
        row = conn.execute("SELECT actor FROM events WHERE task_id=? AND type='task.created'", (task["id"],)).fetchone()
    assert row is not None
    assert row["actor"] == "ingress:webhook"


def test_matching_approval_is_not_limited_to_first_thousand_rows(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    repo = "koshuang/personal-agent-runtime"
    event = _event(db, key="many-approvals", request=_request(mode="write", scope={"repo": repo}))
    # Seed enough approved rows that a fixed list(limit=1000) implementation would miss the matching one.
    for index in range(1001):
        approval = request_approval(
            idempotency_key=f"noise-{index}",
            subject_type="ingress_event",
            subject_id=f"other-{index}",
            action="write_repository",
            requested_by="test",
            scope={"repo": repo},
            path=db,
        )
        decide_approval(approval["id"], decision="approved", decided_by="test", reason="noise", path=db)
    approval = request_approval(
        idempotency_key="matching-after-page",
        subject_type="ingress_event",
        subject_id=event["id"],
        action="write_repository",
        requested_by="scheduler",
        scope={"repo": repo},
        path=db,
    )
    decide_approval(approval["id"], decision="approved", decided_by="kos", reason="exact", path=db)
    result = materialize_ingress_event(event["id"], path=db)
    assert result["decision"] == "materialized"
    assert result["approval_id"] == approval["id"]
