# Architecture Decision Record: Preservation of INV-01 and Rejection of Agent Autonomy (002)

## Status
Accepted — INV-01 Preserved; Direct Agent Autonomy Dropped

## Context
Phase 2 Story `766a6c00` ("Agent Autonomy Settings") proposed allowing users to configure the agent to skip human approval interrupts and directly apply mapping plans or transaction edits without human review.

However, Project Invariant 01 states:
> **INV-01 (No apply tool):** No apply tool exists. Propose → preview → interrupt → post-resume apply. The agent NEVER modifies data during its own reasoning loop.

Revoking INV-01 would introduce catastrophic data mutations if the LLM hallucinated, wide regex wildcards were applied blindly to financial ledgers, or unreviewed rules modified accounting categories without audit trails.

## Decision
We **preserve INV-01 strictly**:
- No `apply` tool is added to the agent toolset.
- The human-in-the-loop interrupt boundary (`POST /agent/resume`) remains non-negotiable for all database-modifying plans.
- Direct autonomous mutation by the agent is rejected.
- Any future automation will be restricted to timeout policies (e.g. auto-rejecting stale unreviewed plans after N hours) rather than autonomous execution.

## Consequences
- Guarantees financial data integrity and strict auditability.
- Complies fully with test suite invariants asserting byte-for-byte prompt integrity.
