from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB

_CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RESERVED_AUTHORITY_PREFIXES = ("credential", "secret", "production", "paid", "billing", "admin")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS worker_capabilities (
          worker TEXT NOT NULL,
          capability TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(worker, capability)
        )
        """
    )


def validate_capability(capability: str) -> str:
    value = capability.strip().lower()
    if not _CAPABILITY_RE.fullmatch(value):
        raise ValueError(f"invalid capability: {capability}")
    if any(value == prefix or value.startswith(prefix + ".") or value.startswith(prefix + "-") for prefix in _RESERVED_AUTHORITY_PREFIXES):
        raise ValueError(f"capability cannot represent authority: {capability}")
    return value


def normalize_capabilities(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    return sorted({validate_capability(value) for value in values})


def required_capabilities(task: dict[str, Any]) -> list[str]:
    raw = json.loads(task.get("context_json") or "{}")
    values = raw.get("required_capabilities", [])
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("task required_capabilities must be a list of strings")
    return normalize_capabilities(values)


def declare_worker_capabilities(*, worker: str, capabilities: list[str], path: Path = DEFAULT_DB) -> dict[str, Any]:
    if not worker.strip():
        raise ValueError("worker is required")
    normalized = normalize_capabilities(capabilities)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM worker_capabilities WHERE worker=?", (worker,))
        conn.executemany(
            "INSERT INTO worker_capabilities(worker, capability) VALUES (?, ?)",
            [(worker, capability) for capability in normalized],
        )
    return {"worker": worker, "capabilities": normalized, "authoritative": False}


def get_worker_capabilities(*, worker: str, path: Path = DEFAULT_DB) -> list[str]:
    if not path.exists():
        return []
    with _connect(path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT capability FROM worker_capabilities WHERE worker=? ORDER BY capability",
            (worker,),
        ).fetchall()
    return [str(row["capability"]) for row in rows]


def evaluate_worker_eligibility(*, task: dict[str, Any], worker: str | None, path: Path = DEFAULT_DB) -> dict[str, Any]:
    required = required_capabilities(task)
    if not required:
        return {
            "eligible": True,
            "worker": worker,
            "required_capabilities": [],
            "declared_capabilities": get_worker_capabilities(worker=worker, path=path) if worker else [],
            "missing_capabilities": [],
            "reason": "task has no required capabilities",
            "authority_expanded": False,
        }
    if not worker:
        return {
            "eligible": False,
            "worker": None,
            "required_capabilities": required,
            "declared_capabilities": [],
            "missing_capabilities": required,
            "reason": "task requires capabilities but no worker identity was provided",
            "authority_expanded": False,
        }
    declared = get_worker_capabilities(worker=worker, path=path)
    missing = sorted(set(required) - set(declared))
    return {
        "eligible": not missing,
        "worker": worker,
        "required_capabilities": required,
        "declared_capabilities": declared,
        "missing_capabilities": missing,
        "reason": "all required capabilities are declared" if not missing else "worker is missing required capabilities",
        "authority_expanded": False,
    }


def claim_task_if_eligible(*, task_id: str, worker: str, path: Path = DEFAULT_DB, **claim_kwargs: Any) -> dict[str, Any]:
    from .db import claim_task, get_task

    task = get_task(task_id, path=path)
    if not task:
        raise KeyError(task_id)
    eligibility = evaluate_worker_eligibility(task=task, worker=worker, path=path)
    if not eligibility["eligible"]:
        missing = ", ".join(eligibility["missing_capabilities"])
        raise RuntimeError(f"worker is not eligible for task; missing capabilities: {missing}")
    claimed = claim_task(task_id=task_id, worker=worker, path=path, **claim_kwargs)
    claimed["eligibility"] = eligibility
    return claimed
