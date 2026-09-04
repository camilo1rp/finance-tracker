# Email enrichment — closing summary

Delivered across Tasks 00–03, 9 commits (`enrichment(A)` … `enrichment(03B)` plus fixups), suite at 218 passing + 1 deselected live test, starting from 119.

## What exists

**Evidence layer.** Receipt emails are treated as evidence about existing transactions, never as a transaction source. `EmailSource` (`app/domain/email_source.py`) is the provider-agnostic port with a sender allowlist (`*` = unrestricted) enforced in the port, not by callers. `McpEmailSource` (`app/integrations/gmail_mcp/`) is the Gmail adapter: per-call streamable-HTTP sessions, a hard-coded three-tool allowlist (`search_threads`, `get_message`, `list_labels`) enforced before any network call, OAuth via static token or refresh-token provider. `FakeEmailSource` carries the same allowlist logic for tests.

**Deterministic pipeline.** `enrich_transaction` (`app/services/enrichment_service.py`) does candidate retrieval (merchant → known sender patterns → date window) → extraction (`ReceiptExtractor`: regex fallback or `ModelReceiptExtractor` with structured output and no tools) → matching (`exact_total` → `split_partial` → `date_only` → `unmatched`) → persistence. One commit per transaction; unmatched candidates are stored so they are never re-fetched. `enrich_range` drives bulk runs from `python -m app.agent.cli --enrich`.

**Data.** Four new tables: `transaction_evidence` (unique on `transaction_id, kind, external_ref`; extraction JSON, never bodies), `merchant_senders` (seed/learned/user sender patterns), `transaction_overrides` (provenance: which plan and evidence set an override), `enrichment_proposals` (open → consumed). Category precedence is **unchanged**: `set_transaction_category` writes the existing `Transaction.category_override` column, so `effective_category`, analytics, and reclassify gates needed no modification.

**Plan gate.** Two new mapping ops, `set_transaction_category` and `remove_transaction_override`, flow through the existing preview → interrupt → approve/reject/edit → apply path. A different category on an already-overridden transaction (user-set or enrichment-set) rejects the whole plan; the agent must propose a removal first, visibly. `ApplyResult` carries `overrides_set` / `overrides_removed`.

**Agent.** A fourth agent, the enricher, sits beside analyst and steward under the coordinator. It is compiled without a checkpointer, receives scope only via task string, has read tools plus `find_receipts` (which persists evidence) and ends via `submit_recommendation`, which validates every proposed override against the DB (transaction exists, evidence ids belong to it, confidence ≥ `ENRICHMENT_CONFIDENCE_THRESHOLD`) and stores a proposal. The coordinator hands the steward a proposal **id**, never ops; the steward's `load_proposal` materializes them into a normal plan. On approve, the proposal is marked consumed.

## How to operate it

1. Enroll a GCP project in the Workspace Developer Preview, enable `gmailmcp.googleapis.com`, create a Desktop OAuth client with `gmail.readonly`, obtain a refresh token — steps in `docs/email-enrichment/GMAIL-SETUP.md`.
2. Set `EMAIL_PROVIDER=gmail`, `EMAIL_SENDER_ALLOWLIST` (start narrow: one merchant domain), and the `GMAIL_OAUTH_*` trio (or `EMAIL_MCP_ACCESS_TOKEN` for a quick test). Optionally `EXTRACTION_MODEL`; without it the regex extractor runs.
3. Run the spike: `python -m scripts.gmail_mcp_spike`, review `docs/email-enrichment/spike-output.md` for redaction and for shape mismatches against the documented schema (display names in `sender`, time in `date`, empty `plaintextBody`). If anything mismatches, stop and send the redacted output back before touching `mapping.py`.
4. Bulk: `python -m app.agent.cli --enrich --from … --to … --dry-run`, then a one-week real run. Smoke: `pytest -o addopts= -m live_gmail`.
5. Conversational: ask the coordinator what a purchase was or to research a merchant over a range; approve or reject the resulting plan at the interrupt.

## Invariants now protecting this (all pinned by tests, listed in `docs/PROJECT-MAP.md` §9)

- Enrichment writes only evidence, senders, and proposals — never `Transaction`, mapping tables, or overrides.
- `category_override` is written only by the plan gate and the PATCH endpoint; reclassify never touches it.
- Override conflicts reject the whole plan before any write.
- Raw email content never reaches a DB row, trace, or log; enrichment spans carry redacted inputs/outputs.
- The Gmail adapter can invoke only three read tools; allowlist search returns empty without a network call; `fetch` re-validates the sender.
- Rule-only plan previews are byte-identical to before.
- Proposals reach the steward by id only; `submit_recommendation` enforces threshold and evidence ownership server-side.
- One checkpointer, at the coordinator only.

## Deliberate decisions to not "fix" (recorded in §14)

`transaction_overrides` is provenance, not precedence. `mark_consumed` commits after the apply commit, by design. Gmail dates are day-precision and RFC Message-ID is unavailable, hence `external_ref = gmail:<id>`. Gmail `from:` is fuzzy, so the allowlist is re-applied post-search. The confidence threshold lives in the validator, not the prompt. The enricher ends via a submit tool, not structured output.

## Deferred

Split transactions (one charge, several categories — line items are already retained in evidence for this). Attachment download / PDF receipts. Microsoft 365 or IMAP adapters (`McpTransport` and `EmailSource` are the seams). Scheduled bulk enrichment beyond the CLI. Chat UI on the existing thread/interrupt contract. Proposal expiry / cleanup of `open` proposals.
