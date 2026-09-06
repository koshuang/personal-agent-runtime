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

- HTTP: `http://localhost:8080`
- MCP: `http://localhost:8080/mcp`
- SQLite: `.par/runtime-go.db`

可用 `PAR_ADDR` 與 `PAR_DB` 覆寫。

## Health check

```bash
curl -sS http://localhost:8080/healthz
```

## HTTP API：建立任務

```bash
curl -sS -X POST http://localhost:8080/v1/tasks \
  -H 'content-type: application/json' \
  -d '{"prompt":"inspect this runtime and return a short health summary"}'
```

預期立即得到 `202 Accepted` 與 `task_id`。

## HTTP API：查詢任務

```bash
curl -sS http://localhost:8080/v1/tasks/<task_id>
```

## MCP：initialize

```bash
curl -sS -X POST http://localhost:8080/mcp \
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
curl -sS -X POST http://localhost:8080/mcp \
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
curl -sS -X POST http://localhost:8080/mcp \
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

## Cloudflare Tunnel / ChatGPT Developer Mode

若本機 `8080` 已透過 Cloudflare Tunnel 暴露，例如：

```text
https://<your-tunnel>.trycloudflare.com
```

ChatGPT 自訂 MCP / Plugin 的 endpoint 應填：

```text
https://<your-tunnel>.trycloudflare.com/mcp
```

不要填 `/healthz` 或 `/v1/tasks`。

在 ChatGPT Scan Tools 前，先確認：

```bash
curl -sS https://<your-tunnel>.trycloudflare.com/healthz
```

以及用上面的 initialize / tools/list payload 對公開 `/mcp` 執行一次。

OpenAI 官方建議以 Streamable HTTP MCP endpoint（通常為 `/mcp`）先完成 initialization、tool list、tool call、schema、annotations 與 error 驗證，再接 ChatGPT Developer Mode。

## 自動測試

```bash
go test ./...
```

CI 會驗證 HTTP API 與 MCP 的核心行為，包括 initialize、tools/list、submit/get/result-pending/cancel，以及 SQLite persistence。

## 目前刻意尚未完成

- Worker Adapter
- deterministic verification
- automatic task execution

這些完成並具有 E2E evidence 前，不得把 Issue #5 或 v0.1 MVP 宣告為 DONE。
