from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import DEFAULT_DB, claim_task, complete_task, create_task, fail_task, get_task, heartbeat, init_db, next_task


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="par")
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command", required=True)

    create = task_sub.add_parser("create")
    create.add_argument("--goal", required=True)
    create.add_argument("--repo")
    create.add_argument("--mode", default="read-only")
    create.add_argument("--priority", type=int, default=100)
    create.add_argument("--context", default="{}", help="JSON object")

    nxt = task_sub.add_parser("next")
    nxt.add_argument("--worker", required=True)

    claim = task_sub.add_parser("claim")
    claim.add_argument("task_id")
    claim.add_argument("--worker", required=True)
    claim.add_argument("--lease-minutes", type=int, default=30)

    beat = task_sub.add_parser("heartbeat")
    beat.add_argument("task_id")
    beat.add_argument("--worker", required=True)
    beat.add_argument("--lease-minutes", type=int, default=30)

    complete = task_sub.add_parser("complete")
    complete.add_argument("task_id")
    complete.add_argument("--run-id", required=True)
    complete.add_argument("--worker", required=True)
    complete.add_argument("--summary", required=True)
    complete.add_argument("--evidence", default="[]")
    complete.add_argument("--blockers", default="[]")
    complete.add_argument("--next-action")
    complete.add_argument("--metadata", default="{}")

    fail = task_sub.add_parser("fail")
    fail.add_argument("task_id")
    fail.add_argument("--run-id", required=True)
    fail.add_argument("--worker", required=True)
    fail.add_argument("--summary", required=True)
    fail.add_argument("--blockers", default="[]")

    show = task_sub.add_parser("show")
    show.add_argument("task_id")

    return p


def main() -> None:
    args = parser().parse_args()
    path = Path(args.db)

    if args.command == "init":
        init_db(path)
        dump({"ok": True, "db": str(path)})
        return

    if args.task_command == "create":
        init_db(path)
        dump(create_task(goal=args.goal, repo=args.repo, mode=args.mode, priority=args.priority, context=json.loads(args.context), path=path))
    elif args.task_command == "next":
        init_db(path)
        task = next_task(path=path)
        dump(task or {"task": None})
    elif args.task_command == "claim":
        dump(claim_task(task_id=args.task_id, worker=args.worker, lease_minutes=args.lease_minutes, path=path))
    elif args.task_command == "heartbeat":
        heartbeat(task_id=args.task_id, worker=args.worker, lease_minutes=args.lease_minutes, path=path)
        dump({"ok": True})
    elif args.task_command == "complete":
        complete_task(task_id=args.task_id, run_id=args.run_id, worker=args.worker, summary=args.summary, evidence=json.loads(args.evidence), blockers=json.loads(args.blockers), next_action=args.next_action, metadata=json.loads(args.metadata), path=path)
        dump({"ok": True})
    elif args.task_command == "fail":
        fail_task(task_id=args.task_id, run_id=args.run_id, worker=args.worker, summary=args.summary, blockers=json.loads(args.blockers), path=path)
        dump({"ok": True})
    elif args.task_command == "show":
        dump(get_task(args.task_id, path=path) or {"task": None})


if __name__ == "__main__":
    main()
