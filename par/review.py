from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect, now_iso

ROLES = {"orchestrator", "worker", "critic", "auditor"}
SEVERITIES = {"none", "low", "medium", "high", "critical"}
VERDICTS = {"accepted", "rejected"}


def submit_review(
    *,
    task_id: str,
    reviewer: str,
    findings: list[Any] | None = None,
    severity: str = "none",
    evidence_gap: list[Any] | None = None,
    recommended_action: str = "",
    verdict: str,
    provider: str | None = None,
    model: str | None = None,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    if severity not in SEVERITIES:
        raise ValueError(f"invalid severity: {severity}")
    if verdict not in VERDICTS:
        raise ValueError(f"invalid verdict: {verdict}")

    ts = now_iso()
    review_run_id = str(uuid.uuid4())
    review = {
        "findings": findings or [],
        "severity": severity,
        "evidence_gap": evidence_gap or [],
        "recommended_action": recommended_action,
        "verdict": verdict,
    }

    with connect(path) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise KeyError(task_id)
        if task["status"] != "review_pending":
            raise RuntimeError("task is not awaiting independent review")

        worker_run = conn.execute(
            "SELECT * FROM runs WHERE task_id=? AND status='completed' ORDER BY started_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if not worker_run:
            raise RuntimeError("review_pending task has no completed worker run")
        if worker_run["worker"] == reviewer:
            raise RuntimeError("independent reviewer must use a different worker identity")

        metadata = {
            "role": "critic",
            "provider": provider,
            "model": model,
            "cost_usd": 0,
            "review": review,
            "reviewed_run_id": worker_run["id"],
        }
        conn.execute(
            """
            INSERT INTO runs
            (id, task_id, worker, status, summary, evidence_json, blockers_json, metadata_json, started_at, finished_at)
            VALUES (?, ?, ?, 'completed', ?, '[]', '[]', ?, ?, ?)
            """,
            (
                review_run_id,
                task_id,
                reviewer,
                f"Independent critic review: {verdict}",
                json.dumps(metadata, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, 'review.submitted', ?, ?, ?)",
            (str(uuid.uuid4()), task_id, reviewer, json.dumps({"run_id": review_run_id, **review}, ensure_ascii=False), ts),
        )

        next_status = "completed" if verdict == "accepted" else "review_rejected"
        conn.execute(
            "UPDATE tasks SET status=?, claimed_by=NULL, lease_expires_at=NULL, updated_at=? WHERE id=?",
            (next_status, ts, task_id),
        )
        event_type = "task.completed" if verdict == "accepted" else "task.review_rejected"
        conn.execute(
            "INSERT INTO events (id, task_id, type, actor, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                task_id,
                event_type,
                reviewer,
                json.dumps({"review_run_id": review_run_id, "verdict": verdict}, ensure_ascii=False),
                ts,
            ),
        )

    return {
        "task_id": task_id,
        "run_id": review_run_id,
        "role": "critic",
        "reviewer": reviewer,
        "status": next_status,
        "review": review,
    }
