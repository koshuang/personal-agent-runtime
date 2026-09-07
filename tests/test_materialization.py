from __future__ import annotations

from pathlib import Path

import pytest

from par.approvals import decide_approval, expire_approval, request_approval
from par.db import connect, init_db
from par.ingress import ingest_event
from par.materialization import materialize_ingress_event
from par.portability import export_state, restore_state


def _event(db: Path, *, key: str, mode: str = "read-only", scope: dict | None = None, authority: dict | None = None):
    return ingest_event(
        idempotency_key=key,
        source="human",
        kind="continue",
        requested_action={
            "type": "task",
            "action": "inspect_repository" if mode == "read-only" else "write_repository",
            "goal": "Inspect repository state" if mode == "read-only" else "Prepare repository write task",
            "repo": "koshuang/personal-agent-runtime",
            "mode": mode,
            "scope": scope or {"repo": "koshuang/personal-agent-runtime"},
            "context": {"requested_by_test": True},
            "required_capabilities": ["repo-read"] if mode == "read-only" else ["repo-write"],
            "acceptance_criteria": ["materialization decision is deterministic"],
            "non_goals": ["execute the materialized task"],
            "risk_permission_tier": "low" if mode == "read-only" else "gated-write",
            "evidence_required": ["persisted task or fail-closed decision"],
            "expected_next_state_transition": "ingress event -> queued task" if mode == "read-only" else "approved ingress event -> queued task",
        },
        authority=authority or {},
        path=db,
    )


def test_read_only_event_materializes_exactly_one_task(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _event(db, key="read-only")

    first = materialize_ingress_event(event["id"], path=db)
    second = materialize_ingress_event(event["id"], path=db)

    assert first["decision"] == "materialized"
    assert second["task"]["id"] == first["task"]["id"]
    assert first["task"]["mode"] == "read-only"
    assert first["task"]["idempotency_key"] == f"ingress-event:{event['id']}"
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_materialized_task_preserves_source_and_capabilities(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _event(db, key="context")
    result = materialize_ingress_event(event["id"], path=db)
    task = result["task"]
    import json

    context = json.loads(task["context_json"])
    assert context["source_ingress_event_id"] == event["id"]
    assert context["ingress_source"] == "human"
    assert context["ingress_kind"] == "continue"
    assert context["materialization_action"] == "inspect_repository"
    assert context["required_capabilities"] == ["repo-read"]


def test_write_event_without_approval_fails_closed_even_with_authority_claim(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _event(
        db,
        key="write-no-approval",
        mode="write",
        authority={"claims_write": True, "production": True, "credential": "present"},
    )
    result = materialize_ingress_event(event["id"], path=db)
    assert result["decision"] == "approval_required"
    assert result["authority_is_grant"] is False
    assert result["task"] is None
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("terminal", ["pending", "rejected", "expired"])
def test_non_approved_approval_states_do_not_pass_gate(tmp_path: Path, terminal: str) -> None:
    db = tmp_path / f"{terminal}.db"
    init_db(db)
    event = _event(db, key=f"write-{terminal}", mode="write")
    approval = request_approval(
        idempotency_key=f"approval-{terminal}",
        subject_type="ingress_event",
        subject_id=event["id"],
        action="write_repository",
        requested_by="scheduler",
        scope={"repo": "koshuang/personal-agent-runtime"},
        path=db,
    )
    if terminal == "rejected":
        decide_approval(approval["id"], decision="rejected", decided_by="kos", reason="reject", path=db)
    elif terminal == "expired":
        expire_approval(approval["id"], actor="scheduler", reason="expired", path=db)

    result = materialize_ingress_event(event["id"], path=db)
    assert result["decision"] == "approval_required"
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_approved_evidence_requires_exact_subject_action_and_scope(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = _event(db, key="write-match", mode="write")

    wrong_scope = request_approval(
        idempotency_key="wrong-scope",
        subject_type="ingress_event",
        subject_id=event["id"],
        action="write_repository",
        requested_by="scheduler",
        scope={"repo": "other"},
        path=db,
    )
    decide_approval(wrong_scope["id"], decision="approved", decided_by="kos", reason="wrong scope", path=db)
    assert materialize_ingress_event(event["id"], path=db)["decision"] == "approval_required"

    wrong_action = request_approval(
        idempotency_key="wrong-action",
        subject_type="ingress_event",
        subject_id=event["id"],
        action="different_action",
        requested_by="scheduler",
        scope={"repo": "koshuang/personal-agent-runtime"},
        path=db,
    )
    decide_approval(wrong_action["id"], decision="approved", decided_by="kos", reason="wrong action", path=db)
    assert materialize_ingress_event(event["id"], path=db)["decision"] == "approval_required"

    exact = request_approval(
        idempotency_key="exact",
        subject_type="ingress_event",
        subject_id=event["id"],
        action="write_repository",
        requested_by="scheduler",
        scope={"repo": "koshuang/personal-agent-runtime"},
        path=db,
    )
    exact = decide_approval(exact["id"], decision="approved", decided_by="kos", reason="exact scope", path=db)
    result = materialize_ingress_event(event["id"], path=db)
    assert result["decision"] == "materialized"
    assert result["approval_id"] == exact["id"]
    assert result["task"]["mode"] == "write"
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_invalid_task_shaped_requested_action_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = ingest_event(
        idempotency_key="bad",
        source="api",
        kind="continue",
        requested_action={"command": "Continue"},
        path=db,
    )
    with pytest.raises(ValueError, match="type must be task"):
        materialize_ingress_event(event["id"], path=db)


def test_portability_preserves_materialization_outcome(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    artifact = tmp_path / "state.parstate"
    init_db(source)
    event = _event(source, key="portable", mode="write")
    approval = request_approval(
        idempotency_key="portable-approval",
        subject_type="ingress_event",
        subject_id=event["id"],
        action="write_repository",
        requested_by="scheduler",
        scope={"repo": "koshuang/personal-agent-runtime"},
        path=source,
    )
    decide_approval(approval["id"], decision="approved", decided_by="kos", reason="portable", path=source)
    export_state(source, artifact)
    restore_state(artifact, restored)

    result = materialize_ingress_event(event["id"], path=restored)
    assert result["decision"] == "materialized"
    assert result["event_id"] == event["id"]
    assert result["task"]["idempotency_key"] == f"ingress-event:{event['id']}"
