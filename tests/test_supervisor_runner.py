from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from par.ingress import get_event, list_events
from par.portability import export_state, restore_state
from par.supervisor_runner import sanitized_environment


def _runner(db: Path, provider: str, key: str, proposal: dict, *, env=None, check=True):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "par.supervisor_runner",
            "--db",
            str(db),
            "--provider",
            provider,
            "--idempotency-key",
            key,
        ],
        input=json.dumps(proposal),
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def test_sanitized_environment_excludes_ambient_credentials() -> None:
    env = sanitized_environment({"PATH": "/bin", "AWS_ACCESS_KEY_ID": "secret", "PAR_TEST_AMBIENT_SECRET": "secret"})
    assert env == {"PATH": "/bin"}


def test_provider_child_does_not_receive_parent_ambient_secret(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["PAR_TEST_AMBIENT_SECRET"] = "synthetic-secret"
    db = tmp_path / "runtime.db"
    completed = _runner(
        db,
        "fixture_a",
        "supervisor:isolation",
        {"kind": "observe", "payload": {"finding": "safe"}},
        env=env,
    )
    assert completed.returncode == 0
    assert len(list_events(path=db)) == 1


def test_real_provider_replacement_preserves_durable_semantics_and_restore(tmp_path: Path) -> None:
    proposal = {
        "kind": "request_action",
        "payload": {"task_id": "task-1"},
        "requested_action": {"action": "continue", "subject": "task-1"},
    }
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    event_a = json.loads(_runner(db_a, "fixture_a", "supervisor:a", proposal).stdout)
    event_b = json.loads(_runner(db_b, "fixture_b", "supervisor:b", proposal).stdout)
    persisted_a = get_event(event_a["id"], path=db_a)
    persisted_b = get_event(event_b["id"], path=db_b)
    assert persisted_a is not None and persisted_b is not None
    for field in ("kind", "payload", "requested_action", "authority", "authority_is_grant"):
        assert persisted_a[field] == persisted_b[field]
    assert persisted_a["authority"] == {}

    artifact = tmp_path / "replacement.parstate"
    export_state(db_b, artifact)
    restored = tmp_path / "restored.db"
    restore_state(artifact, restored)
    restored_event = get_event(event_b["id"], path=restored)
    assert restored_event is not None
    for field in ("kind", "payload", "requested_action", "authority", "authority_is_grant"):
        assert restored_event[field] == persisted_a[field]


def test_provider_runner_preserves_fail_closed_no_partial_write(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    completed = _runner(
        db,
        "fixture_b",
        "supervisor:blocked",
        {"kind": "request_action", "requested_action": {"action": "run", "credentials": "ambient"}},
        check=False,
    )
    assert completed.returncode != 0
    assert list_events(path=db) == []
