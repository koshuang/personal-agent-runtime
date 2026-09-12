from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .ingress import ingest_event


SUPPORTED_INTENTS = {"observe", "request_action"}


def _freeze(value: Any) -> Any:
    """Recursively detach and freeze JSON-like supervisor values."""
    if isinstance(value, MappingABC):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Return an isolated mutable copy for the durable ingress boundary."""
    if isinstance(value, MappingABC):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class SupervisorIntent:
    kind: str
    payload: Mapping[str, Any]
    requested_action: Mapping[str, Any]


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
        payload=_freeze(payload),
        requested_action=_freeze(requested_action),
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
        payload=_thaw(normalized.payload),
        requested_action=_thaw(normalized.requested_action),
        authority={},
        path=path,
    )
