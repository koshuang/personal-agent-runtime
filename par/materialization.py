from __future__ import annotations

from pathlib import Path
from typing import Any

from .approvals import list_approvals
from .db import DEFAULT_DB, create_task
from .ingress import get_event


def _required_string(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _object(value: Any, *, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _capabilities(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("required_capabilities must be a list of non-empty strings")
    return [item.strip() for item in value]


def _task_request(event: dict[str, Any]) -> dict[str, Any]:
    requested = _object(event.get("requested_action"), name="requested_action")
    if requested.get("type") != "task":
        raise ValueError("requested_action.type must be task")

    action = _required_string(requested.get("action"), name="requested_action.action")
    goal = _required_string(requested.get("goal"), name="requested_action.goal")
    mode = _required_string(requested.get("mode"), name="requested_action.mode")
    repo = requested.get("repo")
    if repo is not None:
        repo = _required_string(repo, name="requested_action.repo")
    scope = _object(requested.get("scope"), name="requested_action.scope")
    context = _object(requested.get("context"), name="requested_action.context")
    required_capabilities = _capabilities(requested.get("required_capabilities"))
    priority = requested.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ValueError("requested_action.priority must be an integer")

    if mode != "read-only":
        if not scope:
            raise ValueError("non-read-only requested_action.scope must be non-empty")
        if repo is not None and scope.get("repo") != repo:
            raise ValueError("non-read-only requested_action.scope.repo must exactly match requested_action.repo")

    return {
        "action": action,
        "goal": goal,
        "mode": mode,
        "repo": repo,
        "scope": scope,
        "context": context,
        "required_capabilities": required_capabilities,
        "priority": priority,
    }


def _matching_approval(event_id: str, request: dict[str, Any], *, path: Path) -> dict[str, Any] | None:
    for approval in list_approvals(status="approved", limit=1000, path=path):
        if approval["subject_type"] != "ingress_event":
            continue
        if approval["subject_id"] != event_id:
            continue
        if approval["action"] != request["action"]:
            continue
        if approval["scope"] != request["scope"]:
            continue
        return approval
    return None


def materialize_ingress_event(event_id: str, *, path: Path = DEFAULT_DB) -> dict[str, Any]:
    event = get_event(event_id, path=path)
    if event is None:
        raise KeyError(f"ingress event not found: {event_id}")

    request = _task_request(event)
    approval: dict[str, Any] | None = None
    if request["mode"] != "read-only":
        approval = _matching_approval(event_id, request, path=path)
        if approval is None:
            return {
                "decision": "approval_required",
                "event_id": event_id,
                "action": request["action"],
                "mode": request["mode"],
                "authority_is_grant": False,
                "task": None,
            }

    context = dict(request["context"])
    context["source_ingress_event_id"] = event_id
    context["ingress_source"] = event["source"]
    context["ingress_kind"] = event["kind"]
    context["materialization_action"] = request["action"]
    context["materialization_scope"] = request["scope"]
    if request["required_capabilities"]:
        context["required_capabilities"] = request["required_capabilities"]
    if approval is not None:
        context["approval_id"] = approval["id"]

    task = create_task(
        goal=request["goal"],
        repo=request["repo"],
        mode=request["mode"],
        context=context,
        priority=request["priority"],
        idempotency_key=f"ingress-event:{event_id}",
        path=path,
    )
    return {
        "decision": "materialized",
        "event_id": event_id,
        "approval_id": approval["id"] if approval is not None else None,
        "authority_is_grant": False,
        "task": task,
    }
