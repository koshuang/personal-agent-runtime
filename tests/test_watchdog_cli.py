from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from par.db import connect
from par.ingress import ingest_event


def _cli(db: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "par", "--db", str(db), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _counts(db: Path) -> tuple[int, int, int]:
    with connect(db) as conn:
        return (
            conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        )


def _safe_requested_action() -> dict:
    return {
        "type": "task",
        "action": "inspect_repository",
        "goal": "Inspect repository state",
        "repo": "koshuang/personal-agent-runtime",
        "mode": "read-only",
        "scope": {"repo": "koshuang/personal-agent-runtime"},
        "context": {},
        "required_capabilities": ["repo-read"],
        "priority": 100,
        "acceptance_criteria": ["Persisted inspection task exists"],
        "non_goals": ["No repository write"],
        "risk_permission_tier": "low",
        "evidence_required": ["queued task"],
        "expected_next_state_transition": "ingress -> queued task",
    }


def test_watchdog_wake_cli_materializes_missed_safe_ingress_and_projects_it(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    ingest_event(
        idempotency_key="watchdog-cli-safe",
        source="schedule",
        kind="recovery",
        requested_action=_safe_requested_action(),
        path=db,
    )

    result = _cli(db, "watchdog", "wake", "--dispatch-limit", "1", "--worker", "watchdog-test")

    assert result["decision"] == "next_action_available"
    assert result["dispatch"]["materialized"] == 1
    assert result["scheduler"]["decision"] == "execute"
    assert result["scheduler"]["task_id"] is not None
    assert result["target_system_read_only"] is True


def test_watchdog_wake_cli_repeated_idle_is_zero_noise(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    _cli(db, "init")
    before = _counts(db)

    first = _cli(db, "watchdog", "wake")
    second = _cli(db, "watchdog", "wake")

    assert first["decision"] == "idle"
    assert second["decision"] == "idle"
    assert first["runtime_state_mutated"] is False
    assert second["runtime_state_mutated"] is False
    assert _counts(db) == before
