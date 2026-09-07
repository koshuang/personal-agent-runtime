from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect

TELEMETRY_KEY = "execution_telemetry"


def _non_negative(name: str, value: float | int | None) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


def validate_telemetry(*, duration_ms: int | None = None, cost_usd: float | None = None, quota_units: float | None = None, source: str | None = None) -> dict[str, Any]:
    duration_ms = _non_negative("duration_ms", duration_ms)
    cost_usd = _non_negative("cost_usd", cost_usd)
    quota_units = _non_negative("quota_units", quota_units)
    if source is not None and not source.strip():
        raise ValueError("telemetry source must be non-empty when provided")
    return {"duration_ms": duration_ms, "cost_usd": cost_usd, "quota_units": quota_units, "source": source}


def _derived_duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at or not finished_at:
        return None
    started = datetime.fromisoformat(started_at)
    finished = datetime.fromisoformat(finished_at)
    return max(0, int((finished - started).total_seconds() * 1000))


def record_run_telemetry(*, task_id: str, run_id: str, worker: str, duration_ms: int | None = None, cost_usd: float | None = None, quota_units: float | None = None, source: str | None = None, path: Path = DEFAULT_DB) -> dict[str, Any]:
    telemetry = validate_telemetry(duration_ms=duration_ms, cost_usd=cost_usd, quota_units=quota_units, source=source)
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=? AND task_id=? AND worker=?", (run_id, task_id, worker)).fetchone()
        if not row:
            raise RuntimeError("run does not belong to this worker/task")
        if row["status"] not in {"completed", "failed"} or not row["finished_at"]:
            raise RuntimeError("execution telemetry can only be recorded for a finished run")
        metadata = json.loads(row["metadata_json"] or "{}")
        if TELEMETRY_KEY in metadata:
            raise RuntimeError("execution telemetry is append-once and already exists")
        if telemetry["duration_ms"] is None:
            telemetry["duration_ms"] = _derived_duration_ms(row["started_at"], row["finished_at"])
        metadata[TELEMETRY_KEY] = telemetry
        conn.execute("UPDATE runs SET metadata_json=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), run_id))
    return telemetry


def metrics_summary(*, path: Path = DEFAULT_DB) -> dict[str, Any]:
    with connect(path) as conn:
        rows = list(conn.execute("SELECT status, started_at, finished_at, metadata_json FROM runs ORDER BY started_at, id"))
    completed = sum(1 for row in rows if row["status"] == "completed")
    failed = sum(1 for row in rows if row["status"] == "failed")
    durations: list[int] = []
    costs: list[float] = []
    quotas: list[float] = []
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        telemetry = metadata.get(TELEMETRY_KEY) or {}
        duration = telemetry.get("duration_ms")
        if duration is None:
            duration = _derived_duration_ms(row["started_at"], row["finished_at"])
        if duration is not None:
            durations.append(int(duration))
        if telemetry.get("cost_usd") is not None:
            costs.append(float(telemetry["cost_usd"]))
        if telemetry.get("quota_units") is not None:
            quotas.append(float(telemetry["quota_units"]))
    return {
        "run_count": len(rows),
        "completed_runs": completed,
        "failed_runs": failed,
        "known_total_duration_ms": sum(durations),
        "unknown_duration_runs": len(rows) - len(durations),
        "known_total_cost_usd": sum(costs),
        "unknown_cost_runs": len(rows) - len(costs),
        "known_total_quota_units": sum(quotas),
        "unknown_quota_runs": len(rows) - len(quotas),
    }
