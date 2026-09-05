# Roadmap

This roadmap is evidence-driven. Order may change when a pilot exposes a more important reliability or safety gap.

## North Star

A worker should be able to wake up with no previous chat transcript, determine the highest-value safe next action from durable state, execute it, persist evidence and next state, and stop safely. Another worker/session must be able to continue.

## Phase 1 — Durable Shared State

Status: **active**

### Definition of Done

- [x] SQLite-backed `tasks / runs / events / artifacts` foundation exists.
- [x] Workers can discover and exclusively claim a task with a lease.
- [x] Workers can heartbeat, complete, fail, and persist `next_action`.
- [x] Phase 1 read-only policy exists.
- [ ] The AVTime read-only pilot is executed end-to-end with objective evidence.
- [ ] A fresh session successfully reconstructs the task state without prior transcript.
- [ ] Reconciliation can identify stale leases, retryable failures, missing completion evidence, and unmaterialized `next_action`.
- [ ] Runtime exposes enough state for an autonomous scheduler to decide whether to resume, retry, verify, or create one next task.
- [ ] Core lifecycle tests pass in CI.

### Exit evidence

At least one real task must survive a full session boundary. “The code exists” is not sufficient evidence.

## Phase 2 — Reliable Continuous Worker

Do not start until Phase 1 exit evidence exists.

Target capabilities:

- deterministic Reconciliation loop;
- retry policy and dead-letter state;
- idempotency keys / duplicate prevention across triggers;
- structured checkpoint / resume context;
- worker capability declarations;
- minimal execution metrics and cost/quota metadata;
- scheduled worker can repeatedly advance state without creating noise or duplicate work.

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

These are not missing features during Phase 1; they are intentionally deferred.
