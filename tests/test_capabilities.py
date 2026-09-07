from __future__ import annotations

import pytest

from par.capabilities import (
    claim_task_if_eligible,
    declare_worker_capabilities,
    evaluate_worker_eligibility,
    get_worker_capabilities,
)
from par.db import create_task, get_task, init_db
from par.scheduler import decide


def test_matching_capabilities_allow_claim(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(
        goal="Read repository state",
        mode="read-only",
        context={"required_capabilities": ["repo.read", "filesystem.read"]},
        path=db,
    )
    declare_worker_capabilities(worker="worker-a", capabilities=["filesystem.read", "repo.read", "extra.safe"], path=db)

    claimed = claim_task_if_eligible(task_id=task["id"], worker="worker-a", path=db)

    assert claimed["status"] == "claimed"
    assert claimed["eligibility"]["eligible"] is True
    assert claimed["eligibility"]["missing_capabilities"] == []


def test_missing_or_unknown_worker_fails_closed(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(
        goal="Inspect code",
        mode="read-only",
        context={"required_capabilities": ["repo.read"]},
        path=db,
    )

    unknown = evaluate_worker_eligibility(task=task, worker="unknown", path=db)
    assert unknown["eligible"] is False
    assert unknown["missing_capabilities"] == ["repo.read"]

    with pytest.raises(RuntimeError, match="missing capabilities"):
        claim_task_if_eligible(task_id=task["id"], worker="unknown", path=db)

    assert get_task(task["id"], path=db)["status"] == "pending"


def test_empty_requirements_preserve_existing_behavior(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="No special capability", mode="read-only", path=db)

    eligibility = evaluate_worker_eligibility(task=task, worker="unregistered", path=db)
    assert eligibility["eligible"] is True

    claimed = claim_task_if_eligible(task_id=task["id"], worker="unregistered", path=db)
    assert claimed["status"] == "claimed"


def test_scheduler_exposes_capability_reason_and_preserves_policy_boundary(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(
        goal="Potential write",
        mode="write",
        context={"required_capabilities": ["repo.read"]},
        path=db,
    )
    declare_worker_capabilities(worker="worker-a", capabilities=["repo.read"], path=db)

    eligible = decide(worker="worker-a", path=db)
    assert eligible["decision"] == "execute"
    assert eligible["eligibility"]["eligible"] is True
    assert eligible["eligibility"]["authority_expanded"] is False
    assert eligible["requires_human"] is True
    assert eligible["safe_to_auto_execute"] is False

    db2 = tmp_path / "runtime2.db"
    init_db(db2)
    create_task(
        goal="Needs capability",
        mode="read-only",
        context={"required_capabilities": ["repo.read"]},
        path=db2,
    )
    blocked = decide(worker="worker-b", path=db2)
    assert blocked["decision"] == "await_capable_worker"
    assert blocked["finding_type"] == "worker_capability_gap"
    assert blocked["eligibility"]["missing_capabilities"] == ["repo.read"]
    assert blocked["safe_to_auto_execute"] is False


def test_authority_like_capabilities_are_rejected(tmp_path):
    db = tmp_path / "runtime.db"
    init_db(db)

    for capability in ["credential.aws", "production.deploy", "paid.provider", "secret.read", "admin"]:
        with pytest.raises(ValueError, match="cannot represent authority"):
            declare_worker_capabilities(worker="worker-a", capabilities=[capability], path=db)

    assert get_worker_capabilities(worker="worker-a", path=db) == []
