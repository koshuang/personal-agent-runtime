# Remote MCP validation runbook

Use this checklist only after a stable HTTPS gateway is forwarding to the runtime's loopback listener. Do not expose the runtime's cleartext listener directly to the public internet.

## Prerequisites

- runtime remains bound to `127.0.0.1`
- the gateway terminates HTTPS and forwards only to the loopback runtime
- `PAR_MCP_BEARER_TOKEN` is injected from an operator secret store or environment, never committed
- gateway-level principal restriction and distributed rate limiting are enabled
- runtime-level bearer authentication and request quota are enabled

Set local shell variables without committing values:

```bash
export MCP_URL='https://your-stable-host.example/mcp'
export PAR_MCP_BEARER_TOKEN='...'
```

## 1. Negative authentication check

An anonymous MCP request must fail before tool dispatch:

```bash
curl -i -X POST "$MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"remote-smoke","version":"1.0.0"},"capabilities":{}}}'
```

Expected: HTTP `401 Unauthorized`.

## 2. MCP Inspector initialize

The MCP Inspector CLI requires Node.js 22.19 or newer on its current v2 line.

```bash
npx @modelcontextprotocol/inspector --cli \
  --transport http \
  --server-url "$MCP_URL" \
  --header "Authorization: Bearer $PAR_MCP_BEARER_TOKEN" \
  --method initialize
```

Expected: server information, protocol version, capabilities, and instructions are returned without authentication or transport errors.

## 3. MCP Inspector tools/list

```bash
npx @modelcontextprotocol/inspector --cli \
  --transport http \
  --server-url "$MCP_URL" \
  --header "Authorization: Bearer $PAR_MCP_BEARER_TOKEN" \
  --method tools/list
```

Expected tools:

- `submit_task`
- `get_task`
- `get_task_result`
- `cancel_task`

## 4. Authenticated representative tool call

Use the read-only workspace worker for the remote validation run. Confirm `PAR_WORKER=workspace` and set `PAR_WORKSPACE_ROOT` to a deliberately non-sensitive test workspace before submitting the task.

```bash
npx @modelcontextprotocol/inspector --cli \
  --transport http \
  --server-url "$MCP_URL" \
  --header "Authorization: Bearer $PAR_MCP_BEARER_TOKEN" \
  --method tools/call \
  --tool-name submit_task \
  --tool-args-json '{"prompt":"read README.md"}'
```

Record the returned task id, then query it with `get_task` and later `get_task_result` using the same Inspector command shape and `--tool-args-json '{"task_id":"<id>"}'`.

Evidence is acceptable only when the final result is verified/completed and the route remains zero-cost/read-only.

## 5. Rate-limit evidence

For the persistent deployment, verify both boundaries independently:

1. the public gateway rejects abusive bursts according to its configured distributed policy;
2. the runtime returns HTTP `429` with `Retry-After` if the authenticated process-local quota is exhausted.

Do not weaken the runtime quota merely because an edge limit exists.

## 6. ChatGPT Developer Mode

After the Inspector checks pass, add the same stable HTTPS MCP endpoint in ChatGPT Developer Mode using the configured authentication method. Scan tools and verify that the four expected tools are discovered.

Then perform one controlled read-only task through ChatGPT and retrieve its verified result. Do not enable arbitrary shell or network execution as part of this validation.

## Evidence to attach to Issue #14

Record only non-secret evidence:

- stable hostname (no token)
- timestamp
- runtime commit SHA
- MCP Inspector version
- initialize result summary
- discovered tool names
- representative task id and terminal status
- zero-cost/read-only route evidence
- ChatGPT Developer Mode scan/connect result
- rate-limit test result

Never paste bearer tokens, gateway credentials, cookies, or authorization headers into GitHub issues or logs.
