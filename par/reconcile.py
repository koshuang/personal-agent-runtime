from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_obj(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _accepted_independent_review(task_runs: list[dict[str, Any]]) -> bool:
    for run in task_runs:
        metadata = _json_obj(run.get("metadata_json"))
        review = metadata.get("review")
        if metadata.get("role") == "critic" and isinstance(review, dict) and review.get("verdict") == "accepted":
            return True
    return False


def _represents_successor(parent: dict[str, Any], other: dict[str, Any], *, now: str) -> bool:
    if other["id"] == parent["id"]:
        return False
    other_context = _json_obj(other.get("context_json"))
    if other_context.get("parent_task_id") == parent["id"]:
        return True
    active = other["status"] == "pending" or (
        other["status"] == "claimed"
        and other.get("lease_expires_at")
        and other["lease_expires_at"] >= now
    )
    return bool(active and parent.get("next_action") and parent["next_action"] in (other.get("goal") or ""))


def reconcile(*, path: Path = DEFAULT_DB) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    findings: list[dict[str, Any]] = []

    with connect(path) as conn:
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks ORDER BY created_at")]
        runs = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY started_at")]

    runs_by_task: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        runs_by_task.setdefault(run["task_id"], []).append(run)

    for task in tasks:
        task_runs = runs_by_task.get(task["id"], [])
        latest_run = task_runs[-1] if task_runs else None
        context = _json_obj(task.get("context_json"))
        requires_review = bool(context.get("requires_independent_review"))

        if task["status"] == "review_pending":
            findings.append({
                "type": "pending_independent_review",
                "task_id": task["id"],
                "action": "request_independent_review",
                "requires_human": False,
            })

        if task["status"] == "review_rejected":
            findings.append({
                "type": "rejected_independent_review",
                "task_id": task["id"],
                "action": "inspect_review_and_rework",
                "automatic_retry": False,
                "requires_human": True,
            })

        if task["status"] == "dead_letter":
            findings.append({
                "type": "dead_letter",
                "task_id": task["id"],
                "action": "inspect_terminal_failure",
                "attempt_count": task.get("attempt_count", 0),
                "max_attempts": task.get("max_attempts", 0),
                "automatic_retry": False,
                "requires_human": True,
            })

        if task["status"] == "claimed" and task.get("lease_expires_at") and task["lease_expires_at"] < now:
            findings.append({
                "type": "stale_lease",
                "task_id": task["id"],
                "action": "resume_or_reclaim",
                "recoverable": True,
            })

        if task["status"] == "failed" or (latest_run and latest_run["status"] == "failed" and task["status"] != "dead_letter"):
            attempt_count = int(task.get("attempt_count", 0))
            max_attempts = int(task.get("max_attempts", 0))
            retryable = attempt_count < max_attempts
            findings.append({
                "type": "failed_work" if retryable else "retry_budget_exhausted",
                "task_id": task["id"],
                "action": "classify_retry" if retryable else "dead_letter",
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "retryable": retryable,
                "automatic_retry": False,
                "requires_human": True,
            })

        if task["status"] == "completed":
            if requires_review and not _accepted_independent_review(task_runs):
                findings.append({
                    "type": "independent_review_evidence_gap",
                    "task_id": task["id"],
                    "action": "verify_independent_review",
                })

            worker_runs = [r for r in task_runs if _json_obj(r.get("metadata_json")).get("role") != "critic"]
            evidence_run = worker_runs[-1] if worker_runs else latest_run
            evidence = _json_list(evidence_run.get("evidence_json") if evidence_run else None)
            if not evidence_run or evidence_run.get("status") != "completed" or not evidence:
                findings.append({
                    "type": "completion_evidence_gap",
                    "task_id": task["id"],
                    "action": "verify",
                })

            if task.get("next_action"):
                represented_successor = any(_represents_successor(task, other, now=now) for other in tasks)
                if not represented_successor:
                    findings.append({
                        "type": "unmaterialized_next_action",
                        "task_id": task["id"],
                        "next_action": task["next_action"],
                        "action": "materialize",
                    })

        if task["status"] != "claimed":
            for run in task_runs:
                if run["status"] == "running":
                    findings.append({
                        "type": "orphan_running_run",
                        "task_id": task["id"],
                        "run_id": run["id"],
                        "action": "inspect_state_mismatch",
                    })

    return {
        "healthy_idle": len(findings) == 0,
        "finding_count": len(findings),
        "findings": findings,
    }
