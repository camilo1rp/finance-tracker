# Architecture Decision Record: Authentication Decision Gate (001)

## Status
Accepted — Option (A): Defer Authentication

## Context
The finance tracker backend currently exposes 37 HTTP endpoints with open local access. The personal ledger domain is designed for single-user or single-family operation on a trusted local network or behind a reverse proxy (e.g., VPN, Tailscale, Cloudflare Tunnel).

In the v1 MVP backlog, a client-side login screen and Zustand auth store were proposed without any backend session verification or JWT validation. A client-side guard over 37 open server endpoints is security theater and provides no actual boundary.

## Decision
We select **Option (A)**: Defer authentication honestly.
- No client-side login screen or fake auth store is built.
- The backend remains open and unauthenticated for local/single-user deployment.
- Multi-user authentication with server-side JWT / session validation is formally assigned to Phase 2 (Story `f1a72219`).
- Single-user local deployment is documented in settings and architecture notes.

## Consequences
- Eliminates security theater and misleading mock authorization guards.
- Simplifies local deployment and development workflow.
- Prepares a clean boundary for real server-side authentication in Phase 2 without needing to unwind client-side workarounds.
