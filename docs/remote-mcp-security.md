# Remote MCP security boundary

The runtime is local-first. Its default listen address is `127.0.0.1:8080`, where authentication is optional for local development.

## Remote binding policy

Direct non-loopback HTTP binding is intentionally disabled. `PAR_ADDR` must resolve to loopback (`127.0.0.1`, `::1`, or `localhost`). This prevents bearer credentials from ever being exposed over the runtime's cleartext `ListenAndServe` transport.

For remote access, keep the runtime on loopback and place a trusted HTTPS gateway or tunnel in front of it. Configure `PAR_MCP_BEARER_TOKEN` so requests forwarded by that gateway must still authenticate before reaching `/mcp` or `/v1/tasks`.

```bash
export PAR_ADDR=127.0.0.1:8080
export PAR_MCP_BEARER_TOKEN='replace-with-a-random-secret'
./personal-agent-runtime
```

Protected endpoints require:

```text
Authorization: Bearer <token>
```

This applies to `/mcp` and all `/v1/tasks` task endpoints. `/healthz` remains unauthenticated and exposes only health metadata.

Never commit the token to the repository, put it in command examples with a real value, or log it. Store it in an operator secret store or injected environment variable. Rotate it by updating the secret and restarting the runtime.

## Trusted HTTPS gateway requirements

The gateway is the only supported remote exposure path for v0.1. It must terminate TLS before forwarding traffic to the loopback runtime. It should also provide principal restriction, rate limiting and request quotas before forwarding to the runtime. The runtime bearer check remains a defense-in-depth boundary behind the gateway.

Browser-based MCP clients perform an unauthenticated CORS `OPTIONS` preflight before the authenticated request. The runtime therefore allows preflight requests through the bearer middleware so the MCP handler can validate `Origin` and return the allowed CORS headers; all non-preflight MCP/task requests still require the configured bearer token.

A temporary Cloudflare tunnel must not directly publish an unauthenticated runtime. Before treating any tunnel or hostname as persistent infrastructure, complete Issue #14's stable HTTPS, abuse protection, MCP Inspector, and ChatGPT Developer Mode validation.

## Current limits

This is single-principal static bearer authentication, not multi-tenant authorization or OAuth. It does not make arbitrary shell/network execution safe; those capabilities remain out of scope until a stronger sandbox and authorization model exist.
