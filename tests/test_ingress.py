from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from par.db import connect, init_db
from par.ingress import get_event, ingest_event, list_events
from par.portability import export_state, restore_state


def test_all_sources_share_one_durable_envelope(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    for source in ("schedule", "human", "api", "webhook"):
        event = ingest_event(
            idempotency_key=f"evt-{source}",
            source=source,
            kind="continue",
            payload={"source": source},
            requested_action={"command": "Continue"},
            authority={"claims_write": True},
            path=db,
        )
        assert event["source"] == source
        assert event["status"] == "received"
        assert event["authority_is_grant"] is False
    assert len(list_events(path=db)) == 4
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_idempotent_replay_returns_same_identity_without_noise(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    kwargs = dict(
        idempotency_key="same",
        source="api",
        kind="chk",
        payload={"repo": "x"},
        requested_action={"command": "Chk"},
        authority={"requested_mode": "write"},
        path=db,
    )
    first = ingest_event(**kwargs)
    second = ingest_event(**kwargs)
    assert second["id"] == first["id"]
    assert len(list_events(path=db)) == 1

    with pytest.raises(ValueError, match="different ingress envelope"):
        ingest_event(**{**kwargs, "payload": {"repo": "different"}})


def test_invalid_envelope_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    with pytest.raises(ValueError):
        ingest_event(idempotency_key="", source="api", kind="x", path=db)
    with pytest.raises(ValueError):
        ingest_event(idempotency_key="x", source="unknown", kind="x", path=db)
    with pytest.raises(ValueError):
        ingest_event(idempotency_key="x", source="api", kind="", path=db)
    with pytest.raises(ValueError):
        ingest_event(idempotency_key="x", source="api", kind="x", payload=[], path=db)  # type: ignore[arg-type]


def test_non_finite_json_numbers_fail_closed(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    init_db(db)
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite JSON numbers"):
            ingest_event(
                idempotency_key=f"bad-{value}",
                source="api",
                kind="x",
                payload={"nested": [1, {"value": value}]},
                path=db,
            )
    assert list_events(path=db) == []


def test_portability_preserves_ingress_identity(tmp_path: Path) -> None:
    source_db = tmp_path / "source.db"
    restored_db = tmp_path / "restored.db"
    artifact = tmp_path / "state.parstate"
    init_db(source_db)
    event = ingest_event(
        idempotency_key="portable",
        source="webhook",
        kind="continue",
        payload={"n": 1},
        requested_action={"command": "Continue"},
        authority={"credential": "descriptive-only"},
        path=source_db,
    )
    export_state(source_db, artifact)
    restore_state(artifact, restored_db)
    recovered = get_event(event["id"], path=restored_db)
    assert recovered is not None
    assert recovered["id"] == event["id"]
    assert recovered["idempotency_key"] == "portable"
    assert recovered["payload"] == {"n": 1}
    assert recovered["authority_is_grant"] is False


def _cli(db: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "par", "--db", str(db), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_event_cli_ingest_show_list_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "runtime.db"
    event = _cli(
        db,
        "event",
        "ingest",
        "--idempotency-key",
        "cli-1",
        "--source",
        "human",
        "--kind",
        "continue",
        "--payload",
        '{"repo":"koshuang/personal-agent-runtime"}',
        "--requested-action",
        '{"command":"Continue"}',
        "--authority",
        '{"requested_mode":"write"}',
    )
    assert event["authority_is_grant"] is False

    shown = _cli(db, "event", "show", event["id"])
    assert shown["id"] == event["id"]
    assert shown["requested_action"] == {"command": "Continue"}

    listed = _cli(db, "event", "list")
    assert [item["id"] for item in listed["events"]] == [event["id"]]

    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
