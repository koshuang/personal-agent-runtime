from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

ARTIFACT_VERSION = 1
DB_NAME = "runtime.db"
MANIFEST_NAME = "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_state(db_path: Path, artifact_path: Path) -> dict[str, object]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot = Path(tmpdir) / DB_NAME
        source = sqlite3.connect(db_path)
        try:
            target = sqlite3.connect(snapshot)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        manifest = {
            "format": "personal-agent-runtime-shared-state",
            "version": ARTIFACT_VERSION,
            "database": DB_NAME,
            "sha256": _sha256(snapshot),
        }
        with zipfile.ZipFile(artifact_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, DB_NAME)
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return manifest


def restore_state(artifact_path: Path, db_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(artifact_path, "r") as archive:
        entries = archive.namelist()
        if len(entries) != 2 or set(entries) != {DB_NAME, MANIFEST_NAME}:
            raise ValueError("portable artifact contains unexpected or duplicate files")
        manifest = json.loads(archive.read(MANIFEST_NAME))
        if (
            manifest.get("format") != "personal-agent-runtime-shared-state"
            or manifest.get("version") != ARTIFACT_VERSION
            or manifest.get("database") != DB_NAME
        ):
            raise ValueError("unsupported portable artifact format or version")
        payload = archive.read(DB_NAME)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != manifest.get("sha256"):
        raise ValueError("portable artifact integrity check failed")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="runtime-restore-", suffix=".db", dir=db_path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path = Path(tmp_name)
        conn = sqlite3.connect(tmp_path)
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise ValueError("restored SQLite database failed integrity_check")
        finally:
            conn.close()

        # Restore is an offline operation. Remove stale WAL sidecars from a previous
        # local database before atomically replacing the canonical DB file.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{db_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
        os.replace(tmp_path, db_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return manifest
