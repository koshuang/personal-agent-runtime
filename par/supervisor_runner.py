from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TextIO

from .db import DEFAULT_DB
from .supervisor_adapter import MAX_PROPOSAL_CHARS

SAFE_ENV_KEYS = ("PATH", "PYTHONPATH", "PYTHONHOME", "SYSTEMROOT", "WINDIR")
PROVIDER_TIMEOUT_SECONDS = 30
MAX_PROVIDER_OUTPUT_CHARS = MAX_PROPOSAL_CHARS


def sanitized_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    return {key: source[key] for key in SAFE_ENV_KEYS if key in source}


def _read_bounded_text(stream: TextIO, max_chars: int, error_message: str) -> str:
    raw = stream.read(max_chars + 1)
    if len(raw) > max_chars:
        raise ValueError(error_message)
    return raw


def _run_provider(provider: str, proposal: str) -> str:
    command = [
        sys.executable,
        "-m",
        f"par.supervisor_providers.{provider}",
    ]
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as provider_stdout, tempfile.TemporaryFile(
        mode="w+t", encoding="utf-8"
    ) as provider_stderr:
        completed = subprocess.run(
            command,
            input=proposal,
            stdout=provider_stdout,
            stderr=provider_stderr,
            text=True,
            encoding="utf-8",
            check=False,
            env=sanitized_environment(),
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
        provider_stdout.seek(0)
        output = _read_bounded_text(
            provider_stdout,
            MAX_PROVIDER_OUTPUT_CHARS,
            "supervisor provider output exceeds maximum size",
        )
        provider_stderr.seek(0)
        error_output = _read_bounded_text(
            provider_stderr,
            MAX_PROVIDER_OUTPUT_CHARS,
            "supervisor provider error output exceeds maximum size",
        )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=output,
            stderr=error_output,
        )
    return output


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m par.supervisor_runner")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--provider", required=True)
    p.add_argument("--idempotency-key", required=True)
    return p


def main() -> None:
    args = parser().parse_args()
    proposal = _read_bounded_text(
        sys.stdin,
        MAX_PROPOSAL_CHARS,
        "supervisor proposal exceeds maximum size",
    )
    provider_output = _run_provider(args.provider, proposal)
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
        input=provider_output,
        text=True,
        encoding="utf-8",
        check=True,
        env=sanitized_environment(),
    )


if __name__ == "__main__":
    main()
