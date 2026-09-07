from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect, create_task, get_task
from .scheduler import decide


def state_counts(*, path: Path = DEFAULT_DB) -> dict[str, int]:
    with connect(path) as conn:
        return {
            "tasks": int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
            "runs": int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
            "events": int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
        }


def successor_idempotency_key(parent_task_id: str, next_action: str) -> str:
    digest = hashlib.sha256(next_action.strip().encode("utf-8")).hexdigest()[:24]
    return f"scheduler-next:{parent_task_id}:{digest}"


def advance_once(*, worker: str | None = None, path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Perform at most one scheduler-owned state transition.

    Phase 2 deliberately keeps execution bounded. This function only materializes a
    persisted next_action for a completed read-only task. Task execution, retries,
    review decisions, capability gaps, and write-mode work remain outside this
    automatic transition and therefore fail closed through `decide()`.
    """

    before = state_counts(path=path)
    decision = decide(worker=worker, path=path)
    if decision.get("decision") != "materialize_next":
        return {
            "changed": False,
            "action": decision.get("decision"),
            "decision": decision,
            "counts_before": before,
            "counts_after": before,
        }

    task_id = str(decision.get("task_id") or "")
    parent = get_task(task_id, path=path)
    if not parent:
        raise RuntimeError("scheduler selected a missing parent task")
    if parent.get("mode") != "read-only":
        return {
            "changed": False,
            "action": "await_human",
            "decision": decision,
            "reason": "Automatic successor materialization is limited to read-only tasks.",
            "counts_before": before,
            "counts_after": before,
        }

    next_action = str(parent.get("next_action") or "").strip()
    if not next_action:
        raise RuntimeError("materialize_next decision is missing next_action")

    context: dict[str, Any] = {
        "parent_task_id": task_id,
        "materialized_by": "scheduler.advance",
    }
    parent_context = json.loads(parent.get("context_json") or "{}")
    required_capabilities = parent_context.get("required_capabilities")
    if isinstance(required_capabilities, list) and required_capabilities:
        context["required_capabilities"] = required_capabilities

    successor = create_task(
        goal=next_action,
        repo=parent.get("repo"),
        mode="read-only",
        context=context,
        priority=int(parent.get("priority") or 100),
        idempotency_key=successor_idempotency_key(task_id, next_action),
        path=path,
    )
    after = state_counts(path=path)
    changed = after != before
    return {
        "changed": changed,
        "action": "materialized_next" if changed else "already_materialized",
        "decision": decision,
        "successor_task_id": successor["id"],
        "successor_idempotency_key": successor.get("idempotency_key"),
        "counts_before": before,
        "counts_after": after,
    }
