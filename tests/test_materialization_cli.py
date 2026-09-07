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


def test_event_materialize_cli_is_bounded_and_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    event = _cli(
        db,
        "event",
        "ingest",
        "--idempotency-key",
        "cli-materialize-1",
        "--source",
        "human",
        "--kind",
        "continue",
        "--requested-action",
        json.dumps(
            {
                "type": "task",
                "action": "inspect_repository",
                "goal": "Inspect repository state",
                "repo": "koshuang/personal-agent-runtime",
                "mode": "read-only",
                "scope": {"repo": "koshuang/personal-agent-runtime"},
                "required_capabilities": ["repo-read"],
            }
        ),
    )

    first = _cli(db, "event", "materialize", event["id"])
    second = _cli(db, "event", "materialize", event["id"])

    assert first["decision"] == "materialized"
    assert second["task"]["id"] == first["task"]["id"]
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
