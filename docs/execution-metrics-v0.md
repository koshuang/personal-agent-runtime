# Execution Metrics v0

Phase 2 uses a provider-neutral execution telemetry contract stored inside each run's durable `metadata_json.execution_telemetry`.

Fields:

- `duration_ms`: finite non-negative integer; when omitted for a finished run, Runtime derives it deterministically from `started_at` and `finished_at`.
- `cost_usd`: finite non-negative number. Missing means **unknown**, while explicit `0` means known zero cost.
- `quota_units`: finite non-negative provider-neutral usage quantity. Missing means **unknown**.
- `source`: optional non-empty evidence label describing where the telemetry came from.

Telemetry is append-once for a finished run. It is evidence, not an authorization mechanism. Recording `cost_usd=0` or any quota value does not grant paid-provider, credential, production, or side-effect authority.

CLI examples:

```bash
par task complete <task-id> --run-id <run-id> --worker worker-a --summary done \
  --cost-usd 0 --quota-units 0 --telemetry-source deterministic

par metrics summary
```

`par metrics summary` is read-only and keeps unknown values separate from explicit zero values. `.parstate` portability preserves telemetry because it is stored with the run's durable metadata.
