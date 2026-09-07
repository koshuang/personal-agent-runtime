from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect, now_iso
from .ingress import get_event, list_events
from .materialization import materialize_ingress_event

_RECEIVED = "received"
_MATERIALIZED = "materialized"
_BLOCKED = "blocked_auto_dispatch"
_INVALID = "invalid_auto_dispatch"


def _set_status(event_id: str, status: str, *, path: Path) -> None:
    with connect(path) as conn:
        conn.execute(
            "UPDATE ingress_events SET status=?, updated_at=? WHERE id=? AND status<>?",
            (status, now_iso(), event_id, status),
        )


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
        _set_status(event_id, _MATERIALIZED, path=path)
        return {"event_id": event_id, "decision": "already_materialized", "task_created": False}

    eligible, reason = _dispatchable(event)
    if not eligible:
        _set_status(event_id, _BLOCKED, path=path)
        return {
            "event_id": event_id,
            "decision": "blocked",
            "reason": reason,
            "task_created": False,
        }

    try:
        result = materialize_ingress_event(event_id, path=path)
    except (ValueError, RuntimeError) as exc:
        _set_status(event_id, _INVALID, path=path)
        return {
            "event_id": event_id,
            "decision": "invalid",
            "reason": str(exc),
            "task_created": False,
        }
    if result.get("decision") != "materialized" or not result.get("task"):
        _set_status(event_id, _BLOCKED, path=path)
        return {
            "event_id": event_id,
            "decision": "blocked",
            "reason": str(result.get("decision") or "materialization did not create a task"),
            "task_created": False,
        }
    _set_status(event_id, _MATERIALIZED, path=path)
    return {
        "event_id": event_id,
        "decision": "materialized",
        "task_created": True,
        "task_id": result["task"]["id"],
    }


def _pending_events(*, limit: int, path: Path) -> list[dict[str, Any]]:
    # Ensure the additive ingress schema exists without inventing a second state store.
    list_events(limit=1, path=path)
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM ingress_events
            WHERE status=?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (_RECEIVED, limit),
        ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        event = get_event(row["id"], path=path)
        if event is not None:
            events.append(event)
    return events


def dispatch_pending_events(*, limit: int = 100, path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Process a bounded deterministic batch of still-received durable ingress events."""
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    events = _pending_events(limit=limit, path=path)
    results = [dispatch_event(event["id"], path=path) for event in events]
    return {
        "scanned": len(events),
        "materialized": sum(1 for result in results if result["decision"] == "materialized"),
        "already_materialized": sum(1 for result in results if result["decision"] == "already_materialized"),
        "blocked": sum(1 for result in results if result["decision"] in {"blocked", "invalid"}),
        "results": results,
    }
