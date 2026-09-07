from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(db: Path, *args: str) -> dict:
    out = subprocess.check_output([sys.executable, "-m", "par", "--db", str(db), *args], text=True)
    return json.loads(out)


def test_review_cli_worker_to_critic(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    _run(db, "init")
    task = _run(
        db,
        "task",
        "create",
        "--goal",
        "review me",
        "--context",
        '{"requires_independent_review":true}',
    )
    claimed = _run(db, "task", "claim", task["id"], "--worker", "worker-a", "--provider", "subscription", "--model", "model-a")
    _run(
        db,
        "task",
        "complete",
        task["id"],
        "--run-id",
        claimed["run_id"],
        "--worker",
        "worker-a",
        "--summary",
        "done",
        "--evidence",
        '["ok"]',
    )
    review = _run(
        db,
        "review",
        "submit",
        task["id"],
        "--reviewer",
        "critic-b",
        "--verdict",
        "accepted",
        "--recommended-action",
        "close",
    )
    assert review["role"] == "critic"
    assert review["status"] == "completed"
