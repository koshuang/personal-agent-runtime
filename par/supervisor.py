from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .ingress import ingest_event


SUPPORTED_INTENTS = {"observe", "request_action"}


@dataclass(frozen=True)
class SupervisorIntent:
    kind: str
    payload: dict[str, Any]
    requested_action: dict[str, Any]


def adapt_supervisor_intent(
    *,
    supervisor_id: str,
    intent: Mapping[str, Any],
) -> SupervisorIntent:
    """Normalize provider-specific supervisor output into a bounded runtime intent.

    Supervisor identity is descriptive only: it is deliberately excluded from
    requested authority and cannot grant execution permission.
    """
    identity = supervisor_id.strip()
    if not identity:
        raise ValueError("supervisor_id must be non-empty")
    if not isinstance(intent, Mapping):
        raise ValueError("intent must be an object")

    kind = intent.get("kind")
    if kind not in SUPPORTED_INTENTS:
        raise ValueError("unsupported supervisor intent")

    payload = intent.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("intent payload must be an object")

    if kind == "observe":
        if intent.get("requested_action") not in (None, {}):
            raise ValueError("observe intent cannot request an action")
        requested_action: dict[str, Any] = {}
    else:
        requested_action = intent.get("requested_action")
        if not isinstance(requested_action, dict) or not requested_action:
            raise ValueError("request_action intent requires requested_action")
        if any(key in requested_action for key in ("authority", "credential", "credentials", "max_cost_usd")):
            raise ValueError("supervisor intent cannot grant authority, credentials, or cost")

    return SupervisorIntent(
        kind=kind,
        payload=dict(payload),
        requested_action=dict(requested_action),
    )


def submit_supervisor_intent(
    *,
    supervisor_id: str,
    idempotency_key: str,
    intent: Mapping[str, Any],
    path,
) -> dict[str, Any]:
    """Persist a supervisor proposal through the existing durable ingress path."""
    normalized = adapt_supervisor_intent(
        supervisor_id=supervisor_id,
        intent=intent,
    )
    return ingest_event(
        idempotency_key=idempotency_key,
        source="api",
        kind=f"supervisor.{normalized.kind}",
        payload=normalized.payload,
        requested_action=normalized.requested_action,
        authority={},
        path=path,
    )
