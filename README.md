# Personal Agent Runtime

A minimal, model-agnostic runtime for persistent AI work across Claude Code, Codex, Hermes, scheduled jobs, and future workers.

## Phase 1 goals

- Shared state survives chat/session boundaries.
- Workers claim tasks through a common protocol.
- SQLite first; schema stays portable to Postgres/Supabase.
- Read-only pilot against `imhere-tw/avtime-backend`.
- No production access, no AWS credentials, no paid API dependency.

## Quick start

```bash
python -m par init
python -m par task create \
  --goal "Inspect avtime-backend open PRs, CI, reviews and blockers" \
  --repo imhere-tw/avtime-backend \
  --mode read-only
python -m par task next --worker codex
```

See `docs/worker-protocol-v0.md` for the shared contract.
