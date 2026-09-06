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
MAX_MANIFEST_BYTES = 64 * 1024
MAX_DATABASE_BYTES = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_file_if_exists(left: Path, right: Path) -> bool:
    return left.exists() and right.exists() and os.path.samefile(left, right)


def _validate_export_destination(db_path: Path, artifact_path: Path) -> None:
    protected = [db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]
    artifact_resolved = artifact_path.resolve(strict=False)
    for candidate in protected:
        if artifact_resolved == candidate.resolve(strict=False) or _same_file_if_exists(artifact_path, candidate):
            raise ValueError("portable artifact destination conflicts with SQLite state files")


def _read_entry(archive: zipfile.ZipFile, name: str, *, max_bytes: int) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > max_bytes:
        raise ValueError(f"portable artifact entry {name} exceeds size limit")
    if info.file_size > 0:
        if info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise ValueError(f"portable artifact entry {name} exceeds compression ratio limit")
    return archive.read(info)


def export_state(db_path: Path, artifact_path: Path) -> dict[str, object]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    _validate_export_destination(db_path, artifact_path)
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
        manifest = json.loads(_read_entry(archive, MANIFEST_NAME, max_bytes=MAX_MANIFEST_BYTES))
        if (
            manifest.get("format") != "personal-agent-runtime-shared-state"
            or manifest.get("version") != ARTIFACT_VERSION
            or manifest.get("database") != DB_NAME
        ):
            raise ValueError("unsupported portable artifact format or version")
        payload = _read_entry(archive, DB_NAME, max_bytes=MAX_DATABASE_BYTES)
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

        # Restore is an offline operation. Preserve any committed WAL state before
        # removing sidecars and atomically replacing the canonical DB file.
        if db_path.exists():
            current = sqlite3.connect(db_path)
            try:
                checkpoint = current.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if not checkpoint or checkpoint[0] != 0:
                    raise RuntimeError("cannot safely checkpoint destination SQLite WAL before restore")
            finally:
                current.close()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{db_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
        os.replace(tmp_path, db_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return manifest
