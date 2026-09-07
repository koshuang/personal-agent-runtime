from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, get_task, next_task
from .reconcile import reconcile

_DECISION_PRIORITY = {
    "stale_lease": 0,
    "orphan_running_run": 0,
    "failed_work": 1,
    "retry_budget_exhausted": 1,
    "dead_letter": 1,
    "completion_evidence_gap": 2,
    "unmaterialized_next_action": 3,
}


def _decision_from_finding(finding: dict[str, Any], *, path: Path) -> dict[str, Any]:
    finding_type = str(finding["type"])
    task_id = finding.get("task_id")
    task = get_task(task_id, path=path) if task_id else None

    if finding_type in {"stale_lease", "orphan_running_run"}:
        return {
            "decision": "resume",
            "task_id": task_id,
            "run_id": finding.get("run_id"),
            "reason": "Runtime state indicates interrupted or stale in-progress work that must be reconciled before new work.",
            "finding_type": finding_type,
            "requires_human": False,
            "safe_to_auto_execute": False,
        }

    if finding_type == "failed_work":
        return {
            "decision": "retry",
            "task_id": task_id,
            "run_id": finding.get("run_id"),
            "reason": "Failed work is within its retry budget but Phase 2 does not retry automatically.",
            "finding_type": finding_type,
            "attempt_count": finding.get("attempt_count"),
            "max_attempts": finding.get("max_attempts"),
            "requires_human": True,
            "safe_to_auto_execute": False,
        }

    if finding_type in {"retry_budget_exhausted", "dead_letter"}:
        return {
            "decision": "dead_letter",
            "task_id": task_id,
            "run_id": finding.get("run_id"),
            "reason": "Retry budget is exhausted or the task is already terminal; automatic retry is forbidden.",
            "finding_type": finding_type,
            "attempt_count": finding.get("attempt_count"),
            "max_attempts": finding.get("max_attempts"),
            "requires_human": True,
            "safe_to_auto_execute": False,
        }

    if finding_type == "completion_evidence_gap":
        return {
            "decision": "verify",
            "task_id": task_id,
            "run_id": finding.get("run_id"),
            "reason": "Task is marked completed but objective completion evidence is missing or incomplete.",
            "finding_type": finding_type,
            "requires_human": False,
            "safe_to_auto_execute": False,
        }

    if finding_type == "unmaterialized_next_action":
        return {
            "decision": "materialize_next",
            "task_id": task_id,
            "run_id": finding.get("run_id"),
            "reason": "Completed task has a persisted next_action that is not represented by an active successor task.",
            "finding_type": finding_type,
            "next_action": finding.get("next_action") or (task or {}).get("next_action"),
            "requires_human": False,
            "safe_to_auto_execute": False,
        }

    raise ValueError(f"unsupported scheduler finding type: {finding_type}")


def decide(*, path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Project durable Runtime state into one deterministic, read-only scheduler decision."""

    reconciliation = reconcile(path=path)
    actionable = [
        finding
        for finding in reconciliation["findings"]
        if finding.get("type") in _DECISION_PRIORITY
    ]
    if actionable:
        actionable.sort(
            key=lambda finding: (
                _DECISION_PRIORITY[str(finding["type"])],
                str(finding.get("task_id") or ""),
                str(finding.get("run_id") or ""),
            )
        )
        return _decision_from_finding(actionable[0], path=path)

    task = next_task(path=path)
    if task:
        read_only = task.get("mode") == "read-only"
        return {
            "decision": "execute",
            "task_id": task["id"],
            "run_id": None,
            "reason": "Eligible queued task is available and no higher-priority reconciliation finding exists.",
            "finding_type": None,
            "requires_human": not read_only,
            "safe_to_auto_execute": read_only,
        }

    return {
        "decision": "idle",
        "task_id": None,
        "run_id": None,
        "reason": "No reconciliation finding or eligible queued task requires action.",
        "finding_type": None,
        "requires_human": False,
        "safe_to_auto_execute": False,
    }
