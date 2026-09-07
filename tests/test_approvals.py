from __future__ import annotations

from pathlib import Path

import pytest

from par.approvals import decide_approval, expire_approval, get_approval, list_approvals, request_approval
from par.db import connect, init_db
from par.portability import export_state, restore_state


def test_request_is_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    kwargs = dict(
        idempotency_key="approval-1",
        subject_type="task",
        subject_id="task-123",
        action="write_repository",
        requested_by="scheduler",
        scope={"repo": "koshuang/personal-agent-runtime", "branch": "main"},
        path=db,
    )
    first = request_approval(**kwargs)
    second = request_approval(**kwargs)
    assert second["id"] == first["id"]
    assert len(list_approvals(path=db)) == 1

    with pytest.raises(ValueError, match="different approval request"):
        request_approval(**{**kwargs, "scope": {"repo": "other"}})


def test_approval_decision_is_explicit_scoped_and_immutable(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    request = request_approval(
        idempotency_key="approval-2",
        subject_type="ingress_event",
        subject_id="evt-1",
        action="materialize_write_task",
        requested_by="human-command",
        scope={"repo": "koshuang/personal-agent-runtime", "mode": "write"},
        path=db,
    )
    approved = decide_approval(
        request["id"],
        decision="approved",
        decided_by="kos",
        reason="Approve only this scoped action",
        path=db,
    )
    assert approved["status"] == "approved"
    assert approved["decided_by"] == "kos"
    assert approved["decision_reason"] == "Approve only this scoped action"
    assert approved["approval_is_scoped_evidence"] is True
    assert approved["approval_is_blanket_authority"] is False
    assert approved["scope"] == {"mode": "write", "repo": "koshuang/personal-agent-runtime"}

    with pytest.raises(ValueError, match="terminal"):
        decide_approval(
            request["id"],
            decision="rejected",
            decided_by="kos",
            reason="try to flip",
            path=db,
        )


def test_rejected_decision_is_terminal(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    request = request_approval(
        idempotency_key="approval-3",
        subject_type="task",
        subject_id="task-3",
        action="use_paid_provider",
        requested_by="scheduler",
        path=db,
    )
    rejected = decide_approval(
        request["id"],
        decision="rejected",
        decided_by="kos",
        reason="Keep zero-cost policy",
        path=db,
    )
    assert rejected["status"] == "rejected"
    with pytest.raises(ValueError, match="terminal"):
        decide_approval(
            request["id"],
            decision="approved",
            decided_by="kos",
            reason="flip",
            path=db,
        )


def test_expired_decision_is_terminal(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    request = request_approval(
        idempotency_key="approval-expired",
        subject_type="task",
        subject_id="task-expired",
        action="write_repository",
        requested_by="scheduler",
        path=db,
    )
    expired = expire_approval(
        request["id"],
        actor="scheduler",
        reason="deadline elapsed",
        path=db,
    )
    assert expired["status"] == "expired"
    assert expired["decided_by"] == "scheduler"
    assert expired["decision_reason"] == "deadline elapsed"
    assert expired["decided_at"] is not None
    with pytest.raises(ValueError, match="terminal"):
        decide_approval(
            request["id"],
            decision="approved",
            decided_by="kos",
            reason="late approval",
            path=db,
        )


def test_invalid_identifiers_and_non_finite_scope_fail_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    with pytest.raises(ValueError):
        request_approval(idempotency_key="", subject_type="task", subject_id="1", action="x", requested_by="y", path=db)
    with pytest.raises(ValueError):
        request_approval(idempotency_key="a", subject_type="", subject_id="1", action="x", requested_by="y", path=db)
    with pytest.raises(ValueError):
        request_approval(idempotency_key="b", subject_type="task", subject_id="", action="x", requested_by="y", path=db)
    with pytest.raises(ValueError):
        request_approval(idempotency_key="c", subject_type="task", subject_id="1", action="", requested_by="y", path=db)
    with pytest.raises(ValueError):
        request_approval(idempotency_key="d", subject_type="task", subject_id="1", action="x", requested_by="", path=db)
    with pytest.raises(ValueError):
        request_approval(idempotency_key="e", subject_type="task", subject_id="1", action="x", requested_by="y", scope=[], path=db)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        request_approval(idempotency_key="f", subject_type="task", subject_id="1", action="x", requested_by="y", scope={"n": float("nan")}, path=db)


def test_approval_state_does_not_create_tasks_or_runs(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    request = request_approval(
        idempotency_key="approval-4",
        subject_type="ingress_event",
        subject_id="evt-4",
        action="production_change",
        requested_by="human",
        scope={"service": "example"},
        path=db,
    )
    decide_approval(request["id"], decision="approved", decided_by="kos", reason="scoped approval", path=db)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_portability_preserves_approval_decision_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    artifact = tmp_path / "state.parstate"
    init_db(source)
    request = request_approval(
        idempotency_key="portable-approval",
        subject_type="task",
        subject_id="task-portable",
        action="write_repository",
        requested_by="scheduler",
        scope={"repo": "koshuang/personal-agent-runtime"},
        path=source,
    )
    decided = decide_approval(
        request["id"],
        decision="approved",
        decided_by="kos",
        reason="portable evidence",
        path=source,
    )
    export_state(source, artifact)
    restore_state(artifact, restored)
    recovered = get_approval(decided["id"], path=restored)
    assert recovered is not None
    assert recovered["id"] == decided["id"]
    assert recovered["idempotency_key"] == "portable-approval"
    assert recovered["status"] == "approved"
    assert recovered["decided_by"] == "kos"
    assert recovered["decision_reason"] == "portable evidence"
    assert recovered["scope"] == {"repo": "koshuang/personal-agent-runtime"}
