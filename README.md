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

### Current implementation track

Phases 1–4 are complete; Phase 5 (Supervisor Adapters) is active. The Go runtime already provides a bounded local MCP / HTTP execution path with persisted state, an automatic deterministic `echo` worker, verification, and result artifacts. Issue #14 remains the separate external-integration boundary for stable HTTPS / ChatGPT Developer Mode evidence.

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

From current `main`:

```bash
go mod tidy
go run ./cmd/server
```

Defaults:

- API: `http://127.0.0.1:8080`
- MCP: `http://127.0.0.1:8080/mcp`
- SQLite: `.par/runtime-go.db`
- default bounded worker: deterministic local `echo`
- default cost ceiling: `max_cost_usd=0`

A submitted task is executed automatically by the local worker, deterministically verified, and completes with durable result evidence such as `worker-result.json`. Restart the server with the same `PAR_DB` and query the same task ID to verify persistence.

The default checks prove the bounded local `echo` worker path. Dedicated checks that instantiate `NewReadOnlyWorkspaceWorker` separately prove the bounded local workspace-worker path. Neither establishes a provider-backed worker, public deployment, or ChatGPT external connectivity. See [`docs/mcp-mvp-local-test.md`](./docs/mcp-mvp-local-test.md) for reproducible HTTP, MCP, result, and restart checks; external stable-HTTPS / Developer Mode validation remains tracked by [Issue #14](https://github.com/koshuang/personal-agent-runtime/issues/14).

## Existing Python Phase 1 runtime

The original shared-state prototype is still valid and should be preserved while the MCP/API vertical slice is built.

Python 3.11+ is supported. From a fresh clone, install the package and its declared test dependencies in an isolated virtual environment, then run the complete Python suite:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
pytest -q
```

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
- [Issue #14 — remote MCP stable-HTTPS / ChatGPT Developer Mode verification](https://github.com/koshuang/personal-agent-runtime/issues/14)
- [ROADMAP.md](./ROADMAP.md) — current phase and exit evidence

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
