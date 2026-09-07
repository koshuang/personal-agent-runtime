from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import DEFAULT_DB
from .reconcile import reconcile
from .scheduler import decide

COMMANDS = {"chk", "continue", "fix"}


def _projection(*, worker: str | None, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    reconciliation = reconcile(path=path)
    scheduler = decide(worker=worker, path=path)
    return reconciliation, scheduler


def _chk(*, worker: str | None, path: Path) -> dict[str, Any]:
    reconciliation, scheduler = _projection(worker=worker, path=path)
    return {
        "command": "chk",
        "decision": "checked",
        "read_only": True,
        "mutated": False,
        "reconciliation": reconciliation,
        "scheduler": scheduler,
    }


def _continue(*, worker: str | None, path: Path) -> dict[str, Any]:
    reconciliation, scheduler = _projection(worker=worker, path=path)
    decision = str(scheduler["decision"])
    if decision == "idle":
        return {
            "command": "continue",
            "decision": "idle",
            "reason": "No durable reconciliation finding or queued work can be continued without inventing new work.",
            "read_only": True,
            "mutated": False,
            "scheduler": scheduler,
            "reconciliation": reconciliation,
        }
    return {
        "command": "continue",
        "decision": "next_action_available",
        "read_only": True,
        "mutated": False,
        "next": scheduler,
        "reconciliation": reconciliation,
    }


def _fix(
    *,
    finding_type: str | None,
    task_id: str | None,
    worker: str | None,
    path: Path,
) -> dict[str, Any]:
    reconciliation, scheduler = _projection(worker=worker, path=path)
    if not finding_type and not task_id:
        return {
            "command": "fix",
            "decision": "needs_input",
            "reason": "Fix requires an explicit durable finding type or task target; command wording alone is not authority.",
            "read_only": True,
            "mutated": False,
            "reconciliation": reconciliation,
        }

    matches = [
        finding
        for finding in reconciliation["findings"]
        if (finding_type is None or finding.get("type") == finding_type)
        and (task_id is None or finding.get("task_id") == task_id)
    ]
    if not matches:
        return {
            "command": "fix",
            "decision": "target_not_found",
            "reason": "No current durable reconciliation finding matches the requested target.",
            "read_only": True,
            "mutated": False,
            "target": {"finding_type": finding_type, "task_id": task_id},
        }
    if len(matches) != 1:
        return {
            "command": "fix",
            "decision": "needs_input",
            "reason": "The supplied target matches multiple durable findings; provide a more specific task target.",
            "read_only": True,
            "mutated": False,
            "target": {"finding_type": finding_type, "task_id": task_id},
            "match_count": len(matches),
        }

    finding = matches[0]
    return {
        "command": "fix",
        "decision": "fix_proposed",
        "reason": "A single durable finding was identified. This resolver only proposes the bounded next action and performs no mutation.",
        "read_only": True,
        "mutated": False,
        "finding": finding,
        "proposed_action": finding.get("action"),
        "requires_existing_gates": True,
        "scheduler": scheduler,
    }


def resolve_command(
    command: str,
    *,
    worker: str | None = None,
    finding_type: str | None = None,
    task_id: str | None = None,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    """Resolve a short human command from durable state without mutating runtime state."""
    normalized = command.strip().lower()
    if normalized not in COMMANDS:
        raise ValueError(f"unsupported command: {command}")
    if normalized == "chk":
        if finding_type or task_id:
            raise ValueError("chk does not accept a fix target")
        return _chk(worker=worker, path=path)
    if normalized == "continue":
        if finding_type or task_id:
            raise ValueError("continue does not accept a fix target")
        return _continue(worker=worker, path=path)
    return _fix(finding_type=finding_type, task_id=task_id, worker=worker, path=path)
