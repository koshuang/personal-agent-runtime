from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect, now_iso

SOURCE_VALUES = {"schedule", "human", "api", "webhook"}

_INGRESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingress_events (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  requested_action_json TEXT NOT NULL DEFAULT '{}',
  authority_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'received',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingress_events_source_created_at
ON ingress_events(source, created_at);
CREATE INDEX IF NOT EXISTS idx_ingress_events_status_created_at_id
ON ingress_events(status, created_at, id);
"""


def _ensure_schema(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(_INGRESS_SCHEMA)


def _validate_json_value(value: Any, *, name: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must contain only finite JSON numbers")
    if isinstance(value, dict):
        for nested in value.values():
            _validate_json_value(nested, name=name)
    elif isinstance(value, list):
        for nested in value:
            _validate_json_value(nested, name=name)


def _object(value: dict[str, Any] | None, *, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    _validate_json_value(value, name=name)
    return value


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json") or "{}")
    result["requested_action"] = json.loads(
        result.pop("requested_action_json") or "{}"
    )
    result["authority"] = json.loads(result.pop("authority_json") or "{}")
    result["authority_is_grant"] = False
    return result


def ingest_event(
    *,
    idempotency_key: str,
    source: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    requested_action: dict[str, Any] | None = None,
    authority: dict[str, Any] | None = None,
    path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    key = idempotency_key.strip()
    if not key:
        raise ValueError("idempotency_key must be non-empty")
    if source not in SOURCE_VALUES:
        raise ValueError(f"invalid ingress source: {source}")

    kind = kind.strip()
    if not kind:
        raise ValueError("kind must be non-empty")

    payload_obj = _object(payload, name="payload")
    action_obj = _object(requested_action, name="requested_action")
    authority_obj = _object(authority, name="authority")
    opts = {
        "ensure_ascii": False,
        "sort_keys": True,
        "separators": (",", ":"),
        "allow_nan": False,
    }
    payload_json = json.dumps(payload_obj, **opts)
    action_json = json.dumps(action_obj, **opts)
    authority_json = json.dumps(authority_obj, **opts)

    _ensure_schema(path)
    event_id = str(uuid.uuid4())
    ts = now_iso()
    with connect(path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO ingress_events
            (id, idempotency_key, source, kind, payload_json, requested_action_json,
             authority_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'received', ?, ?)
            """,
            (
                event_id,
                key,
                source,
                kind,
                payload_json,
                action_json,
                authority_json,
                ts,
                ts,
            ),
        )
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT * FROM ingress_events WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if not row:
                raise RuntimeError(
                    "idempotent ingress insert was ignored without an existing event"
                )
            existing = dict(row)
            expected = (
                source,
                kind,
                payload_json,
                action_json,
                authority_json,
            )
            actual = (
                existing["source"],
                existing["kind"],
                existing["payload_json"],
                existing["requested_action_json"],
                existing["authority_json"],
            )
            if actual != expected:
                raise ValueError(
                    "idempotency_key was reused with a different ingress event"
                )
            return _decode(row)

        row = conn.execute(
            "SELECT * FROM ingress_events WHERE id=?",
            (event_id,),
        ).fetchone()

    if not row:
        raise RuntimeError("ingress event was not persisted")
    return _decode(row)


def get_event(
    event_id: str,
    *,
    path: Path = DEFAULT_DB,
) -> dict[str, Any] | None:
    _ensure_schema(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM ingress_events WHERE id=?",
            (event_id,),
        ).fetchone()
    return _decode(row) if row else None


def list_events(
    *,
    source: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    path: Path = DEFAULT_DB,
) -> list[dict[str, Any]]:
    if source is not None and source not in SOURCE_VALUES:
        raise ValueError(f"invalid ingress source: {source}")
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")

    _ensure_schema(path)
    clauses: list[str] = []
    params: list[Any] = []
    if source is not None:
        clauses.append("source=?")
        params.append(source)
    if kind is not None:
        clauses.append("kind=?")
        params.append(kind)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT * FROM ingress_events{where} "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [_decode(row) for row in rows]
