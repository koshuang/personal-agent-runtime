# Project Engineering Profile

This file classifies the project against the shared Personal Engineering Guidelines. It is a routing aid, not a second copy of repository policy.

Shared baseline: https://github.com/koshuang/personal-engineering-guidelines

## Current maturity

**Pilot / platform build-out.**

The runtime is intentionally proving durable state, resumability, reconciliation, permission boundaries, and low-cost autonomous execution before expanding integrations or production authority.

## Primary outcome

Reduce the human execution loop by making agent work durable, resumable, bounded, observable, and safe across sessions and tools.

## Required capabilities

- durable state outside chat with explicit canonical ownership;
- bounded permissions, side effects, retries, leases, and cost;
- objective evidence before task completion;
- replay/reconciliation/watchdog behavior for interrupted or stale work;
- deterministic protocol and state-machine tests;
- checkpoint/resume semantics that do not depend on one transcript or model;
- explicit next-state transitions and closure conditions;
- observability sufficient to explain why autonomous work did or did not progress.

## Canonical sources

Read in this order:

1. `AGENTS.md`.
2. `ROADMAP.md` and current phase Definition of Done.
3. Active GitHub issues / PRs and runtime shared state.
4. Relevant architecture/protocol docs and tests.
5. Runtime evidence from actual runs.

This profile does not override the repository's autonomous-next-step or safety rules.

## Deliberate profile choices

- Phase 1 favors zero additional API spend and minimal privileges over breadth of capability.
- Production deployment, company credentials, payment systems, and destructive side effects remain outside the current profile.
- New integrations are lower priority than proving state integrity, resumability, evidence, and recovery.
- Lightweight implementation is preferred until repeated evidence justifies more infrastructure.

## Revisit when

Promote toward Production / long-lived platform only after real pilots demonstrate stable resume/recovery, permission boundaries, cost control, and observable operation without transcript dependence.
