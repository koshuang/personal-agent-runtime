# Ingress Event Contract v0

Phase 3 introduces a provider-neutral durable ingress boundary for external triggers. The contract intentionally separates **recording a request** from **granting execution authority**.

## Sources

Supported `source` values:

- `schedule`
- `human`
- `api`
- `webhook`

All sources use the same envelope and persistence path.

## Durable envelope

Each ingress event stores:

- `id`: runtime-generated stable event identity
- `idempotency_key`: caller-supplied replay identity, unique across ingress events
- `source`: provider-neutral source category
- `kind`: application-level event kind
- `payload`: JSON object containing source payload
- `requested_action`: JSON object describing what the caller is asking for
- `authority`: JSON object describing caller/request context only
- `status`: initially `received`
- timestamps

The read model always exposes `authority_is_grant=false`.

## Authority boundary

`authority` is descriptive evidence, not a permission grant. Values such as `write=true`, `production=true`, `credential=...`, `paid_provider=true`, or similar claims must not bypass task policy, human approval, credential boundaries, worker capability policy, or cost policy.

Ingress therefore does **not** automatically:

- create a task
- claim a worker
- execute work
- grant credentials
- enable production writes
- authorize paid-provider use

A later policy/materialization layer must evaluate the event independently before any executable task exists.

## Idempotency

Re-sending the exact same envelope with the same `idempotency_key` returns the original event identity and does not add another row. Reusing the same key with a different source, kind, payload, requested action, or authority fails closed.

## CLI

```bash
python -m par --db .par/runtime.db event ingest \
  --idempotency-key human-2026-09-07-continue \
  --source human \
  --kind continue \
  --payload '{"repo":"koshuang/personal-agent-runtime"}' \
  --requested-action '{"command":"Continue"}' \
  --authority '{"requested_mode":"write"}'

python -m par --db .par/runtime.db event show <event-id>
python -m par --db .par/runtime.db event list --limit 100
```

`payload`, `requested_action`, and `authority` must each be JSON objects. Invalid source values, empty idempotency keys, blank kinds, and non-object JSON fail closed.

## Portability

`ingress_events` lives in the same SQLite Shared State and is therefore included in the `.parstate` export/restore contract. Restore must preserve event ID, idempotency key, source, payload, requested action, authority, status, and timestamps.

## Non-goals for v0

- event-to-task policy mapping
- provider-specific webhook signature verification
- production HTTP ingress endpoints
- automatic human approval decisions
- remote MCP / Cloudflare setup
