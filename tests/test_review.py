from __future__ import annotations

import json
from pathlib import Path

import pytest

from par.db import claim_task, complete_task, create_task, get_task, init_db
from par.reconcile import reconcile
from par.review import submit_review


def _review_task(db: Path) -> tuple[str, str]:
    task = create_task(
        goal="synthetic high-value task",
        mode="read-only",
        context={"requires_independent_review": True},
        path=db,
    )
    claimed = claim_task(
        task_id=task["id"],
        worker="worker-a",
        role="worker",
        provider="subscription",
        model="model-a",
        path=db,
    )
    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        summary="implementation done",
        evidence=["deterministic:test-pass"],
        metadata={"cost_usd": 0},
        path=db,
    )
    return task["id"], claimed["run_id"]


def test_worker_requires_distinct_critic_before_closure(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task_id, worker_run_id = _review_task(db)

    pending = get_task(task_id, path=db)
    assert pending is not None
    assert pending["status"] == "review_pending"
    worker_metadata = json.loads(pending["runs"][0]["metadata_json"])
    assert worker_metadata["role"] == "worker"
    assert worker_metadata["provider"] == "subscription"
    assert worker_metadata["model"] == "model-a"

    with pytest.raises(RuntimeError, match="different worker identity"):
        submit_review(task_id=task_id, reviewer="worker-a", verdict="accepted", path=db)

    review = submit_review(
        task_id=task_id,
        reviewer="critic-b",
        findings=[],
        severity="none",
        evidence_gap=[],
        recommended_action="close",
        verdict="accepted",
        provider="subscription",
        model="model-b",
        path=db,
    )
    assert review["status"] == "completed"
    assert review["run_id"] != worker_run_id
    assert review["review"] == {
        "findings": [],
        "severity": "none",
        "evidence_gap": [],
        "recommended_action": "close",
        "verdict": "accepted",
    }

    completed = get_task(task_id, path=db)
    assert completed is not None
    assert completed["status"] == "completed"
    assert len(completed["runs"]) == 2
    critic_metadata = json.loads(completed["runs"][1]["metadata_json"])
    assert critic_metadata["role"] == "critic"
    assert critic_metadata["reviewed_run_id"] == worker_run_id
    assert critic_metadata["review"]["verdict"] == "accepted"
    assert critic_metadata["cost_usd"] == 0

    types = {event["type"] for event in completed["events"]}
    assert "task.review_requested" in types
    assert "review.submitted" in types
    assert "task.completed" in types

    findings = reconcile(path=db)["findings"]
    assert not any(f["type"] in {"pending_independent_review", "independent_review_evidence_gap"} for f in findings)


def test_reconcile_surfaces_pending_review(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task_id, _ = _review_task(db)

    findings = reconcile(path=db)["findings"]
    pending = [f for f in findings if f["type"] == "pending_independent_review"]
    assert pending == [{
        "type": "pending_independent_review",
        "task_id": task_id,
        "action": "request_independent_review",
        "requires_human": False,
    }]


def test_rejected_review_is_not_auto_retried(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task_id, _ = _review_task(db)

    review = submit_review(
        task_id=task_id,
        reviewer="critic-b",
        findings=["missing acceptance evidence"],
        severity="high",
        evidence_gap=["acceptance-check"],
        recommended_action="rework and resubmit",
        verdict="rejected",
        path=db,
    )
    assert review["status"] == "review_rejected"

    findings = reconcile(path=db)["findings"]
    rejected = [f for f in findings if f["type"] == "rejected_independent_review"]
    assert rejected == [{
        "type": "rejected_independent_review",
        "task_id": task_id,
        "action": "inspect_review_and_rework",
        "automatic_retry": False,
        "requires_human": True,
    }]


def test_non_review_task_preserves_phase1_completion(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="ordinary read-only task", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=db)
    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        summary="done",
        evidence=["ok"],
        path=db,
    )
    result = get_task(task["id"], path=db)
    assert result is not None
    assert result["status"] == "completed"
