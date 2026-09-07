# Event dispatcher v0

## Goal

讓 event consumer 與 hourly watchdog 共用同一個 provider-neutral、bounded、idempotent dispatcher，避免 durable ingress 在 ingest 後因 consumer crash 而永久卡住。

## Core invariant

Dispatcher **只允許自動 materialize safe read-only ingress**。

它不 claim、不 execute、不 shell/network、不呼叫 GitHub write API、不 merge/comment/rerun/deploy，也不取得 credential。任何 write-like / production / credential / paid-provider 路徑仍必須走既有 approval、materialization、capability、review、cost 與 credential boundaries。

## Contract

### `dispatch_event(event_id)`

- event 已有 canonical materialized task → `already_materialized`
- requested action 不是 `type=task` → `blocked`
- mode 不是 `read-only` → `blocked`
- read-only contract malformed → `invalid`
- eligible read-only event → materialize exactly one queued task

Materialized identity 使用既有 `ingress-event:<event_id>` idempotency namespace，不建立第二套 event↔task mapping store。

### `dispatch_pending_events(limit=N)`

- 依 durable ingress `created_at, id` deterministic ordering
- `1 <= limit <= 1000`
- bounded scan
- 每個 event 使用相同 `dispatch_event()` contract
- repeated run 對已完成 materialization 的 state 不建立新 task/event noise

## CLI

```text
par dispatch pending --limit 100
```

此 CLI 可同時被：

- event consumer 在 ingest 後立即呼叫
- hourly watchdog 定期呼叫，補救 ingest→materialize 之間的 crash gap

## Crash recovery

```text
ingest event
→ process crashes before materialization
→ .parstate / durable DB survives
→ fresh runtime
→ par dispatch pending
→ exactly one queued read-only task
```

已 materialized 的 event 在 fresh runtime 重跑仍回 `already_materialized`，不 duplicate。

## Safety boundary

Dispatcher 的「自動」只到 **queued read-only task creation** 為止。Task 是否可 claim、由哪個 worker 執行、是否需要 review、是否達 cost/quota limit，仍由既有 runtime policy 決定。
