# Architecture Decision Record: Multi-User Backend Prerequisite for OAuth/SSO (003)

## Status
Deferred to Multi-User Backend Milestone

## Context
Phase 2 Story `f1a72219` ("OAuth / SSO Authentication") specifies integrating OAuth2 / OIDC providers (Google, GitHub, etc.) for authenticating end users.

Currently, the SQLite / Postgres schema is single-tenant: accounts, transactions, mappings, and artifacts have no tenant isolation key (`organization_id` or `user_id` foreign key on accounts). All registered owners share the unified ledger.

## Decision
OAuth / SSO integration is formally gated behind the Multi-User Backend Schema Migration:
1. Multi-tenant schema migration: add `user_id` or `tenant_id` to `Account`, `AnalysisArtifact`, and checkpoint state tables.
2. Fast-API middleware: JWT validation via JWKS, injecting `user_id` into database sessions and queries.
3. Client-side integration: NextAuth.js or custom OAuth PKCE flow using standard Authorization Code exchange.

Until the multi-user database migration is scheduled, the application remains operating under ADR-001 (single-user trusted local deployment).
