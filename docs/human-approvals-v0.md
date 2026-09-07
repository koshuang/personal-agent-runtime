# Human Approval State v0

Phase 3 把「有人提出一個高風險要求」與「人類已明確批准該動作」分成不同 durable state。

## Contract

`approval_requests` 保存 provider-neutral approval evidence：

- `subject_type` + `subject_id`：批准綁定的 durable subject。
- `action`：被批准或拒絕的具體動作。
- `scope`：額外限制條件；必須是 finite JSON object。
- `status`：`pending / approved / rejected / expired`。
- `requested_by`：誰提出 approval request。
- `decided_by` / `decision_reason` / `decided_at`：explicit human decision evidence。

## Safety boundary

Approval 是 **scoped evidence，不是 blanket authority**。

即使 status 是 `approved`，runtime 也不得因此自行取得：

- credential；
- production access；
- paid-provider budget；
- write permission；
- arbitrary shell/network execution。

真正執行仍必須通過對應 policy/capability/credential boundary。未來 scheduler/materializer 只能把 approval 當作特定 `subject + action + scope` 的 evidence。

Read model 固定回傳：

- `approval_is_scoped_evidence=true`
- `approval_is_blanket_authority=false`

## Idempotency

Approval request 使用 caller-provided `idempotency_key`：

- same key + same request → 回傳同一 approval identity；
- same key + conflicting request → fail closed；
- 不產生 duplicate row 或額外 task/run。

## Terminal immutability

只有 `pending` 可以轉為 terminal state。`approved`、`rejected`、`expired` 一旦形成，不可 flip 或覆寫。需要重新取得授權時應建立新的 approval request，而不是修改舊 evidence。

## Current non-goals

v0 不提供 RBAC、OAuth、production authorization service、event→task auto materialization 或 side-effect execution。