# Worker Protocol v0

## Goal

Allow Claude Code, Codex, Hermes, scheduled jobs, and future workers to operate on the same durable state without sharing a chat transcript.

## Task lifecycle

`pending -> claimed -> completed|failed`

A claimed task has a lease. If the lease expires, another worker may reclaim it.

## Minimal worker loop

1. `python -m par task next --worker <name>`
2. Inspect task goal, repo, mode, context and policy.
3. `python -m par task claim <task_id> --worker <name>`
4. Execute only within the task's declared safety boundary.
5. Send heartbeat for long work.
6. Complete with summary, evidence, blockers and next action; or fail explicitly.

## Required result fields

- `summary`: concise statement of what was learned or accomplished.
- `evidence`: URLs, commit SHAs, PR numbers, check names, file paths, or other verifiable references.
- `blockers`: unresolved facts or dependencies only.
- `next_action`: smallest useful next step.
- `metadata`: optional token/cost/runtime metadata.

## Phase 1 safety policy

Default mode is `read-only`.

Workers MUST NOT, unless a later task explicitly grants it:

- modify a target repository;
- push, merge, deploy, rerun production jobs, or change infrastructure;
- access AWS, production database, Stripe, or company credentials;
- use paid API tokens merely because they exist in the environment.

For the first pilot, `imhere-tw/avtime-backend` may be inspected through GitHub read APIs only.

## Canonical state

The SQLite database is canonical for runtime state. Chat sessions are disposable working memory. A new session must be able to continue by reading the task and run records only.
