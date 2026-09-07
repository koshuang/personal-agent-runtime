from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import DEFAULT_DB, claim_task, complete_task, create_task, fail_task, get_task, heartbeat, init_db, next_task
from .portability import export_state, restore_state
from .reconcile import reconcile
from .review import submit_review
from .scheduler import decide


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="par")
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("reconcile")

    scheduler = sub.add_parser("scheduler")
    scheduler_sub = scheduler.add_subparsers(dest="scheduler_command", required=True)
    scheduler_sub.add_parser("decide")

    review = sub.add_parser("review")
    review_sub = review.add_subparsers(dest="review_command", required=True)
    submit = review_sub.add_parser("submit")
    submit.add_argument("task_id")
    submit.add_argument("--reviewer", required=True)
    submit.add_argument("--findings", default="[]")
    submit.add_argument("--severity", default="none")
    submit.add_argument("--evidence-gap", default="[]")
    submit.add_argument("--recommended-action", default="")
    submit.add_argument("--verdict", required=True, choices=["accepted", "rejected"])
    submit.add_argument("--provider")
    submit.add_argument("--model")

    state = sub.add_parser("state")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    export = state_sub.add_parser("export")
    export.add_argument("artifact")
    restore = state_sub.add_parser("restore")
    restore.add_argument("artifact")

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
    claim.add_argument("--role", default="worker", choices=["orchestrator", "worker", "critic", "auditor"])
    claim.add_argument("--provider")
    claim.add_argument("--model")

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

    if args.command == "reconcile":
        init_db(path)
        dump(reconcile(path=path))
        return

    if args.command == "scheduler":
        init_db(path)
        if args.scheduler_command == "decide":
            dump(decide(path=path))
        return

    if args.command == "review":
        init_db(path)
        if args.review_command == "submit":
            dump(submit_review(
                task_id=args.task_id,
                reviewer=args.reviewer,
                findings=json.loads(args.findings),
                severity=args.severity,
                evidence_gap=json.loads(args.evidence_gap),
                recommended_action=args.recommended_action,
                verdict=args.verdict,
                provider=args.provider,
                model=args.model,
                path=path,
            ))
        return

    if args.command == "state":
        artifact = Path(args.artifact)
        if args.state_command == "export":
            dump({"ok": True, "artifact": str(artifact), "manifest": export_state(path, artifact)})
        elif args.state_command == "restore":
            dump({"ok": True, "db": str(path), "manifest": restore_state(artifact, path)})
        return

    if args.task_command == "create":
        init_db(path)
        dump(create_task(goal=args.goal, repo=args.repo, mode=args.mode, priority=args.priority, context=json.loads(args.context), path=path))
    elif args.task_command == "next":
        init_db(path)
        task = next_task(path=path)
        dump(task or {"task": None})
    elif args.task_command == "claim":
        dump(claim_task(task_id=args.task_id, worker=args.worker, lease_minutes=args.lease_minutes, role=args.role, provider=args.provider, model=args.model, path=path))
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
