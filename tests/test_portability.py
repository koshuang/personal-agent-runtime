from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from par.db import create_task, get_task, init_db
from par.portability import export_state, restore_state


def test_export_restore_preserves_task_identity_and_is_repeatable(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    artifact = tmp_path / "state.parstate"
    restored = tmp_path / "fresh" / "runtime.db"
    init_db(source)
    task = create_task(goal="inspect repo", repo="example/repo", context={"evidence": ["ok"]}, path=source)

    manifest = export_state(source, artifact)
    assert manifest["version"] == 1

    restore_state(artifact, restored)
    recovered = get_task(task["id"], path=restored)
    assert recovered is not None
    assert recovered["id"] == task["id"]
    assert recovered["goal"] == "inspect repo"

    restore_state(artifact, restored)
    recovered_again = get_task(task["id"], path=restored)
    assert recovered_again is not None
    assert recovered_again["id"] == task["id"]


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

    with pytest.raises(ValueError, match="unexpected files"):
        restore_state(artifact, tmp_path / "restored.db")
