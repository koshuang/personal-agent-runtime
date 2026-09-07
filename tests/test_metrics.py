from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from par.db import claim_task, complete_task, create_task, init_db
from par.metrics import metrics_summary, record_run_telemetry, validate_telemetry
from par.portability import export_state, restore_state


def _finished_run(db: Path, *, worker: str = "worker-a") -> tuple[dict, str]:
    task = create_task(goal="inspect", mode="read-only", path=db)
    claimed = claim_task(task_id=task["id"], worker=worker, path=db)
    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker=worker,
        summary="done",
        evidence=[{"kind": "test"}],
        path=db,
    )
    return task, claimed["run_id"]


def test_metrics_distinguish_explicit_zero_from_unknown(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task, run_id = _finished_run(db)
    telemetry = record_run_telemetry(
        task_id=task["id"],
        run_id=run_id,
        worker="worker-a",
        cost_usd=0.0,
        quota_units=0.0,
        source="deterministic-test",
        path=db,
    )
    assert telemetry["duration_ms"] is not None
    assert telemetry["cost_usd"] == 0.0
    assert telemetry["quota_units"] == 0.0

    _finished_run(db, worker="worker-b")
    summary = metrics_summary(path=db)
    assert summary["run_count"] == 2
    assert summary["completed_runs"] == 2
    assert summary["unknown_duration_runs"] == 0
    assert summary["known_total_cost_usd"] == 0.0
    assert summary["unknown_cost_runs"] == 1
    assert summary["known_total_quota_units"] == 0.0
    assert summary["unknown_quota_runs"] == 1


@pytest.mark.parametrize("field,value", [("duration_ms", -1), ("cost_usd", -0.1), ("quota_units", float("nan")), ("cost_usd", float("inf"))])
def test_invalid_telemetry_fails_closed(field: str, value: float) -> None:
    kwargs = {"duration_ms": None, "cost_usd": None, "quota_units": None}
    kwargs[field] = value
    with pytest.raises(ValueError):
        validate_telemetry(**kwargs)


def test_telemetry_is_append_once(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task, run_id = _finished_run(db)
    record_run_telemetry(task_id=task["id"], run_id=run_id, worker="worker-a", cost_usd=0, path=db)
    with pytest.raises(RuntimeError, match="append-once"):
        record_run_telemetry(task_id=task["id"], run_id=run_id, worker="worker-a", cost_usd=1, path=db)


def test_telemetry_survives_portable_restore(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    artifact = tmp_path / "state.parstate"
    init_db(source)
    task, run_id = _finished_run(source)
    record_run_telemetry(
        task_id=task["id"],
        run_id=run_id,
        worker="worker-a",
        duration_ms=123,
        cost_usd=0,
        quota_units=7,
        source="fixture",
        path=source,
    )
    export_state(source, artifact)
    restore_state(artifact, restored)
    summary = metrics_summary(path=restored)
    assert summary["run_count"] == 1
    assert summary["known_total_duration_ms"] == 123
    assert summary["known_total_cost_usd"] == 0.0
    assert summary["known_total_quota_units"] == 7.0


def test_metrics_cli_summary_and_complete_telemetry(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="cli", mode="read-only", path=db)
    claimed = claim_task(task_id=task["id"], worker="cli-worker", path=db)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "par",
            "--db",
            str(db),
            "task",
            "complete",
            task["id"],
            "--run-id",
            claimed["run_id"],
            "--worker",
            "cli-worker",
            "--summary",
            "done",
            "--cost-usd",
            "0",
            "--quota-units",
            "2",
            "--telemetry-source",
            "cli-test",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout)["telemetry"]["cost_usd"] == 0.0

    result = subprocess.run(
        [sys.executable, "-m", "par", "--db", str(db), "metrics", "summary"],
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(result.stdout)
    assert summary["run_count"] == 1
    assert summary["known_total_quota_units"] == 2.0
    assert summary["unknown_cost_runs"] == 0
