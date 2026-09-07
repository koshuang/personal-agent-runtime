from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import DEFAULT_DB
from .ingress import ingest_event

_SUPPORTED_EVENTS = {"pull_request", "pull_request_review", "check_run"}


def _required_string(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _repository(value: Any) -> str:
    repo = _required_string(value, name="repository")
    parts = repo.split("/")
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise ValueError("repository must be in owner/name form")
    return repo


def _pull_request_number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("pull_request_number must be a positive integer")
    return value


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("payload must be a JSON object")
    return value


def ingest_github_pr_event(
    *,
    event_name: str,
    delivery_id: str,
    repository: str,
    pull_request_number: int,
    payload: dict[str, Any] | None = None,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    """Normalize one bounded GitHub PR event into the existing durable ingress contract."""
    event_name = _required_string(event_name, name="event_name")
    if event_name not in _SUPPORTED_EVENTS:
        raise ValueError(f"unsupported GitHub event: {event_name}")
    delivery_id = _required_string(delivery_id, name="delivery_id")
    repository = _repository(repository)
    pull_request_number = _pull_request_number(pull_request_number)
    payload_obj = _payload(payload)

    action = payload_obj.get("action")
    if action is not None and (not isinstance(action, str) or not action.strip()):
        raise ValueError("payload.action must be a non-empty string when provided")

    requested_action = {
        "type": "task",
        "action": "inspect_github_pull_request_state",
        "goal": f"Inspect GitHub PR state for {repository}#{pull_request_number}",
        "repo": repository,
        "mode": "read-only",
        "scope": {"repo": repository, "pull_request": pull_request_number},
        "context": {
            "github_event_name": event_name,
            "github_delivery_id": delivery_id,
            "github_pull_request_number": pull_request_number,
            "github_action": action.strip() if isinstance(action, str) else None,
        },
        "required_capabilities": ["repo-read"],
        "acceptance_criteria": [
            "Current PR review/check state is inspected from authoritative GitHub data",
            "Any actionable review or CI blocker is reported as evidence rather than inferred from chat history",
        ],
        "non_goals": [
            "Do not merge, comment, push, rerun workflows, deploy, or mutate GitHub state",
            "Do not infer write authority from webhook actor or credential presence",
        ],
        "risk_permission_tier": "read-only",
        "evidence_required": [
            "GitHub PR identity and current review/check status",
            "Explicit blocker/finding evidence when action is required",
        ],
        "expected_next_state_transition": "github event -> durable ingress -> one bounded read-only PR inspection task",
    }

    return ingest_event(
        idempotency_key=f"github-delivery:{delivery_id}",
        source="webhook",
        kind=f"github.{event_name}",
        payload={
            "repository": repository,
            "pull_request_number": pull_request_number,
            "event_name": event_name,
            "delivery_id": delivery_id,
            "event": payload_obj,
        },
        requested_action=requested_action,
        authority={
            "source_actor_is_grant": False,
            "credential_presence_is_grant": False,
        },
        path=path,
    )
