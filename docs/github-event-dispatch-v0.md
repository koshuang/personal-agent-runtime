# GitHub event dispatch v0

## Goal

證明 Phase 4 的第一條 event-driven path：一個已由可信 transport 取得的 GitHub PR event，可以 deterministic 進入既有 durable ingress / materialization pipeline，而不是建立另一套 task lifecycle。

## Trust boundary

這個 adapter **不是 public webhook server**，也不負責 webhook signature termination、GitHub App installation、secret provisioning 或 network listener。

輸入被視為 transport 已取得但仍非 authority 的 event data。任何 actor、credential presence、payload wording 都不會成為 execution grant。

## Supported events

v0 只接受：

- `pull_request`
- `pull_request_review`
- `check_run`

每個 event 必須提供：

- non-empty GitHub delivery id
- repository（`owner/name`）
- positive pull request number
- JSON object payload

未知 event 或 malformed input fail closed。

## Durable mapping

GitHub delivery id 會映射到 ingress idempotency key：

```text
github-delivery:<delivery_id>
```

Normalized ingress：

```text
source = webhook
kind   = github.<event_name>
```

Requested action 永遠是 bounded read-only PR inspection task，並包含完整 autonomous task closure contract：Acceptance Criteria、Non-goals、risk tier、evidence required、expected next-state transition。

## Safety invariant

Adapter 只 durable ingest event。它不 claim、不 execute、不 merge、不 comment、不 push、不 rerun workflow、不 deploy，也不取得 GitHub credential。

Materialization 仍由既有 `materialize_ingress_event()` 決定；後續 execution 仍受 scheduler、capability、review、cost、credential 與 approval boundaries 約束。

## Replay semantics

- Same delivery id + identical envelope：回傳同一 ingress event identity。
- Same delivery id + conflicting envelope：fail closed。
- 同 event repeated materialization：由既有 `ingress-event:<event_id>` task idempotency 保證最多一個 queued task。

## Phase 4 role

這個 slice 不替代 hourly watchdog。它只證明 GitHub 變化可以即時進入相同 durable pipeline；polling/reconciliation 仍保留為漏事件與 stale state 的 self-healing boundary。
