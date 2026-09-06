# Remote MCP security boundary

The runtime is local-first. Its default listen address is `127.0.0.1:8080`, where authentication is optional for local development.

## Direct non-loopback binding

If `PAR_ADDR` is changed to a non-loopback address, the process refuses to start unless `PAR_MCP_BEARER_TOKEN` is set.

```bash
export PAR_ADDR=0.0.0.0:8080
export PAR_MCP_BEARER_TOKEN='replace-with-a-random-secret'
./personal-agent-runtime
```

Protected endpoints require:

```text
Authorization: Bearer <token>
```

This applies to `/mcp` and all `/v1/tasks` task endpoints. `/healthz` remains unauthenticated and exposes only health metadata.

Never commit the token to the repository, put it in command examples with a real value, or log it. Store it in an operator secret store or injected environment variable. Rotate it by updating the secret and restarting the runtime.

## Preferred remote deployment

For durable remote access, keep the runtime bound to loopback and place a trusted HTTPS gateway in front of it. The gateway should provide TLS termination, authentication/principal restriction, rate limiting and request quotas before forwarding to the runtime. The runtime bearer check is defense in depth and a safe fail-closed boundary if direct non-loopback binding is needed.

A temporary Cloudflare tunnel must not directly publish an unauthenticated runtime. Before treating any tunnel or hostname as persistent infrastructure, complete Issue #14's stable HTTPS, abuse protection, MCP Inspector, and ChatGPT Developer Mode validation.

## Current limits

This is single-principal static bearer authentication, not multi-tenant authorization or OAuth. It does not make arbitrary shell/network execution safe; those capabilities remain out of scope until a stronger sandbox and authorization model exist.
