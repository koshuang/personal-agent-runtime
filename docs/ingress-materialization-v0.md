# Ingress-to-task materialization v0

## Goal

把 durable ingress event 轉成最多一個 durable task，同時維持 idempotency、least privilege 與 explicit approval boundary。

## Requested action contract

只有明確 task-shaped event 才可 materialize：

```json
{
  "type": "task",
  "action": "inspect_repository",
  "goal": "Inspect repository state",
  "repo": "koshuang/personal-agent-runtime",
  "mode": "read-only",
  "scope": {"repo": "koshuang/personal-agent-runtime"},
  "context": {},
  "required_capabilities": ["repo-read"],
  "priority": 100
}
```

`type`、`action`、`goal`、`mode` 必須明確存在；runtime 不從自然語言、`authority` 或 chat history 猜測 permission 或 execution semantics。

## Decision policy

- `mode=read-only`：可 materialize queued task，不 claim、不 execute。
- 任何非 `read-only` mode：預設 fail closed，必須有 approved scoped evidence。
- matching approval 必須同時符合：
  - `subject_type=ingress_event`
  - `subject_id=<event id>`
  - `action=<requested action>`
  - `scope` JSON 完全相等
  - `status=approved`
- pending / rejected / expired 一律不能通過。
- ingress `authority` 永遠只是 descriptive evidence，不能繞過 approval gate。

## Idempotency

Materialized task 使用：

`ingress-event:<event_id>`

同一 event 重複 materialize 會取得同一 task identity，不新增 duplicate task。一次 `event materialize` invocation 最多做一次 task materialization transition。

## Provenance

Task context 會保留：

- `source_ingress_event_id`
- `ingress_source`
- `ingress_kind`
- `materialization_action`
- `materialization_scope`
- matching `approval_id`（若需要 approval）
- 原始 safe context / required capabilities

## Safety boundary

Approved evidence 只允許建立 scoped queued task，不代表 credential grant、production access、paid-provider routing 或 worker execution authority。後續 claim / execute 仍需通過既有 capability、policy、review、retry、cost 與 credential boundaries。

此 v0 不執行 shell/network side effect，也不部署 production。
