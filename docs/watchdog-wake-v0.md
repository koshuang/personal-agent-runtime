# Watchdog wake v0

## Goal

讓 hourly schedule 從 primary orchestration 降級為 recovery/watchdog。單次 wake-up 只做 bounded missed-event dispatch，然後從更新後 Shared State 投影 reconciliation 與 scheduler next decision。

## Deterministic sequence

```text
watchdog wake
  → dispatch pending (bounded)
  → reconcile
  → scheduler decide
```

如果 dispatch 在本輪 materialize 新 task，scheduler 必須在同一次 wake-up 看到該 task。

## Safety invariant

Watchdog 不 claim task、不建立 run、不 retry、不 materialize task `next_action`、不 execute、不 shell/network、不取得 credential。

它唯一允許的 mutation 是呼叫既有 dispatcher，對 safe read-only ingress 建立 queued task，並更新 ingress terminal dispatch status。任何 write-like/gated action仍沿既有 approval/materialization/capability/review/cost/credential boundaries。

## Idle behavior

如果沒有 pending ingress、reconciliation finding 或 queued work，回 `decision=idle`。Repeated idle wake-up 不新增 task/run/event，也不 manufacture backlog。

## Bounds

`dispatch_limit` 沿用 dispatcher contract，合法範圍 1..1000；ordering 由 durable ingress `created_at, id` 決定。

## Portability

`.parstate` 在 ingest 後、dispatch 前 export，fresh runtime restore 後執行 watchdog wake，必須能補 materialize missed safe ingress，並在同輪 scheduler projection 中看到該 queued task。
