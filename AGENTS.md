# AGENTS.md

## Mission

Build a small, model-agnostic Personal Agent Runtime that lets AI workers continue useful work across sessions while keeping cost, credentials, side effects, and state explicit.

The human should increasingly stay in the **decision loop**, not the **execution loop**.

## Canonical state

1. Runtime Shared State (`.par/runtime.db` locally in Phase 1; later Postgres/Supabase).
2. GitHub issues / PRs for implementation and durable engineering evidence.
3. `ROADMAP.md` for current phase goals and Definition of Done.
4. Notion architecture/spec documents for product intent and long-form design.

Chat/session history is never canonical state.

## Autonomous next-step rule

On every autonomous run:

1. Read this file, `ROADMAP.md`, active issues/PRs, and current runtime state.
2. Resume an already claimed/active task when safely possible.
3. Otherwise choose the highest-priority eligible queued task.
4. If no eligible task exists, run Reconciliation before concluding there is no work.
5. Reconciliation checks:
   - expired/stale leases;
   - failed runs that are retryable;
   - completed tasks missing objective evidence;
   - completed tasks with an explicit `next_action` not represented by another task/issue;
   - active PRs with failing/pending CI or unresolved review;
   - open issues that satisfy the current roadmap phase;
   - roadmap Definition of Done gaps;
   - recent runtime friction/failure that deserves a small reliability fix.
6. If Reconciliation still finds no executable task, create **at most one** smallest useful issue/task that moves the current phase toward its Definition of Done.
7. Never manufacture backlog merely to appear busy.

## New task contract

Any autonomously created task/issue must include:

- Why
- Outcome
- Scope
- Acceptance Criteria
- Non-goals
- Risk / permission tier
- Evidence required
- Expected next-state transition

A brainstorm without a closure condition is not an executable task.

## Priority

Prefer, in order:

1. Safety / credential / cost boundary failures.
2. State corruption, duplicate execution, lost work, or unrecoverable sessions.
3. Current phase Definition of Done blockers.
4. A real pilot that validates an unproven assumption.
5. Observability and ergonomics that remove recurring manual intervention.
6. New integrations.

Do not build UI, Hermes integration, or complex multi-agent routing before the lower layers are proven by evidence.

## Execution protocol

Workers should follow:

`discover -> claim -> execute -> checkpoint/heartbeat -> evidence -> complete/fail -> persist next_action`

Before ending due to quota/tool/session limits, checkpoint enough durable state for a fresh worker to resume without the previous transcript.

## Safety policy

Phase 1 default is zero additional API spend and minimal privileges.

- Do not use company AWS credentials.
- Do not access production databases or Stripe.
- Do not deploy to production.
- Do not infer permission merely because a credential exists in the environment.
- The AVTime Phase 1 pilot is read-only unless an independent product issue explicitly authorizes writes.
- Prefer reversible changes, branches, PRs, tests, and objective evidence.

## Merge discipline

For this repo, implementation should normally go through a scoped branch + PR once the bootstrap phase is over. Verify tests, CI, acceptance criteria, reviews, and mergeability before merge. Do not claim DONE without evidence.

## Language

GitHub prose defaults to Traditional Chinese. Code identifiers and necessary technical terms may remain English.
