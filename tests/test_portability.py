from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from par.db import claim_task, create_task, get_task, init_db
from par.portability import MAX_COMPRESSION_RATIO, export_state, restore_state


def test_export_restore_preserves_task_run_event_identity_and_is_repeatable(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    artifact = tmp_path / "state.parstate"
    restored = tmp_path / "fresh" / "runtime.db"
    init_db(source)
    task = create_task(goal="inspect repo", repo="example/repo", context={"evidence": ["ok"]}, path=source)
    claimed = claim_task(task_id=task["id"], worker="worker-a", path=source)
    before = get_task(task["id"], path=source)
    assert before is not None
    run_ids = [run["id"] for run in before["runs"]]
    event_ids = [event["id"] for event in before["events"]]
    assert claimed["run_id"] in run_ids

    manifest = export_state(source, artifact)
    assert manifest["version"] == 1

    restore_state(artifact, restored)
    recovered = get_task(task["id"], path=restored)
    assert recovered is not None
    assert recovered["id"] == task["id"]
    assert recovered["goal"] == "inspect repo"
    assert [run["id"] for run in recovered["runs"]] == run_ids
    assert all(run["task_id"] == task["id"] for run in recovered["runs"])
    assert [event["id"] for event in recovered["events"]] == event_ids
    assert all(event["task_id"] == task["id"] for event in recovered["events"])

    restore_state(artifact, restored)
    recovered_again = get_task(task["id"], path=restored)
    assert recovered_again is not None
    assert recovered_again["id"] == task["id"]
    assert [run["id"] for run in recovered_again["runs"]] == run_ids
    assert [event["id"] for event in recovered_again["events"]] == event_ids


def test_export_rejects_database_and_sidecar_alias_destinations(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    init_db(source)

    with pytest.raises(ValueError, match="conflicts with SQLite state files"):
        export_state(source, source)

    alias = tmp_path / "alias.parstate"
    os.link(source, alias)
    with pytest.raises(ValueError, match="conflicts with SQLite state files"):
        export_state(source, alias)

    wal = Path(f"{source}-wal")
    wal.touch()
    with pytest.raises(ValueError, match="conflicts with SQLite state files"):
        export_state(source, wal)


def test_restore_checkpoints_existing_wal_before_replacement(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    artifact = tmp_path / "state.parstate"
    destination = tmp_path / "destination.db"
    init_db(source)
    task = create_task(goal="artifact task", path=source)
    export_state(source, artifact)

    init_db(destination)
    conn = sqlite3.connect(destination)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO tasks (id, goal, status, mode, context_json, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("old-task", "old", "pending", "read-only", "{}", 100, "2026-09-06T00:00:00Z", "2026-09-06T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    restore_state(artifact, destination)
    assert get_task(task["id"], path=destination) is not None
    assert get_task("old-task", path=destination) is None


def test_restore_fails_closed_on_corrupt_payload(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    artifact = tmp_path / "state.parstate"
    corrupt = tmp_path / "corrupt.parstate"
    init_db(source)
    create_task(goal="inspect repo", path=source)
    export_state(source, artifact)

    with zipfile.ZipFile(artifact, "r") as archive:
        manifest = archive.read("manifest.json")
    with zipfile.ZipFile(corrupt, "w") as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("runtime.db", b"not the database from the manifest")

    with pytest.raises(ValueError, match="integrity"):
        restore_state(corrupt, tmp_path / "restored.db")


def test_restore_rejects_unexpected_files(tmp_path: Path) -> None:
    artifact = tmp_path / "bad.parstate"
    manifest = {"format": "personal-agent-runtime-shared-state", "version": 1, "database": "runtime.db", "sha256": "x"}
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("runtime.db", b"x")
        archive.writestr("secret.txt", "must not be accepted")

    with pytest.raises(ValueError, match="unexpected or duplicate files"):
        restore_state(artifact, tmp_path / "restored.db")


def test_restore_rejects_high_compression_ratio_before_reading_payload(tmp_path: Path) -> None:
    artifact = tmp_path / "compressed.parstate"
    payload = b"0" * (1024 * 1024)
    manifest = {"format": "personal-agent-runtime-shared-state", "version": 1, "database": "runtime.db", "sha256": "unused"}
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("runtime.db", payload)

    with zipfile.ZipFile(artifact, "r") as archive:
        info = archive.getinfo("runtime.db")
        assert info.file_size / info.compress_size > MAX_COMPRESSION_RATIO

    with pytest.raises(ValueError, match="compression ratio"):
        restore_state(artifact, tmp_path / "restored.db")
