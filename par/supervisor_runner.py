from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .db import DEFAULT_DB

SAFE_ENV_KEYS = ("PATH", "PYTHONPATH", "PYTHONHOME", "SYSTEMROOT", "WINDIR")
PROVIDER_TIMEOUT_SECONDS = 30


def sanitized_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    return {key: source[key] for key in SAFE_ENV_KEYS if key in source}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m par.supervisor_runner")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--provider", required=True)
    p.add_argument("--idempotency-key", required=True)
    return p


def main() -> None:
    args = parser().parse_args()
    proposal = sys.stdin.read()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            f"par.supervisor_providers.{args.provider}",
        ],
        input=proposal,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=sanitized_environment(),
        timeout=PROVIDER_TIMEOUT_SECONDS,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "par.supervisor_adapter",
            "--db",
            str(Path(args.db)),
            "--supervisor-id",
            args.provider,
            "--idempotency-key",
            args.idempotency_key,
        ],
        input=completed.stdout,
        text=True,
        encoding="utf-8",
        check=True,
        env=sanitized_environment(),
    )


if __name__ == "__main__":
    main()
