from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from par.ingress import get_event, list_events
from par.portability import export_state, restore_state
from par.supervisor_adapter import MAX_PROPOSAL_CHARS


def _adapter(db: Path, supervisor: str, key: str, proposal: dict, *, check: bool = True):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "par.supervisor_adapter",
            "--db",
            str(db),
            "--supervisor-id",
            supervisor,
            "--idempotency-key",
            key,
        ],
        input=json.dumps(proposal),
        capture_output=True,
        text=True,
        check=check,
    )


def test_real_adapter_observe_survives_fresh_runtime_restore(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    completed = _adapter(
        db,
        "hermes",
        "supervisor:observe:1",
        {"kind": "observe", "payload": {"finding": "safe"}},
    )
    event = json.loads(completed.stdout)
    artifact = tmp_path / "runtime.parstate"
    export_state(db, artifact)

    restored = tmp_path / "fresh-runtime.db"
    restore_state(artifact, restored)
    persisted = get_event(event["id"], path=restored)

    assert persisted is not None
    assert persisted["kind"] == "supervisor.observe"
    assert persisted["payload"] == {"finding": "safe"}
    assert persisted["authority"] == {}
    assert persisted["authority_is_grant"] is False


def test_real_adapter_provider_replacement_keeps_bounded_semantics(tmp_path: Path) -> None:
    proposal = {
        "kind": "request_action",
        "payload": {"task_id": "task-1"},
        "requested_action": {"action": "continue", "subject": "task-1"},
    }
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    event_a = json.loads(_adapter(db_a, "hermes", "supervisor:a", proposal).stdout)
    event_b = json.loads(_adapter(db_b, "replacement", "supervisor:b", proposal).stdout)
    persisted_a = get_event(event_a["id"], path=db_a)
    persisted_b = get_event(event_b["id"], path=db_b)

    assert persisted_a is not None and persisted_b is not None
    for field in ("kind", "payload", "requested_action", "authority", "authority_is_grant"):
        assert persisted_a[field] == persisted_b[field]
    assert persisted_a["authority"] == {}


def test_real_adapter_escalation_fails_without_partial_durable_side_effect(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    completed = _adapter(
        db,
        "fixture",
        "supervisor:blocked",
        {
            "kind": "request_action",
            "requested_action": {"action": "run", "credentials": "ambient"},
        },
        check=False,
    )

    assert completed.returncode != 0
    assert list_events(path=db) == []


def test_real_adapter_rejects_oversized_proposal_without_durable_side_effect(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    proposal = {"kind": "observe", "payload": {"finding": "x" * MAX_PROPOSAL_CHARS}}
    completed = _adapter(db, "fixture", "supervisor:oversized", proposal, check=False)

    assert completed.returncode != 0
    assert "supervisor proposal exceeds maximum size" in completed.stderr
    assert list_events(path=db) == []
