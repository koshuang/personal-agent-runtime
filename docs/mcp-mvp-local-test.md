# MCP MVP 本機驗證

目前這個切片先驗證非同步 Task API 與 restart-safe SQLite state；worker / verification / MCP transport 會沿 Issue #5 繼續補齊。

## 啟動

```bash
go mod tidy
go run ./cmd/server
```

預設：

- HTTP: `http://localhost:8080`
- SQLite: `.par/runtime-go.db`

可用 `PAR_ADDR` 與 `PAR_DB` 覆寫。

## 建立任務

```bash
curl -sS -X POST http://localhost:8080/v1/tasks \
  -H 'content-type: application/json' \
  -d '{"prompt":"inspect this runtime and return a short health summary"}'
```

預期立即得到 `202 Accepted` 與 `task_id`。

## 查詢任務

```bash
curl -sS http://localhost:8080/v1/tasks/<task_id>
```

## 驗證 restart-safe

1. 建立一個 task。
2. 停掉 Go server。
3. 再次執行 `go run ./cmd/server`。
4. 用同一個 `task_id` 查詢。
5. 任務仍存在即代表 API state 已跨 process restart 保留。

## 目前刻意尚未完成

- worker adapter
- deterministic verification
- MCP transport
- automatic task execution

在這些完成前，不得把 Issue #5 或 v0.1 MVP 宣告為 DONE。
