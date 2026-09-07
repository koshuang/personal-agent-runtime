from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from par.checkpoint import checkpoint_run, latest_checkpoint, list_checkpoints
from par.db import claim_task, complete_task, create_task, init_db
from par.portability import export_state, restore_state


def test_active_owner_writes_monotonic_checkpoints_and_latest_resume_context(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="long task", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=db)

    first = checkpoint_run(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        context={"phase": "inspect", "completed": ["a"], "next_action": "inspect b"},
        path=db,
    )
    second = checkpoint_run(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        context={"phase": "verify", "completed": ["a", "b"], "next_action": "run tests"},
        path=db,
    )

    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["id"] != second["id"]
    assert [item["sequence"] for item in list_checkpoints(task_id=task["id"], path=db)] == [1, 2]

    latest = latest_checkpoint(task_id=task["id"], path=db)
    assert latest is not None
    assert latest["id"] == second["id"]
    assert latest["run_id"] == claimed["run_id"]
    assert latest["context"]["next_action"] == "run tests"


def test_checkpoint_rejects_non_owner_inactive_run_and_secret_like_context(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="protected checkpoint", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=db)

    with pytest.raises(RuntimeError, match="actively owned"):
        checkpoint_run(
            task_id=task["id"], run_id=claimed["run_id"], worker="worker-b", context={"step": 1}, path=db
        )

    with pytest.raises(ValueError, match="secret-like"):
        checkpoint_run(
            task_id=task["id"],
            run_id=claimed["run_id"],
            worker="worker-a",
            context={"nested": {"api_key": "do-not-store"}},
            path=db,
        )

    complete_task(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        summary="done",
        evidence=["ok"],
        path=db,
    )
    with pytest.raises(RuntimeError, match="actively owned"):
        checkpoint_run(
            task_id=task["id"], run_id=claimed["run_id"], worker="worker-a", context={"late": True}, path=db
        )


def test_checkpoint_survives_portable_state_restore_with_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    artifact = tmp_path / "state.parstate"
    restored = tmp_path / "fresh" / "runtime.db"
    init_db(source)
    task = create_task(goal="portable checkpoint", path=source)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=source)
    checkpoint = checkpoint_run(
        task_id=task["id"],
        run_id=claimed["run_id"],
        worker="worker-a",
        context={"verified": ["schema"], "next_action": "continue in fresh runtime"},
        path=source,
    )

    export_state(source, artifact)
    restore_state(artifact, restored)

    recovered = latest_checkpoint(task_id=task["id"], path=restored)
    assert recovered is not None
    assert recovered["id"] == checkpoint["id"]
    assert recovered["task_id"] == task["id"]
    assert recovered["run_id"] == claimed["run_id"]
    assert recovered["sequence"] == 1
    assert recovered["context"] == checkpoint["context"]


def test_checkpoint_cli_and_resume_context(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    task = create_task(goal="cli checkpoint", path=db)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=db)

    raw = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "par",
            "--db",
            str(db),
            "task",
            "checkpoint",
            task["id"],
            "--run-id",
            claimed["run_id"],
            "--worker",
            "worker-a",
            "--context",
            '{"next_action":"resume from here","evidence":["one"]}',
        ],
        text=True,
    )
    written = json.loads(raw)
    assert written["sequence"] == 1

    raw_resume = subprocess.check_output(
        [sys.executable, "-m", "par", "--db", str(db), "task", "resume-context", task["id"]],
        text=True,
    )
    resumed = json.loads(raw_resume)
    assert resumed["id"] == written["id"]
    assert resumed["context"]["next_action"] == "resume from here"
