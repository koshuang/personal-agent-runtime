# MCP MVP 本機驗證

目前這個切片已包含：

- 非同步 Task HTTP API
- restart-safe SQLite state
- `/mcp` MCP endpoint
- `submit_task`
- `get_task`
- `get_task_result`
- `cancel_task`
- `list_documents`
- `extract_pdf`（受限 local document root、password-gated extraction）

`echo` Worker Adapter、deterministic verification 與 automatic task execution 已可在本機 bounded path 驗證。預設檢查證明 bounded local `echo` worker path；另有直接使用 `NewReadOnlyWorkspaceWorker` 的專用檢查，證明 bounded local workspace-worker path。兩者都不代表 provider-backed worker、public deployment 或 ChatGPT external connector 已完成。遠端 stable HTTPS / Developer Mode 驗證仍由 Issue #14 追蹤。

## 啟動

```bash
go mod tidy
go run ./cmd/server
```

預設：

- HTTP: `http://127.0.0.1:8080`
- MCP: `http://127.0.0.1:8080/mcp`
- SQLite: `.par/runtime-go.db`
- Document inbox: `PAR_DOCUMENT_ROOT`，預設為 `.par/inbox`

可用 `PAR_ADDR` 與 `PAR_DB` 覆寫。預設刻意只綁定 loopback；不要把 `PAR_ADDR` 改成 public listener，除非前方已有明確的 authentication、request/storage quota、rate limiting 與 retention policy。

## Health check

```bash
curl -sS http://127.0.0.1:8080/healthz
```

## HTTP API：建立任務

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/tasks \
  -H 'content-type: application/json' \
  -d '{"prompt":"inspect this runtime and return a short health summary"}'
```

預期立即得到 `202 Accepted` 與 `task_id`。

## HTTP API：查詢任務與結果

```bash
curl -sS http://127.0.0.1:8080/v1/tasks/<task_id>
```

本機預設 worker 為 deterministic `echo`。等待任務完成後，預期 task 顯示 `completed` 狀態；runner 會在完成前驗證 worker result。worker 為 `echo`，成本上限維持 `max_cost_usd=0`，並會持久化 durable result artifact（例如 `worker-result.json`）。`get_task_result` 與 `GET /v1/tasks/{id}/result` 回傳 artifact reference，不會傳送 artifact bytes；要讀取 bytes，需從本機 `PAR_ARTIFACTS` store（預設 `.par/artifacts`）讀取該 reference。這是 local bounded evidence，不是外部模型 provider 的執行證據。

## MCP：initialize

```bash
curl -sS -X POST http://127.0.0.1:8080/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"initialize",
    "params":{
      "protocolVersion":"2025-06-18",
      "capabilities":{},
      "clientInfo":{"name":"manual-test","version":"1.0"}
    }
  }'
```

預期 result 會包含：

- `protocolVersion`
- `serverInfo.name = personal-agent-runtime`
- `capabilities.tools`
- server instructions

## MCP：列出工具

```bash
curl -sS -X POST http://127.0.0.1:8080/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

預期看到：

- `submit_task`
- `get_task`
- `get_task_result`
- `cancel_task`
- `list_documents`
- `extract_pdf`

每個工具都應包含 input schema 與安全 annotations。

## MCP：建立 task

```bash
curl -sS -X POST http://127.0.0.1:8080/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{
    "jsonrpc":"2.0",
    "id":3,
    "method":"tools/call",
    "params":{
      "name":"submit_task",
      "arguments":{"prompt":"verify MCP bridge"}
    }
  }'
```

從 `structuredContent.task_id` 取得 task id，再呼叫 `get_task` / `get_task_result` / `cancel_task`。

## MCP：解密並抽取 local PDF

`extract_pdf` 只允許讀取 `PAR_DOCUMENT_ROOT` 底下的相對路徑，不接受絕對路徑或 traversal。PDF password 只存在於當次 request memory，不會寫入 task state、artifact 或 log。

先列出 inbox 內的 PDF，取得相對 file handle：

```bash
curl -sS -X POST http://127.0.0.1:8080/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_documents","arguments":{}}}'
```

```bash
mkdir -p .par/inbox
cp /path/to/statement.pdf .par/inbox/statement.pdf

curl -sS -X POST http://127.0.0.1:8080/mcp \
  -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"extract_pdf","arguments":{"path":"statement.pdf","password":"<password>"}}}'
```

回應包含 bounded `text`、`path`、`pages`、`bytes`、`sha256` 與 `encrypted` metadata。錯誤 password 會 fail closed。不要把真實 password 放在 task prompt、shell history 或 GitHub issue。

## 驗證 restart-safe

1. 建立一個 task。
2. 重複查詢 task，直到 `status=completed`，並確認 completed result 與 artifact reference 可查詢。
3. 停掉 Go server。
4. 使用與首次啟動相同的工作目錄再次執行 `go run ./cmd/server`；若設定 `PAR_DB` 或 `PAR_ARTIFACTS`，請分別沿用相同值。
5. 用同一個 `task_id` 查詢，並確認 completed result 與 artifact reference 仍可查詢。從本機 `PAR_ARTIFACTS` store 讀取 artifact bytes；HTTP result endpoint 不會傳送這些 bytes。這代表 task state、執行結果與 artifact file 已跨 process restart 保留。

## ChatGPT Developer Mode / 遠端 MCP boundary

v0.1 的 Runtime 本身目前**不是 public multi-tenant service**。它沒有內建 per-principal authentication、request/storage quota 或 task retention，因此不得直接以 unauthenticated public tunnel 暴露 `/mcp`。

若要讓 ChatGPT 從外部連入，必須由受信任的 access gateway / tunnel policy 提供至少：

- authentication；
- 限制只有 Kos / 明確授權 principal 可進入；
- rate limiting / request quota；
- 避免任意 public client 建立 durable task。

Runtime 仍維持 loopback listener，由 gateway 代理到 `http://127.0.0.1:8080/mcp`。MCP handler 也只允許無 `Origin` 的 server-to-server request 或 loopback browser origin，不回傳 wildcard CORS。

在這些 protection 尚未存在前，ChatGPT external connector registration 應視為**尚未完成的 integration evidence**，不能以直接 public exposure 來繞過安全 boundary。這個剩餘驗證由 [Issue #14](https://github.com/koshuang/personal-agent-runtime/issues/14) 追蹤；本機 `echo` 成功不可用來關閉它。

## 自動測試

```bash
go test ./...
```

CI 會驗證 HTTP API 與 MCP 的核心行為，包括 initialize、tools/list、submit/get/result-pending/cancel、prompt size boundary、Origin policy，以及 SQLite persistence。

## 目前刻意尚未完成

- provider-backed natural-language worker
- stable HTTPS + ChatGPT Developer Mode external validation（Issue #14）
- Email attachment connector / inbox automation
- OCR for scanned-image PDFs
- Runtime-native public authentication / per-principal quota / retention

本機 deterministic `echo` execution、verification、result artifact 與 restart persistence 已有 bounded evidence；以上 deferred scope 不得因本機成功而被宣告完成。