# Trusted-local Codex worker v0

`PAR_WORKER=codex` is an opt-in local command adapter. It requires an explicit
`PAR_WORKSPACE_ROOT`; `echo` remains the default worker and its default maximum
cost remains `0`.

Codex is fail-closed on cost: the local operator must set a finite, positive
`PAR_CODEX_ESTIMATED_COST_USD`, and `PAR_MAX_COST_USD` must be at least that
value. This is a pre-dispatch admission estimate, not fabricated usage
telemetry; actual provider cost remains unknown unless the provider supplies it.

The adapter only starts when `PAR_CODEX_EXECUTABLE` is an absolute path to an
executable selected by the local operator. Task text never selects a binary,
shell, flags, workspace, or execution mode. The fixed invocation is:

```text
<PAR_CODEX_EXECUTABLE> exec --sandbox read-only --skip-git-repo-check \
  --output-last-message <runtime-temp-file> -- <task prompt as one argument>
```

The configured `PAR_WORKSPACE_ROOT` is canonicalized and used as the process
working directory. The process receives a minimal environment containing only
`PATH`, `HOME`, and `CODEX_HOME`; ambient tokens, proxy configuration, and
other server environment variables are not inherited. Unless `PAR_CODEX_HOME`
names an existing, dedicated local Codex home, each invocation receives a
disposable empty home which is removed afterward. Operators who opt into a
persistent Codex identity must explicitly select a dedicated home and remain
responsible for its credentials and cost boundary.

The default `PATH` contains only `/opt/homebrew/bin`, `/usr/local/bin`,
`/usr/bin`, and `/bin`, so a standard global Homebrew installation can run an
`env node`-based Codex launcher without inheriting the server environment. An
operator using a non-standard native runtime can set `PAR_CODEX_PATH`; every
entry must be an absolute directory.

Each invocation is bounded by a two-minute timeout and a 64 KiB limit per
stdout and output-message channel. Provider stderr is discarded rather than
returned or buffered, because diagnostics can contain sensitive task context
and a bounded stderr pipe could otherwise terminate a successful provider
process when it fills. Timeout, missing binary, non-zero exit, invalid output,
and output-limit failures return an error to the existing Runner; the task
becomes `failed` and never receives a completed result. A task canceled through
the Task API keeps its `canceled` status while cancellation propagates to the
worker process. The process is placed in a dedicated process group so
cancellation terminates its descendants.

On success the ordinary verified `worker-result.json` artifact contains
provider, worker, fixed command class, `version: unknown`, workspace policy,
exit status, and cost/quota telemetry. Unknown actual cost or quota remains
`null`; the adapter never fabricates usage data or grants paid-provider,
credential, network, or write authority.

This is local-only trusted execution. It does not enable the adapter through
public MCP and does not change the HTTP/MCP Task API, Shared State, retry, or
verification contracts.
