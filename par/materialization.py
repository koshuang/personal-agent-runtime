from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .approvals import list_approvals
from .capabilities import normalize_capabilities
from .db import DEFAULT_DB, connect, create_task
from .ingress import get_event

_RUNTIME_OWNED_CONTEXT_KEYS = {
    "parent_task_id", "source_ingress_event_id", "ingress_source", "ingress_kind",
    "materialization_action", "materialization_scope", "approval_id", "required_capabilities",
}
_READ_ONLY_ACTION_CAPABILITIES = {"inspect_repository": {"repo-read"}}
_SQLITE_INT_MIN = -(2**63)
_SQLITE_INT_MAX = 2**63 - 1


def _required_string(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _required_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list) or not value: raise ValueError(f"{name} must be a non-empty list")
    return value


def _object(value: Any, *, name: str) -> dict[str, Any]:
    if value is None: return {}
    if not isinstance(value, dict): raise ValueError(f"{name} must be a JSON object")
    json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _capabilities(value: Any) -> list[str]:
    if value is None: return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("required_capabilities must be a list of non-empty strings")
    return normalize_capabilities([item.strip() for item in value])


def _task_request(event: dict[str, Any]) -> dict[str, Any]:
    requested = _object(event.get("requested_action"), name="requested_action")
    if requested.get("type") != "task": raise ValueError("requested_action.type must be task")
    action = _required_string(requested.get("action"), name="requested_action.action")
    goal = _required_string(requested.get("goal"), name="requested_action.goal")
    mode = _required_string(requested.get("mode"), name="requested_action.mode")
    acceptance_criteria = _required_list(requested.get("acceptance_criteria"), name="requested_action.acceptance_criteria")
    non_goals = _required_list(requested.get("non_goals"), name="requested_action.non_goals")
    risk_permission_tier = _required_string(requested.get("risk_permission_tier"), name="requested_action.risk_permission_tier")
    evidence_required = _required_list(requested.get("evidence_required"), name="requested_action.evidence_required")
    expected_next_state_transition = _required_string(requested.get("expected_next_state_transition"), name="requested_action.expected_next_state_transition")
    repo = requested.get("repo")
    if repo is not None: repo = _required_string(repo, name="requested_action.repo")
    scope = _object(requested.get("scope"), name="requested_action.scope")
    context = _object(requested.get("context"), name="requested_action.context")
    required_capabilities = _capabilities(requested.get("required_capabilities"))
    priority = requested.get("priority", 100)
    if isinstance(priority, bool) or not isinstance(priority, int): raise ValueError("requested_action.priority must be an integer")
    if not _SQLITE_INT_MIN <= priority <= _SQLITE_INT_MAX: raise ValueError("requested_action.priority is outside SQLite integer range")
    if mode == "read-only":
        allowed = _READ_ONLY_ACTION_CAPABILITIES.get(action)
        if allowed is None: raise ValueError("requested_action.action is not eligible for automatic read-only execution")
        if risk_permission_tier != "read-only": raise ValueError("read-only mode requires read-only risk_permission_tier")
        if set(required_capabilities) != allowed: raise ValueError("read-only action capabilities do not match the approved action contract")
    else:
        if not scope: raise ValueError("non-read-only requested_action.scope must be non-empty")
        if repo is not None and scope.get("repo") != repo: raise ValueError("non-read-only requested_action.scope.repo must exactly match requested_action.repo")
    return {"action": action, "goal": goal, "mode": mode, "repo": repo, "scope": scope, "context": context,
            "required_capabilities": required_capabilities, "priority": priority,
            "task_contract": {"acceptance_criteria": acceptance_criteria, "non_goals": non_goals,
                              "risk_permission_tier": risk_permission_tier, "evidence_required": evidence_required,
                              "expected_next_state_transition": expected_next_state_transition}}


def _matching_approval(event_id: str, request: dict[str, Any], *, path: Path) -> dict[str, Any] | None:
    list_approvals(status="approved", limit=1, path=path)
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM approval_requests WHERE status='approved' AND subject_type='ingress_event' AND subject_id=? AND action=? ORDER BY created_at ASC, id ASC", (event_id, request["action"])).fetchall()
    expected_scope = _canonical_json(request["scope"])
    for row in rows:
        approval = dict(row); approval_scope = json.loads(approval.pop("scope_json") or "{}")
        if _canonical_json(approval_scope) != expected_scope: continue
        approval["scope"] = approval_scope; return approval
    return None


def _sanitize_context(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in _RUNTIME_OWNED_CONTEXT_KEYS}


def _assert_existing_task_matches(task: dict[str, Any], *, request: dict[str, Any], event_id: str, context: dict[str, Any]) -> None:
    if task.get("goal") != request["goal"] or task.get("repo") != request["repo"] or task.get("mode") != request["mode"]: raise RuntimeError("ingress materialization idempotency key collided with a different task")
    if int(task.get("priority", 100)) != request["priority"]: raise RuntimeError("ingress materialization idempotency key collided with a different task")
    existing_context = json.loads(task.get("context_json") or "{}")
    if _canonical_json(existing_context) != _canonical_json(context): raise RuntimeError("ingress materialization idempotency key collided with a different task")
    if existing_context.get("source_ingress_event_id") != event_id: raise RuntimeError("ingress materialization idempotency key is not owned by this event")


def materialize_ingress_event(event_id: str, *, path: Path = DEFAULT_DB) -> dict[str, Any]:
    event = get_event(event_id, path=path)
    if event is None: raise KeyError(f"ingress event not found: {event_id}")
    request = _task_request(event); approval: dict[str, Any] | None = None
    if request["mode"] != "read-only":
        approval = _matching_approval(event_id, request, path=path)
        if approval is None: return {"decision": "approval_required", "event_id": event_id, "action": request["action"], "mode": request["mode"], "authority_is_grant": False, "task": None}
    context = _sanitize_context(request["context"])
    context.update({"task_contract": request["task_contract"], "source_ingress_event_id": event_id, "ingress_source": event["source"], "ingress_kind": event["kind"], "materialization_action": request["action"], "materialization_scope": request["scope"], "required_capabilities": request["required_capabilities"]})
    if approval is not None: context["approval_id"] = approval["id"]
    task = create_task(goal=request["goal"], repo=request["repo"], mode=request["mode"], context=context, priority=request["priority"], idempotency_key=f"ingress-event:{event_id}", actor=f"ingress:{event['source']}", path=path)
    _assert_existing_task_matches(task, request=request, event_id=event_id, context=context)
    return {"decision": "materialized", "event_id": event_id, "approval_id": approval["id"] if approval is not None else None, "authority_is_grant": False, "task": task}
