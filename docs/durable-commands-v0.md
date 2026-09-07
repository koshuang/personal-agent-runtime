# Durable command resolver v0

## Goal

讓 `Chk / Fix / Continue` 從 chat shorthand 變成可由 fresh runtime 只依 Shared State 重建的 deterministic read-only command semantics。

## Core invariant

Command resolver **只讀不寫**。

它不建立 task、不 claim worker、不 retry、不 materialize、不建立 approval，也不執行 shell/network/deploy。需要 mutation 的下一步必須回到既有 ingress → approval（若需要）→ materialization → scheduler/capability/review/cost policy。

## Commands

### `Chk`

回傳：

- current reconciliation projection
- current scheduler projection
- `read_only=true`
- `mutated=false`

Repeated `Chk` 不應新增 task/run/event。

### `Continue`

只使用 durable state：reconciliation findings、queued task、lease/retry/evidence/next_action state。

- 有 durable next decision：`next_action_available`
- 沒有任何 durable work：`idle`

`Continue` 不從前一個 chat transcript 補需求，也不為了保持忙碌而建立 backlog。

### `Fix`

`Fix` 必須有 explicit durable target：`finding_type`、`task_id`，至少其一。

- 沒有 target：`needs_input`
- target 不存在：`target_not_found`
- target 不唯一：`needs_input`
- 唯一 durable finding：`fix_proposed`

`fix_proposed` 只回傳既有 finding 的 bounded proposed action；不執行 mutation。即使 target 是 write-like task，仍不能繞過 ingress / approval / materialization / capability / cost boundary。

## CLI

```text
par command chk [--worker WORKER]
par command continue [--worker WORKER]
par command fix [--worker WORKER] [--finding-type TYPE] [--task-id TASK_ID]
```

## Audit boundary

Resolver 本身不持久化 invocation，避免 read-only `Chk` 製造 canonical event noise。如果未來某個入口需要 durable command audit，應把該入口先正規化成既有 idempotent ingress envelope；resolver 不另外建立 command event store。

## Portability

相同 `.parstate` 在 fresh runtime restore 後，對相同 command 與 target 應得到相同 decision（時間相關 stale lease 狀態除外，仍由 canonical scheduler/reconciliation time semantics 決定）。
