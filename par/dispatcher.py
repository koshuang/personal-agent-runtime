from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect
from .ingress import get_event, list_events
from .materialization import materialize_ingress_event


def _already_materialized(event_id: str, *, path: Path) -> bool:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM tasks WHERE idempotency_key=? LIMIT 1",
            (f"ingress-event:{event_id}",),
        ).fetchone()
    return row is not None


def _dispatchable(event: dict[str, Any]) -> tuple[bool, str]:
    requested = event.get("requested_action")
    if not isinstance(requested, dict):
        return False, "requested_action must be a JSON object"
    if requested.get("type") != "task":
        return False, "requested_action.type must be task"
    if requested.get("mode") != "read-only":
        return False, "automatic dispatch is limited to read-only task materialization"
    return True, "safe read-only task request"


def dispatch_event(event_id: str, *, path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Materialize one eligible ingress event without claiming or executing its task."""
    event = get_event(event_id, path=path)
    if event is None:
        raise KeyError(f"ingress event not found: {event_id}")
    if _already_materialized(event_id, path=path):
        return {"event_id": event_id, "decision": "already_materialized", "task_created": False}

    eligible, reason = _dispatchable(event)
    if not eligible:
        return {
            "event_id": event_id,
            "decision": "blocked",
            "reason": reason,
            "task_created": False,
        }

    try:
        result = materialize_ingress_event(event_id, path=path)
    except (ValueError, RuntimeError) as exc:
        return {
            "event_id": event_id,
            "decision": "invalid",
            "reason": str(exc),
            "task_created": False,
        }
    if result.get("decision") != "materialized" or not result.get("task"):
        return {
            "event_id": event_id,
            "decision": "blocked",
            "reason": str(result.get("decision") or "materialization did not create a task"),
            "task_created": False,
        }
    return {
        "event_id": event_id,
        "decision": "materialized",
        "task_created": True,
        "task_id": result["task"]["id"],
    }


def dispatch_pending_events(*, limit: int = 100, path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Process a bounded deterministic batch of durable ingress events."""
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    events = list_events(limit=limit, path=path)
    results = [dispatch_event(event["id"], path=path) for event in events]
    return {
        "scanned": len(events),
        "materialized": sum(1 for result in results if result["decision"] == "materialized"),
        "already_materialized": sum(1 for result in results if result["decision"] == "already_materialized"),
        "blocked": sum(1 for result in results if result["decision"] in {"blocked", "invalid"}),
        "results": results,
    }
