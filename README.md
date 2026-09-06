# Personal Agent Runtime

A small, model-agnostic runtime for persistent AI work across ChatGPT/MCP, Claude Code, Codex, scheduled jobs, CLI workers, and future providers.

The goal is simple:

> A human gives a goal. The runtime persists state, chooses a safe worker, executes, verifies the result, and exposes durable evidence so another session can continue without the previous chat transcript.

## Start here — for AI agents

If you are an AI agent opening this repository, **do not start coding immediately**.

Read these in order:

1. [`AGENTS.md`](./AGENTS.md) — execution rules, safety boundaries, autonomous next-step policy.
2. [`ROADMAP.md`](./ROADMAP.md) — current phase, Definition of Done, deferred work.
3. Active GitHub issues / PRs — current executable work and acceptance criteria.
4. Relevant docs under [`docs/`](./docs/) — protocol and architecture details.

Canonical state is GitHub + runtime durable state + roadmap/spec docs. **Chat history is not canonical state.**

### Current main implementation track

The current priority is **Issue #5: v0.1 MCP / async task execution vertical slice**.

Target end-to-end flow:

```text
ChatGPT / MCP client
        ↓
MCP / HTTP API
        ↓
Async Task
        ↓
Persisted State
        ↓
Worker
        ↓
Deterministic Verification
        ↓
Verified Result + Evidence
```

Current working PR: **#6 — Go async task API bootstrap**.

Unless a higher-priority safety/reliability blocker appears, continue this track instead of creating a parallel runtime.

## Definition of done for the v0.1 MVP

The MVP is **not done** when the API compiles.

It is done only when a client can:

1. submit a real task;
2. disconnect;
3. the task survives process restart;
4. a worker executes it;
5. verification determines whether the result is acceptable;
6. the final result can be queried later;
7. objective evidence explains why the task was marked complete;
8. default policy incurs no paid API cost.

A worker saying `DONE` is not evidence.

## Current architecture direction

- **Go**: API / MCP-facing runtime path.
- **SQLite**: v0.1 persisted state.
- **Filesystem artifacts**: logs, diffs, reports, screenshots, structured evidence.
- **Provider-neutral workers**: adapters should not bind the task API to one LLM vendor.
- **Free-first routing**: deterministic tools and existing/free quota before paid APIs.
- **Verification before completion**: tests, lint, schema checks, exit codes, artifact checks, or explicit review.

The existing Python runtime remains useful as the Phase 1 durable-state prototype. Do not delete or rewrite it merely to make the repository "all Go".

## Local test — current Go API slice

Checkout the current implementation branch:

```bash
git fetch
git checkout feat/issue-5-go-mcp-mvp
```

Start the API:

```bash
go mod tidy
go run ./cmd/server
```

Defaults:

- API: `http://localhost:8080`
- SQLite: `.par/runtime-go.db`

Health check:

```bash
curl -sS http://localhost:8080/healthz
```

Create a task:

```bash
curl -sS -X POST http://localhost:8080/v1/tasks \
  -H 'content-type: application/json' \
  -d '{"prompt":"inspect this runtime and return a short health summary"}'
```

Query it:

```bash
curl -sS http://localhost:8080/v1/tasks/<task_id>
```

Restart the server and query the same ID again. The task should still exist.

See [`docs/mcp-mvp-local-test.md`](./docs/mcp-mvp-local-test.md) for the current verification steps.

## Existing Python Phase 1 runtime

The original shared-state prototype is still valid and should be preserved while the MCP/API vertical slice is built.

```bash
python -m par init
python -m par task create \
  --goal "Inspect avtime-backend open PRs, CI, reviews and blockers" \
  --repo imhere-tw/avtime-backend \
  --mode read-only
python -m par task next --worker codex
```

See [`docs/worker-protocol-v0.md`](./docs/worker-protocol-v0.md) for the shared worker contract.

## AI execution loop

An autonomous worker should generally follow:

```text
discover
→ read rules + active issue/PR
→ resume existing work if possible
→ otherwise choose highest-priority eligible task
→ claim / execute / checkpoint
→ persist evidence
→ verify
→ complete or fail explicitly
→ persist next_action
→ stop safely
```

If no obvious task exists, run reconciliation before creating anything new. Never manufacture backlog just to remain busy.

## Safety / cost boundary

Default posture:

- no production deployment;
- no company AWS credentials;
- no production DB or Stripe access;
- no destructive operations without explicit authorization;
- no paid API dependency by default;
- prefer branches, PRs, reversible changes, tests, and objective evidence.

For the current MVP, `max_cost_usd = 0` should be treated as a hard default unless a later explicit decision changes it.

## Key links

- [`AGENTS.md`](./AGENTS.md)
- [`ROADMAP.md`](./ROADMAP.md)
- [Issue #5 — v0.1 MCP / async task vertical slice](https://github.com/koshuang/personal-agent-runtime/issues/5)
- [PR #6 — Go async task API bootstrap](https://github.com/koshuang/personal-agent-runtime/pull/6)

## What not to build yet

Do not prioritize these before the current vertical slice has end-to-end evidence:

- Kubernetes / distributed workers
- Redis queue
- complex multi-agent swarm
- rich dashboard
- premium-model routing
- broad production integrations
- separate parallel runtimes

The repository should evolve from proven evidence, not architecture ambition alone.
