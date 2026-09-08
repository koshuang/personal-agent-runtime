from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import DEFAULT_DB
from .dispatcher import dispatch_pending_events
from .reconcile import reconcile
from .scheduler import decide


def watchdog_wake(
    *,
    dispatch_limit: int = 100,
    worker: str | None = None,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    """Run one bounded recovery wake without claiming or executing work."""
    dispatch = dispatch_pending_events(limit=dispatch_limit, path=path)
    reconciliation = reconcile(path=path)
    scheduler = decide(worker=worker, path=path)
    return {
        "decision": "idle" if scheduler.get("decision") == "idle" else "next_action_available",
        "read_only": True,
        "mutated_execution_state": False,
        "dispatch": dispatch,
        "reconciliation": reconciliation,
        "scheduler": scheduler,
    }
