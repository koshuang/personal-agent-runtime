# Roadmap

This roadmap is evidence-driven. Order may change when a pilot exposes a more important reliability or safety gap.

## North Star

A worker should be able to wake up with no previous chat transcript, determine the highest-value safe next action from durable state, execute it, persist evidence and next state, and stop safely. Another worker/session must be able to continue.

## Phase 1 — Durable Shared State

Status: **complete**

### Definition of Done

- [x] SQLite-backed `tasks / runs / events / artifacts` foundation exists.
- [x] Workers can discover and exclusively claim a task with a lease.
- [x] Workers can heartbeat, complete, fail, and persist `next_action`.
- [x] Phase 1 read-only policy exists.
- [x] The AVTime read-only pilot is executed end-to-end with objective evidence. Evidence: Issue #1 + PR #31; the same `imhere-tw/avtime-backend` task was restored by a fresh independent runner from portable Shared State with zero target/production writes and zero paid API cost.
- [x] A fresh session successfully reconstructs task state without prior transcript. Evidence: PR #28/#29 and the AVTime-target rerun in PR #31.
- [x] Reconciliation can identify stale leases, retryable/failed work, missing completion evidence, and unmaterialized `next_action`. Evidence: `par/reconcile.py`, regression tests, and the Phase 1 pilot observation that surfaced `unmaterialized_next_action` rather than silently declaring DONE.
- [x] Runtime exposes enough state for an autonomous scheduler to decide whether to resume, retry, verify, materialize a next task, execute queued work, or remain idle. Evidence: Issue #32 + PR #33 `par scheduler decide` deterministic read-only contract.
- [x] Core lifecycle tests pass in CI. Evidence: current-head Python/Go CI plus dedicated Shared State cross-runtime and AVTime target portability workflows.

### Exit evidence

Phase 1 exit evidence is complete. A real read-only repository task survived a full automation-runtime boundary through the portable Shared State contract, and the target-specific AVTime pilot was rerun successfully with the same-task recovery requirement. The runtime also exposes a deterministic read-only scheduler decision projection, so a fresh orchestrator can inspect durable state without reconstructing policy from chat history.

## Phase 2 — Reliable Continuous Worker

Status: **active**

Target capabilities:

- deterministic Reconciliation loop;
- retry policy and dead-letter state;
- idempotency keys / duplicate prevention across triggers;
- structured checkpoint / resume context;
- worker capability declarations;
- minimal execution metrics and cost/quota metadata;
- scheduled worker can repeatedly advance state without creating noise or duplicate work.

Current design track: Issue #3 adds independent worker/reviewer role separation and blind-spot review. Keep Phase 2 work evidence-driven and do not weaken existing zero-cost, credential, or side-effect boundaries.

## Phase 3 — Unified Active + Passive Control

Target capabilities:

- schedule, human command, API and webhook normalize into common events;
- `Chk / Fix / Continue` operate on durable state instead of a specific chat session;
- event ingestion is idempotent;
- human approval is represented as explicit state.

## Phase 4 — Event-driven Runtime

Target capabilities:

- GitHub events trigger relevant tasks directly;
- hourly schedule becomes watchdog/reconciliation rather than primary driver;
- missed events and stale states can self-heal.

## Phase 5 — Supervisor Adapters

Only after the runtime is reliable:

- Hermes / other orchestrators interact through runtime contracts;
- supervisors do not receive ambient company credentials;
- supervisor replacement does not require changing Shared State semantics.

## Explicitly deferred

- Dashboard / rich UI
- complex multi-agent committee structures
- premium API routing
- production automation
- broad company credential access

These are not missing features during the current phase; they are intentionally deferred.
