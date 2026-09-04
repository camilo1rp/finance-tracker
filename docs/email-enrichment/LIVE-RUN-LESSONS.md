# Email enrichment — live-run lessons (first real mailbox, 2026-09-03/04)

Everything below was found by running against a real Gmail account after the suite was green at 217 tests. Each item passed its tests and failed reality; each is now pinned by a test and recorded in `docs/PROJECT-MAP.md` §14. Read this before changing retrieval, extraction, or matching.

## Outcome

One real purchase (Best Buy, 2026-08-27, $324.74) went end to end: sender learned from a subject search → receipt fetched → total matched by arithmetic → model read "television / electronics" → enricher proposed `Electronics` (a category the taxonomy did not have) → steward preview → human approve at the interrupt → `category_override` set with provenance to the evidence row. Suite at 295 + 2 live-deselected afterwards.

## Lessons, in the order they were hit

| # | What happened | Root cause | Fix | Where |
|---|---|---|---|---|
| 1 | Gmail MCP `search_threads` returned an enrollment error; `tools/list` worked. | Google's Gmail MCP server requires Workspace Developer Preview enrollment, which excludes personal `@gmail.com` accounts. Separately, the tool has open defects since 2026-04 for enrolled users. | Added `GmailRestEmailSource` behind the same `EmailSource` port (`EMAIL_PROVIDER=gmail_rest`), sharing query/text/auth via `gmail_common`. MCP adapter kept for Workspace accounts. | 02C |
| 2 | `--enrich` crashed: `relation "merchant_senders" does not exist`. | Schema bootstrap ran only on API startup. | CLI calls `init_db()` first. | A |
| 3 | Every merchant resolved to zero senders. | Sender lookup was an exact match on the cleaned effective merchant; real payees are raw strings like `dollar tree 9523 westheimer rd …`. | Token-boundary substring match against known keys, longest wins; exact and tolerant results unioned. | A |
| 4 | A bar and a taqueria each pulled several *unrelated personal emails* through the extractor. | Hint fallback OR'd merchant words as separate phrases under a `*` allowlist — `post OR oak` matched half the inbox. Privacy leak, not just noise. | Hints are one quoted phrase, subject-scoped, and every search carries `RECEIPT_SHAPE_CLAUSE`; hint path skipped with < 2 tokens. | A |
| 5 | `merchant_senders` contained a NASA newsletter, Chase, and Bank of America — the latter as the sender for Amazon. | `learn_sender` fired whenever the hint path *fetched* a candidate, not when one *matched*. Poisoned rows then shadowed seeds. | Learn only on `exact_total`/`split_partial` ≥ threshold via the hint path; learned key is the hint phrase; `--reset-learned`. | A |
| 6 | Seeded Amazon/Apple domains never matched. | Seeds were never inserted; `--seed-senders` was a separate step a first-time user skipped. | Seeding runs on every `--enrich` (idempotent). | A |
| 7 | `amazon.com*568eb8rd0`, `microsoft*xbox` resolved `none`. | Tokenizer split on whitespace only. | Split on any non-alphanumeric. | A |
| 8 | Amazon confirmations were outside the window. | Window assumed the email follows the charge; order confirmations precede the card charge by days. | Defaults `EMAIL_LOOKBACK_DAYS=10`, `EMAIL_LOOKAHEAD_DAYS=5`. | A |
| 9 | Both extractors returned no total on a receipt that visibly shows one. | `payload_to_body` preferred `text/plain` whenever present; transactional mail ships a 14–573 byte plain-text *stub* beside the real HTML. | Choose plain only if ≥ 50% the length of the HTML-derived text; else HTML. `--inspect` shows both lengths. | 02C |
| 10 | Model labelled a television "Shopping". | Extraction prompt offered the known-categories list and told the model to prefer it — the bank's vocabulary fed back to the model. | Extraction *describes* (`product_type`, free-form `category_hint`); classification happens in `snap_category` downstream; `unknown` + raw hint is the signal for a new category. | C |
| 11 | Identical email, two runs: `raw_confidence` 0.95 then 0.0. The second would have scored an arithmetically perfect match at 0.2, below threshold. | Match confidence was `raw_confidence + 0.2`; the model's self-report vetoed verified arithmetic. Also non-determinism at default temperature. | `exact_total` 0.9 (+0.1 with order id), `split_partial` 0.7 (+0.1), `date_only` 0.4 × raw. Temperature 0. Self-contradictory 0.0-with-total rewritten to 0.5. | A/C |
| 12 | Agent said "likely a laptop, tablet, or gaming console" when evidence said `television`. | `get_evidence` withheld line-item fields (to protect descriptions) and `product_type` did not exist when the tool was specified. The agent speculated. | `get_evidence` exposes `product_type`, `category_hint`, `dominant_category_raw`; prompt forbids speculation beyond evidence. | 03 |

Related findings that were *not* defects: the receipt-shape clause correctly excluded three Amazon promotional emails (the only Amazon mail in this mailbox — the purchases were made under another account); the matcher correctly rejected an unrelated $179.92 email against an $18.24 charge; the regex extractor's "largest amount is the total" heuristic would have picked a $699.99 "comp. value" over the $324.74 total — the model read the receipt structure correctly.

## Standing conclusions

- **Regex extraction is a bootstrap only.** Set `EXTRACTION_MODEL` (cheapest tier is sufficient; one tool-less structured call per candidate).
- **Rules own scope; the model owns reading.** Which mailbox, which sender, which window, and whether the number matches are code. What the receipt says is the model. Nothing about a receipt's content can widen a search.
- **Anything learned from live data must be gated on a verified match.** Observation ≠ confirmation.
- **Evidence beats taxonomy.** When the receipt is finer than the categories, the system surfaces `unknown` + raw hint and lets a human grow the taxonomy at the interrupt. It does not coarsen.

## Operating notes

- Start with a narrow `EMAIL_SENDER_ALLOWLIST`; widen to `*` only after a dry-run looks right. Use `--ids` and `--inspect <id>` to iterate on one transaction.
- Refresh tokens from an OAuth app in *Testing* status expire in 7 days (`refresh_token_expires_in: 604799`). Move the app to Production.
- `--enrich --dry-run --verbose` prints the exact Gmail query; `--inspect` prints body source/lengths and the extractor's fields without printing body text.
- If a merchant's receipts land in a different mailbox than the one connected, no amount of tuning finds them. Multi-account support is the next design conversation.

## Next candidates (not started)

Model-based triage of candidate *metadata* before fetch (reduces fetches, allows looser retrieval; reads subjects/snippets only). `category:purchases` as a first-pass pre-filter. Multiple mailboxes per user. Splits for multi-category orders (line items already retained). Attachment/PDF receipts. Proposal expiry for stale `open` rows.
