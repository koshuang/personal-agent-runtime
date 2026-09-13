from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from .db import DEFAULT_DB
from .supervisor import submit_supervisor_intent

MAX_PROPOSAL_CHARS = 64 * 1024


def _read_proposal(stream: TextIO) -> dict:
    raw = stream.read(MAX_PROPOSAL_CHARS + 1)
    if len(raw) > MAX_PROPOSAL_CHARS:
        raise ValueError("supervisor proposal exceeds maximum size")
    proposal = json.loads(raw)
    if not isinstance(proposal, dict):
        raise ValueError("supervisor proposal must be a JSON object")
    return proposal


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m par.supervisor_adapter")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--supervisor-id", required=True)
    p.add_argument("--idempotency-key", required=True)
    return p


def main() -> None:
    args = parser().parse_args()
    proposal = _read_proposal(sys.stdin)
    event = submit_supervisor_intent(
        supervisor_id=args.supervisor_id,
        idempotency_key=args.idempotency_key,
        intent=proposal,
        path=Path(args.db),
    )
    print(json.dumps(event, ensure_ascii=False))


if __name__ == "__main__":
    main()
