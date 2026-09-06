# Shared State Portability v0

Phase 1 的 portability artifact 是一個 `.parstate` ZIP，目的只是在獨立 automation runtime 之間搬移 canonical SQLite Shared State，不是 cloud storage 或 database replication。

## Contract

Artifact 只允許兩個 entries：

- `runtime.db`：由 SQLite online backup API 產生的一致 snapshot。
- `manifest.json`：包含 `format`、`version`、database filename 與 SHA-256。

目前 version 為 `1`。Restore 時必須同時驗證 exact entry allowlist（包含 duplicate entry 拒絕）、format/version/database filename、SHA-256 與 SQLite `PRAGMA integrity_check`；任何不相容或損壞都 fail closed。

## CLI

```bash
python -m par --db .par/runtime.db state export state.parstate
python -m par --db .par/runtime.db state restore state.parstate
python -m par --db .par/runtime.db task show <task_id>
```

Restore 是 **offline operation**：目標 Runtime/server 不應同時開著該 database。Restore 會清除舊 database 的 WAL/SHM sidecars 後做 atomic replace，因此重複 restore 同一 artifact 不會重新建立 task/run/event/artifact identity，也不會讓舊 WAL 污染 restored snapshot。

## Security boundary

Portability layer 不讀取 ambient credentials，也不額外掃描 filesystem。Artifact 只包含 Runtime SQLite database 的內容；Phase 1 policy 禁止把 credential/secret 寫進 canonical Runtime state。若未來 Runtime schema 開始正式保存 secret，必須先新增明確 secret filtering / encryption contract，不能沿用 v0 自動匯出。

Phase 1 額外 API 成本維持 0。

## Evidence gate

Implementation/tests 只能證明 contract。Issue #16 與 ROADMAP Phase 1 的最後 closure 仍要求在真正獨立 automation runtime 中：

1. 從前一 runtime export artifact。
2. 在 fresh runtime restore。
3. 不讀前一輪 chat transcript，直接執行 `par task show <原 task_id>`。
4. 客觀確認原 status、evidence、blockers、next_action 與 identity 關聯仍存在。
