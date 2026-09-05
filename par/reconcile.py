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

        if (
            task["status"] == "claimed"
            and task.get("lease_expires_at")
            and task["lease_expires_at"] < now
        ):
            findings.append({
                "type": "stale_lease",
                "task_id": task["id"],
                "action": "resume_or_reclaim",
                "recoverable": True,
            })

        if task["status"] == "failed" or (latest_run and latest_run["status"] == "failed"):
            findings.append({
                "type": "failed_work",
                "task_id": task["id"],
                "action": "classify_retry",
                "automatic_retry": False,
            })

        if task["status"] == "completed":
            evidence = _json_list(latest_run.get("evidence_json") if latest_run else None)
            if not latest_run or latest_run.get("status") != "completed" or not evidence:
                findings.append({
                    "type": "completion_evidence_gap",
                    "task_id": task["id"],
                    "action": "verify",
                })

            if task.get("next_action"):
                active_successor = any(
                    other["id"] != task["id"]
                    and (
                        other["status"] == "pending"
                        or (
                            other["status"] == "claimed"
                            and other.get("lease_expires_at")
                            and other["lease_expires_at"] >= now
                        )
                    )
                    and task["next_action"] in (other.get("goal") or "")
                    for other in tasks
                )
                if not active_successor:
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
