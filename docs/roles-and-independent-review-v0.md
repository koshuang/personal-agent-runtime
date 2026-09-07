# Roles and Independent Review v0

Phase 2 的最小角色契約只有四種 role：

- `orchestrator`：選擇下一個安全動作、建立/安排工作，不直接冒充 reviewer。
- `worker`：執行 task 並產生 evidence。
- `critic`：在不同 run 中檢查 worker 結果，輸出結構化 review。
- `auditor`：透過 reconciliation 檢查缺 review、缺 evidence 或狀態不一致。

## Provider-neutral metadata

Run 的 `metadata_json` 可記錄：

- `role`
- `provider`（nullable）
- `model`（nullable）
- `cost_usd`

這只是 provenance，不綁定 Claude / Codex / Gemini，也不會自動觸發付費 API。

## Independent review gate

Task context 若包含：

```json
{"requires_independent_review": true}
```

則 worker `complete` 不會直接把 task 關成 `completed`，而是轉為 `review_pending`。Critic 必須由**不同 worker identity** 的另一個 run 提交 review。

Critic review 結構：

```json
{
  "findings": [],
  "severity": "none|low|medium|high|critical",
  "evidence_gap": [],
  "recommended_action": "...",
  "verdict": "accepted|rejected"
}
```

- `accepted` → task 才能成為 `completed`。
- `rejected` → task 進入 `review_rejected`，由 Reconciliation 回報，不自動 retry。

Critic 最少應檢查：需求遺漏、錯誤假設、安全/成本風險、evidence 充分性，以及是否存在更小、更安全的方案。

## Safety / cost

- independent review 不擴張 task 原本的 permission / side-effect boundary。
- 預設額外 API 成本仍為 0。
- 沒有第二 provider 時，可以使用另一個 subscription session/worker，或 deterministic validation；「不同 provider」不是 closure 必要條件，「不同 run / worker identity」才是。
- high-risk side effect 仍必須通過既有 policy gate；review gate 不能作為繞過權限的理由。
