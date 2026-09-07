from __future__ import annotations

from pathlib import Path

import pytest

from par.db import init_db
from par.ingress import ingest_event
from par.materialization import materialize_ingress_event


def _closure() -> dict:
    return {
        "acceptance_criteria": ["scope validation is deterministic"],
        "non_goals": ["execute write side effects"],
        "risk_permission_tier": "gated-write",
        "evidence_required": ["fail-closed materialization decision"],
        "expected_next_state_transition": "invalid ingress -> no task",
    }


def test_non_read_only_materialization_rejects_empty_scope(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = ingest_event(
        idempotency_key="empty-scope",
        source="human",
        kind="continue",
        requested_action={
            "type": "task",
            "action": "write_repository",
            "goal": "Prepare write task",
            "repo": "koshuang/personal-agent-runtime",
            "mode": "write",
            "scope": {},
            **_closure(),
        },
        path=db,
    )
    with pytest.raises(ValueError, match="scope must be non-empty"):
        materialize_ingress_event(event["id"], path=db)


def test_non_read_only_repo_scope_must_match_requested_repo(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    event = ingest_event(
        idempotency_key="wrong-repo-scope",
        source="human",
        kind="continue",
        requested_action={
            "type": "task",
            "action": "write_repository",
            "goal": "Prepare write task",
            "repo": "koshuang/personal-agent-runtime",
            "mode": "write",
            "scope": {"repo": "other/repository"},
            **_closure(),
        },
        path=db,
    )
    with pytest.raises(ValueError, match="scope.repo must exactly match"):
        materialize_ingress_event(event["id"], path=db)
