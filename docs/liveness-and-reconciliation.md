# Liveness & Reconciliation Contract

The runtime is considered “alive” when it can repeatedly converge from external reality + durable state toward the next safe useful action without depending on a previous chat transcript.

## Heartbeat is not autonomy

A schedule waking every hour is only a trigger. Autonomy comes from a deterministic decision loop over durable state.

Recommended loop:

```text
wake
  -> read canonical state
  -> reconcile state with reality
  -> select one highest-value eligible action
  -> claim
  -> execute
  -> persist evidence / blocker / next_action
  -> evaluate current phase DoD
  -> stop safely
```

## Reconciliation categories

### 1. Lease health

Detect claimed tasks whose lease expired. They are eligible for recovery by another worker. Preserve prior run history rather than deleting it.

### 2. Failed work

A failure must be classified before retry:

- `retryable`: transient tool/network/CI/rate-limit failure;
- `waiting_external`: external dependency or pending review;
- `human_only`: irreversible decision, credential, payment, destructive action;
- `terminal`: task is invalid or impossible under current constraints.

Blind infinite retry is forbidden.

### 3. Completion evidence

A task marked completed is suspicious when the latest run has no verifiable evidence for work that requires evidence. Reconciliation should surface it for verification rather than trusting a prose DONE.

### 4. Next-action materialization

If a completed task contains `next_action`, determine whether it is:

- already represented by another active task/issue;
- unnecessary because reality changed;
- executable now and should become one next task;
- waiting on an external condition;
- human-only.

Do not create duplicate tasks.

### 5. GitHub reality

For repositories in scope, compare durable state against real GitHub state:

- active PR / issue still exists;
- CI head is current;
- review threads are resolved or actionable;
- merge state / issue closure matches runtime state;
- no live agent claim is being duplicated.

GitHub remains execution evidence; runtime state represents orchestration state.

### 6. Roadmap gaps

When no task is active, inspect `ROADMAP.md` current-phase unchecked Definition of Done items. A DoD gap may generate one small issue only if it has a concrete test/evidence path.

## Selection score

Do not over-engineer a numeric scheduler in Phase 1. Use this ordering:

1. safety / cost / credential risk;
2. lost-state or duplicate-execution risk;
3. current active task that can be closed;
4. current Phase DoD blocker;
5. real-world pilot / validation;
6. recurring operator friction;
7. new integration.

Prefer finishing over starting.

## Stop conditions

A worker should stop cleanly when:

- no eligible useful action remains after reconciliation;
- a true human-only gate exists;
- execution would violate policy;
- quota/tool/session boundary requires checkpointing;
- the next step depends on an external condition not yet true.

Before stopping for a nonterminal reason, persist the smallest sufficient resume context.

## Backlog self-control

The system must not create work merely to stay busy.

When no work exists:

1. run Reconciliation;
2. inspect current Phase DoD;
3. create at most one smallest verifiable issue;
4. if even that would be speculative, record `healthy_idle` and stop.

`healthy_idle` is a valid state. Endless self-generated backlog is not autonomy.
