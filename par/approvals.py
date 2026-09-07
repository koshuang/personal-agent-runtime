from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect, now_iso

_STATUS_VALUES = {"pending", "approved", "rejected", "expired"}
_APPROVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_requests (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  action TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  requested_by TEXT NOT NULL,
  decided_by TEXT,
  decision_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_status_created_at
ON approval_requests(status, created_at);
"""


def _ensure_schema(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(_APPROVAL_SCHEMA)


def _json_object(value: dict[str, Any] | None, *, name: str) -> tuple[dict[str, Any], str]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return value, encoded


def _required(value: str, *, name: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["scope"] = json.loads(result.pop("scope_json") or "{}")
    result["approval_is_scoped_evidence"] = True
    result["approval_is_blanket_authority"] = False
    return result


def request_approval(
    *,
    idempotency_key: str,
    subject_type: str,
    subject_id: str,
    action: str,
    requested_by: str,
    scope: dict[str, Any] | None = None,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    key = _required(idempotency_key, name="idempotency_key")
    subject_type = _required(subject_type, name="subject_type")
    subject_id = _required(subject_id, name="subject_id")
    action = _required(action, name="action")
    requested_by = _required(requested_by, name="requested_by")
    _, scope_json = _json_object(scope, name="scope")

    _ensure_schema(path)
    approval_id = str(uuid.uuid4())
    ts = now_iso()
    with connect(path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO approval_requests
            (id, idempotency_key, subject_type, subject_id, action, scope_json, status, requested_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (approval_id, key, subject_type, subject_id, action, scope_json, requested_by, ts, ts),
        )
        if cur.rowcount == 0:
            row = conn.execute("SELECT * FROM approval_requests WHERE idempotency_key=?", (key,)).fetchone()
            if not row:
                raise RuntimeError("idempotent approval insert was ignored without an existing request")
            existing = dict(row)
            expected = (subject_type, subject_id, action, scope_json, requested_by)
            actual = (
                existing["subject_type"],
                existing["subject_id"],
                existing["action"],
                existing["scope_json"],
                existing["requested_by"],
            )
            if actual != expected:
                raise ValueError("idempotency_key was reused with a different approval request")
            return _decode(row)
        row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
    if not row:
        raise RuntimeError("approval request was not persisted")
    return _decode(row)


def decide_approval(
    approval_id: str,
    *,
    decision: str,
    decided_by: str,
    reason: str,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    approval_id = _required(approval_id, name="approval_id")
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    decided_by = _required(decided_by, name="decided_by")
    reason = _required(reason, name="reason")
    _ensure_schema(path)
    ts = now_iso()
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
        if not row:
            raise KeyError(f"approval request not found: {approval_id}")
        if row["status"] != "pending":
            raise ValueError(f"approval request is terminal: {row['status']}")
        cur = conn.execute(
            """
            UPDATE approval_requests
            SET status=?, decided_by=?, decision_reason=?, decided_at=?, updated_at=?
            WHERE id=? AND status='pending'
            """,
            (decision, decided_by, reason, ts, ts, approval_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError("approval decision lost a concurrent terminal-state race")
        result = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
    if not result:
        raise RuntimeError("approval decision was not persisted")
    return _decode(result)


def expire_approval(approval_id: str, *, actor: str, reason: str, path: Path = DEFAULT_DB) -> dict[str, Any]:
    approval_id = _required(approval_id, name="approval_id")
    actor = _required(actor, name="actor")
    reason = _required(reason, name="reason")
    _ensure_schema(path)
    ts = now_iso()
    with connect(path) as conn:
        cur = conn.execute(
            """
            UPDATE approval_requests
            SET status='expired', decided_by=?, decision_reason=?, decided_at=?, updated_at=?
            WHERE id=? AND status='pending'
            """,
            (actor, reason, ts, ts, approval_id),
        )
        if cur.rowcount != 1:
            row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
            if not row:
                raise KeyError(f"approval request not found: {approval_id}")
            raise ValueError(f"approval request is terminal: {row['status']}")
        row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
    return _decode(row)


def get_approval(approval_id: str, *, path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    _ensure_schema(path)
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
    return _decode(row) if row else None


def list_approvals(*, status: str | None = None, limit: int = 100, path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    if status is not None and status not in _STATUS_VALUES:
        raise ValueError(f"invalid approval status: {status}")
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    _ensure_schema(path)
    with connect(path) as conn:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM approval_requests ORDER BY created_at ASC, id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM approval_requests WHERE status=? ORDER BY created_at ASC, id ASC LIMIT ?",
                (status, limit),
            ).fetchall()
    return [_decode(row) for row in rows]
