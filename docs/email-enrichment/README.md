# Email enrichment — closing summary

Delivered across Tasks 00–03 and 02C, 12 commits, suite at 253 passed + 2 deselected.

## What exists

**Evidence layer.** Receipt emails are treated as evidence about existing transactions, never as a transaction source. `EmailSource` (`app/domain/email_source.py`) is the provider-agnostic port with a sender allowlist (`*` = unrestricted) enforced in the port, not by callers. Two Gmail adapters sit behind that port and share `app/integrations/gmail_common/` (query builder, HTML-to-text, header/sender helpers, token providers, HTTP status mapping):

- `GmailRestEmailSource` (`app/integrations/gmail_rest/`) — **primary.** `EMAIL_PROVIDER=gmail_rest`. Four hard-coded GETs on the Gmail REST API; no Developer Preview enrollment. `external_ref` stays `gmail:<id>`.
- `McpEmailSource` (`app/integrations/gmail_mcp/`) — `EMAIL_PROVIDER=gmail`. Per-call streamable-HTTP sessions and a three-tool allowlist. Requires Workspace Developer Preview enrollment; personal `@gmail.com` accounts are rejected at `search_threads`.

`FakeEmailSource` carries the same allowlist logic for tests.

**Deterministic pipeline.** `enrich_transaction` (`app/services/enrichment_service.py`) does candidate retrieval (merchant → known sender patterns → date window) → extraction (`ReceiptExtractor`: regex fallback or `ModelReceiptExtractor` with structured output and no tools) → matching (`exact_total` → `split_partial` → `date_only` → `unmatched`) → persistence. One commit per transaction; unmatched candidates are stored so they are never re-fetched. `enrich_range` drives bulk runs from `python -m app.agent.cli --enrich`. `TransactionEvidence.provider` is `source.provider_name` (`gmail_rest` vs `gmail`); `external_ref` uses the shared `gmail:<id>` space so the two adapters deduplicate.

**Data.** Four new tables: `transaction_evidence` (unique on `transaction_id, kind, external_ref`; extraction JSON, never bodies), `merchant_senders` (seed/learned/user sender patterns), `transaction_overrides` (provenance: which plan and evidence set an override), `enrichment_proposals` (open → consumed). Category precedence is **unchanged**: `set_transaction_category` writes the existing `Transaction.category_override` column, so `effective_category`, analytics, and reclassify gates needed no modification.

**Plan gate.** Two new mapping ops, `set_transaction_category` and `remove_transaction_override`, flow through the existing preview → interrupt → approve/reject/edit → apply path. A different category on an already-overridden transaction (user-set or enrichment-set) rejects the whole plan; the agent must propose a removal first, visibly. `ApplyResult` carries `overrides_set` / `overrides_removed`.

**Agent.** A fourth agent, the enricher, sits beside analyst and steward under the coordinator. It is compiled without a checkpointer, receives scope only via task string, has read tools plus `find_receipts` (which persists evidence) and ends via `submit_recommendation`, which validates every proposed override against the DB (transaction exists, evidence ids belong to it, confidence ≥ `ENRICHMENT_CONFIDENCE_THRESHOLD`) and stores a proposal. The coordinator hands the steward a proposal **id**, never ops; the steward's `load_proposal` materializes them into a normal plan. On approve, the proposal is marked consumed.

## How to operate it

Path 1 (`gmail_rest`) first. Details in `docs/email-enrichment/GMAIL-SETUP.md`.

1. Create a GCP project, enable the Gmail API, configure an External OAuth consent screen with yourself as a test user, request only `gmail.readonly`, create a Desktop OAuth client, obtain a refresh token.
2. Set `EMAIL_PROVIDER=gmail_rest`, `EMAIL_SENDER_ALLOWLIST` (start narrow: one merchant domain), and the `GMAIL_OAUTH_*` trio (or `GMAIL_ACCESS_TOKEN` / `EMAIL_MCP_ACCESS_TOKEN` for a quick test). Optionally `EXTRACTION_MODEL`; without it the regex extractor runs.
3. Run the REST spike: `python -m scripts.gmail_rest_spike --after … --before …`, review `docs/email-enrichment/spike-output-rest.md` for redaction.
4. Bulk: `python -m app.agent.cli --enrich --from … --to … --dry-run`, then a one-week real run. Smoke: `pytest -o addopts= -m live_gmail_rest`.
5. Conversational: ask the coordinator what a purchase was or to research a merchant over a range; approve or reject the resulting plan at the interrupt.

Path 2 (`gmail` MCP) is only for Workspace accounts enrolled in the Developer Preview Program. The recorded spike (`docs/email-enrichment/spike-output.md`) shows `search_threads` returning an enrollment error for a personal account while `tools/list` succeeded.

## Invariants now protecting this (all pinned by tests, listed in `docs/PROJECT-MAP.md` §9)

- Enrichment writes only evidence, senders, and proposals — never `Transaction`, mapping tables, or overrides.
- `category_override` is written only by the plan gate and the PATCH endpoint; reclassify never touches it.
- Override conflicts reject the whole plan before any write.
- Raw email content never reaches a DB row, trace, or log; enrichment spans carry redacted inputs/outputs.
- The REST client can call only four GET endpoints; the MCP adapter can invoke only three read tools; allowlist search returns empty without a network call; `fetch` re-validates the sender.
- Rule-only plan previews are byte-identical to before.
- Proposals reach the steward by id only; `submit_recommendation` enforces threshold and evidence ownership server-side.
- One checkpointer, at the coordinator only.

## Deliberate decisions to not "fix" (recorded in §14)

`transaction_overrides` is provenance, not precedence. `mark_consumed` commits after the apply commit, by design. Both Gmail adapters exist because MCP is gated on Developer Preview (and has open defects for enrolled users); REST is the primary path for any Google account. `external_ref` stays `gmail:<id>` for both so evidence deduplicates across them; `TransactionEvidence.provider` records `gmail_rest` vs `gmail` for audit. REST `messages.list` returns ids only, so search makes N+1 `messages.get?format=metadata` calls, bounded by `max_results` (≤ 10 by default) and `page_cap`. MCP Gmail dates are day-precision; REST uses `internalDate` at datetime precision. Gmail `from:` is fuzzy, so the allowlist is re-applied post-search. The confidence threshold lives in the validator, not the prompt. The enricher ends via a submit tool, not structured output.

## Deferred

Split transactions (one charge, several categories — line items are already retained in evidence for this). Attachment download / PDF receipts. Microsoft 365 or IMAP adapters (`EmailSource` is the seam; `gmail_common` is the shared Gmail layer). Scheduled bulk enrichment beyond the CLI. Chat UI on the existing thread/interrupt contract. Proposal expiry / cleanup of `open` proposals.
