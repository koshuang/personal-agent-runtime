from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from par.db import connect


def _cli(db: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "par", "--db", str(db), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _task_count(db: Path) -> int:
    with connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]


def test_dispatch_pending_cli_materializes_safe_event_once(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    _cli(
        db,
        "event",
        "ingest",
        "--idempotency-key",
        "cli-dispatch-1",
        "--source",
        "webhook",
        "--kind",
        "github.pull_request",
        "--requested-action",
        json.dumps(
            {
                "type": "task",
                "action": "inspect_repository",
                "goal": "Inspect CLI-dispatched event",
                "repo": "koshuang/personal-agent-runtime",
                "mode": "read-only",
                "scope": {"repo": "koshuang/personal-agent-runtime"},
                "required_capabilities": ["repo-read"],
                "acceptance_criteria": ["inspection evidence exists"],
                "non_goals": ["no writes"],
                "risk_permission_tier": "read-only",
                "evidence_required": ["persisted task"],
                "expected_next_state_transition": "event -> queued task",
            }
        ),
    )

    first = _cli(db, "dispatch", "pending", "--limit", "10")
    second = _cli(db, "dispatch", "pending", "--limit", "10")

    assert first["materialized"] == 1
    assert second["scanned"] == 0
    assert second["materialized"] == 0
    assert second["already_materialized"] == 0
    assert _task_count(db) == 1


def test_dispatch_pending_cli_respects_limit(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    for index in range(2):
        _cli(
            db,
            "event",
            "ingest",
            "--idempotency-key",
            f"cli-dispatch-limit-{index}",
            "--source",
            "webhook",
            "--kind",
            "github.pull_request",
            "--requested-action",
            json.dumps(
                {
                    "type": "task",
                    "action": "inspect_repository",
                    "goal": f"Inspect event {index}",
                    "mode": "read-only",
                    "required_capabilities": ["repo-read"],
                    "acceptance_criteria": ["evidence exists"],
                    "non_goals": ["no writes"],
                    "risk_permission_tier": "read-only",
                    "evidence_required": ["task exists"],
                    "expected_next_state_transition": "event -> queued task",
                }
            ),
        )

    first = _cli(db, "dispatch", "pending", "--limit", "1")
    second = _cli(db, "dispatch", "pending", "--limit", "1")
    third = _cli(db, "dispatch", "pending", "--limit", "1")

    assert first["scanned"] == 1
    assert first["materialized"] == 1
    assert second["scanned"] == 1
    assert second["materialized"] == 1
    assert third["scanned"] == 0
    assert _task_count(db) == 2
