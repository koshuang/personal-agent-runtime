from __future__ import annotations

import argparse
import json
from pathlib import Path

from .approvals import decide_approval, get_approval, list_approvals, request_approval
from .capabilities import claim_task_if_eligible, declare_worker_capabilities, get_worker_capabilities
from .checkpoint import resume_context, write_checkpoint
from .commands import resolve_command
from .db import DEFAULT_DB, complete_task, create_task, fail_task, get_task, heartbeat, init_db, next_task, retry_task
from .dispatcher import dispatch_pending_events
from .ingress import get_event, ingest_event, list_events
from .materialization import materialize_ingress_event
from .metrics import metrics_summary, record_run_telemetry, validate_telemetry
from .portability import export_state, restore_state
from .reconcile import reconcile
from .review import submit_review
from .scheduler import decide
from .watchdog import watchdog_wake


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _telemetry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--duration-ms", type=int)
    parser.add_argument("--cost-usd", type=float)
    parser.add_argument("--quota-units", type=float)
    parser.add_argument("--telemetry-source")


def _json_object(value: str, *, name: str) -> dict:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="par")
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init")
    sub.add_parser("reconcile")

    metrics = sub.add_parser("metrics")
    metrics_sub = metrics.add_subparsers(dest="metrics_command", required=True)
    metrics_sub.add_parser("summary")

    scheduler = sub.add_parser("scheduler")
    scheduler_sub = scheduler.add_subparsers(dest="scheduler_command", required=True)
    scheduler_decide = scheduler_sub.add_parser("decide")
    scheduler_decide.add_argument("--worker")

    command = sub.add_parser("command")
    command_sub = command.add_subparsers(dest="runtime_command", required=True)
    for name in ("chk", "continue", "fix"):
        command_parser = command_sub.add_parser(name)
        command_parser.add_argument("--worker")
        if name == "fix":
            command_parser.add_argument("--finding-type")
            command_parser.add_argument("--task-id")

    dispatch = sub.add_parser("dispatch")
    dispatch_sub = dispatch.add_subparsers(dest="dispatch_command", required=True)
    dispatch_pending = dispatch_sub.add_parser("pending")
    dispatch_pending.add_argument("--limit", type=int, default=100)

    watchdog = sub.add_parser("watchdog")
    watchdog_sub = watchdog.add_subparsers(dest="watchdog_command", required=True)
    watchdog_wake_parser = watchdog_sub.add_parser("wake")
    watchdog_wake_parser.add_argument("--dispatch-limit", type=int, default=100)
    watchdog_wake_parser.add_argument("--worker")

    event = sub.add_parser("event")
    event_sub = event.add_subparsers(dest="event_command", required=True)
    event_ingest = event_sub.add_parser("ingest")
    event_ingest.add_argument("--idempotency-key", required=True)
    event_ingest.add_argument("--source", required=True, choices=["schedule", "human", "api", "webhook"])
    event_ingest.add_argument("--kind", required=True)
    event_ingest.add_argument("--payload", default="{}")
    event_ingest.add_argument("--requested-action", default="{}")
    event_ingest.add_argument("--authority", default="{}")
    event_show = event_sub.add_parser("show")
    event_show.add_argument("event_id")
    event_list = event_sub.add_parser("list")
    event_list.add_argument("--limit", type=int, default=100)
    event_materialize = event_sub.add_parser("materialize")
    event_materialize.add_argument("event_id")

    approval = sub.add_parser("approval")
    approval_sub = approval.add_subparsers(dest="approval_command", required=True)
    approval_request = approval_sub.add_parser("request")
    approval_request.add_argument("--idempotency-key", required=True)
    approval_request.add_argument("--subject-type", required=True)
    approval_request.add_argument("--subject-id", required=True)
    approval_request.add_argument("--action", required=True)
    approval_request.add_argument("--requested-by", required=True)
    approval_request.add_argument("--scope", default="{}")
    approval_show = approval_sub.add_parser("show")
    approval_show.add_argument("approval_id")
    approval_list = approval_sub.add_parser("list")
    approval_list.add_argument("--status", choices=["pending", "approved", "rejected", "expired"])
    approval_list.add_argument("--limit", type=int, default=100)
    approval_approve = approval_sub.add_parser("approve")
    approval_approve.add_argument("approval_id")
    approval_approve.add_argument("--decided-by", required=True)
    approval_approve.add_argument("--reason", required=True)
    approval_reject = approval_sub.add_parser("reject")
    approval_reject.add_argument("approval_id")
    approval_reject.add_argument("--decided-by", required=True)
    approval_reject.add_argument("--reason", required=True)

    worker_cmd = sub.add_parser("worker")
    worker_sub = worker_cmd.add_subparsers(dest="worker_command", required=True)
    worker_declare = worker_sub.add_parser("declare")
    worker_declare.add_argument("--worker", required=True)
    worker_declare.add_argument("--capability", action="append", default=[])
    worker_show = worker_sub.add_parser("show")
    worker_show.add_argument("--worker", required=True)

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
    create.add_argument("--required-capability", action="append", default=[])
    create.add_argument("--idempotency-key")
    create.add_argument("--max-attempts", type=int, default=3)

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

    checkpoint = task_sub.add_parser("checkpoint")
    checkpoint.add_argument("task_id")
    checkpoint.add_argument("--run-id", required=True)
    checkpoint.add_argument("--worker", required=True)
    checkpoint.add_argument("--summary", required=True)
    checkpoint.add_argument("--completed-steps", default="[]")
    checkpoint.add_argument("--remaining-steps", default="[]")
    checkpoint.add_argument("--evidence", default="[]")
    checkpoint.add_argument("--blockers", default="[]")
    checkpoint.add_argument("--resume-hint")
    checkpoint.add_argument("--metadata", default="{}")

    resume = task_sub.add_parser("resume")
    resume.add_argument("task_id")

    retry = task_sub.add_parser("retry")
    retry.add_argument("task_id")
    retry.add_argument("--actor", default="human")

    complete = task_sub.add_parser("complete")
    complete.add_argument("task_id")
    complete.add_argument("--run-id", required=True)
    complete.add_argument("--worker", required=True)
    complete.add_argument("--summary", required=True)
    complete.add_argument("--evidence", default="[]")
    complete.add_argument("--blockers", default="[]")
    complete.add_argument("--next-action")
    complete.add_argument("--metadata", default="{}")
    _telemetry_args(complete)

    fail = task_sub.add_parser("fail")
    fail.add_argument("task_id")
    fail.add_argument("--run-id", required=True)
    fail.add_argument("--worker", required=True)
    fail.add_argument("--summary", required=True)
    fail.add_argument("--blockers", default="[]")
    _telemetry_args(fail)

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

    if args.command == "metrics":
        init_db(path)
        if args.metrics_command == "summary":
            dump(metrics_summary(path=path))
        return

    if args.command == "scheduler":
        init_db(path)
        if args.scheduler_command == "decide":
            dump(decide(worker=args.worker, path=path))
        return

    if args.command == "command":
        dump(resolve_command(
            args.runtime_command,
            worker=args.worker,
            finding_type=getattr(args, "finding_type", None),
            task_id=getattr(args, "task_id", None),
            path=path,
        ))
        return

    if args.command == "dispatch":
        init_db(path)
        if args.dispatch_command == "pending":
            dump(dispatch_pending_events(limit=args.limit, path=path))
        return

    if args.command == "watchdog":
        init_db(path)
        if args.watchdog_command == "wake":
            dump(watchdog_wake(dispatch_limit=args.dispatch_limit, worker=args.worker, path=path))
        return

    if args.command == "event":
        init_db(path)
        if args.event_command == "ingest":
            dump(ingest_event(
                idempotency_key=args.idempotency_key,
                source=args.source,
                kind=args.kind,
                payload=_json_object(args.payload, name="payload"),
                requested_action=_json_object(args.requested_action, name="requested_action"),
                authority=_json_object(args.authority, name="authority"),
                path=path,
            ))
        elif args.event_command == "show":
            dump(get_event(args.event_id, path=path) or {"event": None})
        elif args.event_command == "list":
            dump({"events": list_events(limit=args.limit, path=path)})
        elif args.event_command == "materialize":
            dump(materialize_ingress_event(args.event_id, path=path))
        return

    if args.command == "approval":
        init_db(path)
        if args.approval_command == "request":
            dump(request_approval(
                idempotency_key=args.idempotency_key,
                subject_type=args.subject_type,
                subject_id=args.subject_id,
                action=args.action,
                requested_by=args.requested_by,
                scope=_json_object(args.scope, name="scope"),
                path=path,
            ))
        elif args.approval_command == "show":
            dump(get_approval(args.approval_id, path=path) or {"approval": None})
        elif args.approval_command == "list":
            dump({"approvals": list_approvals(status=args.status, limit=args.limit, path=path)})
        elif args.approval_command == "approve":
            dump(decide_approval(
                args.approval_id,
                decision="approved",
                decided_by=args.decided_by,
                reason=args.reason,
                path=path,
            ))
        elif args.approval_command == "reject":
            dump(decide_approval(
                args.approval_id,
                decision="rejected",
                decided_by=args.decided_by,
                reason=args.reason,
                path=path,
            ))
        return

    if args.command == "worker":
        init_db(path)
        if args.worker_command == "declare":
            dump(declare_worker_capabilities(worker=args.worker, capabilities=args.capability, path=path))
        elif args.worker_command == "show":
            dump({"worker": args.worker, "capabilities": get_worker_capabilities(worker=args.worker, path=path), "authoritative": False})
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
        context = json.loads(args.context)
        if not isinstance(context, dict):
            raise ValueError("task context must be a JSON object")
        if args.required_capability:
            existing = context.get("required_capabilities", [])
            if existing and (not isinstance(existing, list) or not all(isinstance(value, str) for value in existing)):
                raise ValueError("context required_capabilities must be a list of strings")
            context["required_capabilities"] = list(existing) + list(args.required_capability)
        dump(create_task(
            goal=args.goal,
            repo=args.repo,
            mode=args.mode,
            priority=args.priority,
            context=context,
            idempotency_key=args.idempotency_key,
            max_attempts=args.max_attempts,
            path=path,
        ))
    elif args.task_command == "next":
        init_db(path)
        task = next_task(path=path)
        dump(task or {"task": None})
    elif args.task_command == "claim":
        dump(claim_task_if_eligible(
            task_id=args.task_id,
            worker=args.worker,
            lease_minutes=args.lease_minutes,
            role=args.role,
            provider=args.provider,
            model=args.model,
            path=path,
        ))
    elif args.task_command == "heartbeat":
        heartbeat(task_id=args.task_id, worker=args.worker, lease_minutes=args.lease_minutes, path=path)
        dump({"ok": True})
    elif args.task_command == "checkpoint":
        dump(write_checkpoint(
            task_id=args.task_id,
            run_id=args.run_id,
            worker=args.worker,
            summary=args.summary,
            completed_steps=json.loads(args.completed_steps),
            remaining_steps=json.loads(args.remaining_steps),
            evidence=json.loads(args.evidence),
            blockers=json.loads(args.blockers),
            resume_hint=args.resume_hint,
            metadata=json.loads(args.metadata),
            path=path,
        ))
    elif args.task_command == "resume":
        dump(resume_context(task_id=args.task_id, path=path))
    elif args.task_command == "retry":
        dump(retry_task(task_id=args.task_id, actor=args.actor, path=path))
    elif args.task_command == "complete":
        telemetry = validate_telemetry(duration_ms=args.duration_ms, cost_usd=args.cost_usd, quota_units=args.quota_units, source=args.telemetry_source)
        complete_task(task_id=args.task_id, run_id=args.run_id, worker=args.worker, summary=args.summary, evidence=json.loads(args.evidence), blockers=json.loads(args.blockers), next_action=args.next_action, metadata=json.loads(args.metadata), path=path)
        recorded = record_run_telemetry(task_id=args.task_id, run_id=args.run_id, worker=args.worker, path=path, **telemetry)
        dump({"ok": True, "telemetry": recorded})
    elif args.task_command == "fail":
        telemetry = validate_telemetry(duration_ms=args.duration_ms, cost_usd=args.cost_usd, quota_units=args.quota_units, source=args.telemetry_source)
        fail_task(task_id=args.task_id, run_id=args.run_id, worker=args.worker, summary=args.summary, blockers=json.loads(args.blockers), path=path)
        recorded = record_run_telemetry(task_id=args.task_id, run_id=args.run_id, worker=args.worker, path=path, **telemetry)
        dump({"ok": True, "telemetry": recorded})
    elif args.task_command == "show":
        dump(get_task(args.task_id, path=path) or {"task": None})


if __name__ == "__main__":
    main()
