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

Status: **complete**

### Definition of Done

- [x] Deterministic Reconciliation loop identifies stale/interrupted/failed/evidence-gap/next-action states.
- [x] Retry policy is bounded and exhausted work enters dead-letter state. Evidence: Issue #38 + PR #39.
- [x] Idempotency keys prevent duplicate task creation across repeated triggers.
- [x] Structured checkpoint / resume context survives fresh sessions. Evidence: Issue #41 + PR #42.
- [x] Worker capability declarations make task eligibility deterministic without expanding authority. Evidence: Issue #44 + PR #47.
- [x] Independent worker / critic role separation prevents self-review closure. Evidence: Issue #3 + PR #35.
- [x] Minimal execution telemetry records duration / cost / quota evidence while preserving unknown-vs-zero semantics. Evidence: Issue #48 + PR #52.
- [x] Scheduled workers can repeatedly advance durable state across fresh automation runtimes without duplicate/noise. Evidence: Issue #53 + PR #54 dedicated repeated-advancement workflow.
- [x] Portable Shared State, canonical CI, and AVTime portability continue to pass throughout Phase 2 changes.

### Exit evidence

Phase 2 exit evidence is complete. PR #54 proved a chain across multiple fresh GitHub Actions runtimes: seed → materialize successor → complete with persisted next action → materialize final successor → complete → idle → idle. Portable `.parstate` carried identity between runs, deterministic successor idempotency prevented duplication, and terminal counts remained exactly `tasks=3, runs=3, events=9` across repeated idle wake-ups. Reconciliation was also hardened so a completed persisted successor remains evidence that a parent `next_action` was already materialized rather than recreating the same task.

## Phase 3 — Unified Active + Passive Control

Status: **complete**

### Definition of Done

- [x] Schedule, human command, API and webhook normalize into one provider-neutral durable ingress envelope. Evidence: Issue #56 + PR #57.
- [x] Ingress replay is idempotent and descriptive authority never becomes execution permission. Evidence: Issue #56 + PR #57.
- [x] Human approval is represented as explicit scoped durable state with immutable approve/reject evidence. Evidence: Issue #58 + PR #59/#60.
- [x] Safe ingress can deterministically materialize at most one queued task; gated actions fail closed unless approved evidence exactly matches subject/action/scope. Evidence: Issue #61 + PR #62.
- [x] `Chk / Fix / Continue` operate on durable state rather than a specific chat session, remain read-only, and do not manufacture backlog or bypass downstream gates. Evidence: Issue #63 + PR #64.
- [x] Portable Shared State preserves ingress, approval, materialization, and command outcomes across fresh runtimes.
- [x] Exact-head CI, AVTime portability, Shared State recovery, and repeated scheduled advancement remain green through Phase 3 changes.

### Exit evidence

Phase 3 exit evidence is complete. A fresh runtime can reconstruct provider-neutral ingress from schedule/human/API/webhook sources, evaluate explicit scoped approval evidence, deterministically decide whether one ingress event may become one queued task, and interpret `Chk / Fix / Continue` from Shared State without the previous chat transcript. The command layer is deliberately read-only; write-like or otherwise gated actions still return to the ingress → approval → materialization → scheduler/capability/review/cost boundaries instead of gaining authority from command wording.

Issue #14 remote MCP stable-HTTPS / ChatGPT Developer Mode verification remains a separate human-only boundary until `agent.koshuang.com` and external credentials/gateway configuration are available. It is not Phase 3 exit evidence and is not implicitly completed by this roadmap transition.

## Phase 4 — Event-driven Runtime

Status: **active**

Target capabilities:

- GitHub events trigger relevant tasks directly;
- hourly schedule becomes watchdog/reconciliation rather than primary driver;
- missed events and stale states can self-heal.

Current direction: define the smallest provider-neutral event-dispatch contract first, then prove one bounded GitHub event path end to end. Preserve the existing ingress, approval, idempotency, capability, review, cost, and credential boundaries; do not introduce arbitrary shell/network execution or production automation as part of this phase transition.

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
