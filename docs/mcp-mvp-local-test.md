# MCP MVP 本機驗證

目前這個切片已包含：

- 非同步 Task HTTP API
- restart-safe SQLite state
- `/mcp` MCP endpoint
- `submit_task`
- `get_task`
- `get_task_result`
- `cancel_task`

Worker Adapter、deterministic verification 與 automatic task execution 仍會沿 Issue #5 繼續補齊；因此目前不能把整個 v0.1 MVP 宣告為 DONE。

## 啟動

```bash
go mod tidy
go run ./cmd/server
```

預設：

- HTTP: `http://127.0.0.1:8080`
- MCP: `http://127.0.0.1:8080/mcp`
- SQLite: `.par/runtime-go.db`

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

## HTTP API：查詢任務

```bash
curl -sS http://127.0.0.1:8080/v1/tasks/<task_id>
```

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

每個工具都應包含 input schema 與安全 annotations。

## MCP：建立 task

```bash
curl -sS -X POST http://127.0.0.1:8080/mcp \
  -H 'content-type: application/json' \
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

## 驗證 restart-safe

1. 建立一個 task。
2. 停掉 Go server。
3. 再次執行 `go run ./cmd/server`。
4. 用同一個 `task_id` 查詢。
5. 任務仍存在即代表 state 已跨 process restart 保留。

## ChatGPT Developer Mode / 遠端 MCP boundary

v0.1 的 Runtime 本身目前**不是 public multi-tenant service**。它沒有內建 per-principal authentication、request/storage quota 或 task retention，因此不得直接以 unauthenticated public tunnel 暴露 `/mcp`。

若要讓 ChatGPT 從外部連入，必須由受信任的 access gateway / tunnel policy 提供至少：

- authentication；
- 限制只有 Kos / 明確授權 principal 可進入；
- rate limiting / request quota；
- 避免任意 public client 建立 durable task。

Runtime 仍維持 loopback listener，由 gateway 代理到 `http://127.0.0.1:8080/mcp`。MCP handler 也只允許無 `Origin` 的 server-to-server request 或 loopback browser origin，不回傳 wildcard CORS。

在這些 protection 尚未存在前，ChatGPT external connector registration 應視為**尚未完成的 integration evidence**，不能以直接 public exposure 來繞過安全 boundary。

## 自動測試

```bash
go test ./...
```

CI 會驗證 HTTP API 與 MCP 的核心行為，包括 initialize、tools/list、submit/get/result-pending/cancel、prompt size boundary、Origin policy，以及 SQLite persistence。

## 目前刻意尚未完成

- Worker Adapter
- deterministic verification
- automatic task execution
- Runtime-native public authentication / per-principal quota / retention

這些完成並具有 E2E evidence 前，不得把 Issue #5 或 v0.1 MVP 宣告為 DONE。
