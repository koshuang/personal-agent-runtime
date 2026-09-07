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


def _counts(db: Path) -> tuple[int, int, int]:
    with connect(db) as conn:
        return (
            conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        )


def test_command_chk_cli_is_read_only_and_repeatable(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    _cli(db, "init")
    before = _counts(db)
    first = _cli(db, "command", "chk")
    second = _cli(db, "command", "chk")
    assert first == second
    assert first["decision"] == "checked"
    assert first["scheduler"]["decision"] == "idle"
    assert _counts(db) == before


def test_command_continue_cli_projects_existing_task(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    task = _cli(db, "task", "create", "--goal", "Persisted CLI work")
    before = _counts(db)
    result = _cli(db, "command", "continue")
    assert result["decision"] == "next_action_available"
    assert result["next"]["task_id"] == task["id"]
    assert _counts(db) == before


def test_command_fix_cli_requires_specific_target(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    _cli(db, "init")
    before = _counts(db)
    result = _cli(db, "command", "fix")
    assert result["decision"] == "needs_input"
    assert _counts(db) == before
