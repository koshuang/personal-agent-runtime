# Worker Capability Contract v0

Phase 2 uses provider-neutral capability declarations to decide whether a worker is eligible to claim a task. Capabilities describe what a worker can do; they never grant authority.

## Task requirements

A task may put `required_capabilities` in `context_json` as a list of normalized capability names such as `repo.read` or `filesystem.read`. Existing tasks with no requirements remain eligible for any worker subject to the existing task policy.

## Worker declarations

Workers declare capabilities in durable Shared State with `par worker declare --worker <id> --capability <name>`. Re-declaration replaces the worker's previous capability set. `par worker show --worker <id>` is read-only.

Capability names are provider/model neutral. Vendor/model names may be stored elsewhere as provenance but are not the application-level eligibility contract.

## Eligibility

A worker is eligible only when every required capability is present in its durable declaration. Missing or unknown capabilities fail closed. `par scheduler decide --worker <id>` exposes required, declared, and missing capabilities. `par task claim ... --worker <id>` uses the same eligibility rule before the existing claim transition.

Tasks with an empty requirement set preserve the pre-capability behavior.

## Authority boundary

Capabilities are descriptive and `authority_expanded` is always false. They do not grant credentials, production access, paid-provider use, deployment permission, or broader side-effect policy. Authority-like capability namespaces (`credential`, `secret`, `production`, `paid`, `billing`, `admin`) are rejected so callers cannot confuse capability matching with permission grants.

The existing task `mode` and policy gates remain authoritative. For example, a worker that satisfies all capabilities for a non-read-only task still receives `requires_human=true` and `safe_to_auto_execute=false` from the scheduler.

## Examples

```bash
par worker declare --worker repo-reader \
  --capability repo.read \
  --capability filesystem.read

par task create \
  --goal "Inspect repository" \
  --required-capability repo.read

par scheduler decide --worker repo-reader
par task claim <task-id> --worker repo-reader
```

This contract intentionally does not perform model benchmarking, credential discovery, paid-provider routing, or automatic role negotiation.
