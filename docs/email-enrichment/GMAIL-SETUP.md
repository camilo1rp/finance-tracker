# Gmail MCP setup

The Gmail MCP server is a **Developer Preview**. Schemas and availability may change; do not treat this as a stable Google API.

Reference:

- MCP server reference: https://developers.google.com/workspace/gmail/api/reference/mcp
- Configure MCP servers: https://developers.google.com/workspace/guides/configure-mcp-servers

This adapter may invoke only `search_threads`, `get_message`, and `list_labels`. Request scope `https://www.googleapis.com/auth/gmail.readonly` only.

## 1. Enroll and enable

1. Enroll the Workspace account / GCP org in the [Workspace Developer Preview Program](https://developers.google.com/workspace/preview).
2. Create or select a GCP project.
3. Enable `gmail.googleapis.com` and `gmailmcp.googleapis.com` on that project (`gcloud services enable gmail.googleapis.com gmailmcp.googleapis.com --project=PROJECT_ID`).

## 2. OAuth client (Desktop, readonly)

1. Configure the OAuth consent screen (Internal if possible; otherwise External with yourself as a test user).
2. Under Data Access, add **only** `https://www.googleapis.com/auth/gmail.readonly`. Do not add `gmail.compose` or `mail.google.com`.
3. Create an OAuth client of type **Desktop app**. Copy the client ID and client secret.

## 3. Refresh token

Desktop clients use the authorization-code flow with `access_type=offline` and `prompt=consent` so Google returns a refresh token.

1. Open this URL in a browser (replace `CLIENT_ID`):

```
https://accounts.google.com/o/oauth2/v2/auth?client_id=CLIENT_ID&redirect_uri=http://127.0.0.1:8753/&response_type=code&scope=https://www.googleapis.com/auth/gmail.readonly&access_type=offline&prompt=consent
```

2. After consent, the browser lands on `http://127.0.0.1:8753/?code=...`. Copy the `code` query parameter.
3. Exchange it (replace `CLIENT_ID`, `CLIENT_SECRET`, `CODE`):

```
curl -s -X POST https://oauth2.googleapis.com/token \
  -d client_id=CLIENT_ID \
  -d client_secret=CLIENT_SECRET \
  -d code=CODE \
  -d grant_type=authorization_code \
  -d redirect_uri=http://127.0.0.1:8753/
```

4. Store `refresh_token` from the JSON. Do not commit it.

A one-shot listener for step 2: `python -m http.server 8753` in an empty directory, or any tool that prints the inbound query string.

## 4. Environment

Copy from `.env.example`. Never commit values.

| Variable | Purpose |
|---|---|
| `EMAIL_PROVIDER=gmail` | Selects `McpEmailSource`. |
| `EMAIL_SENDER_ALLOWLIST` | Comma-separated sender patterns. Start with one merchant domain. |
| `EMAIL_MCP_URL` | Default `https://gmailmcp.googleapis.com/mcp/v1`. |
| `EMAIL_MCP_TIMEOUT_S` | Default `20`. |
| `EMAIL_MCP_ACCESS_TOKEN` | Optional static access token; if set, it wins. |
| `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` / `GMAIL_OAUTH_REFRESH_TOKEN` | Used when the static token is unset. |

## 5. Live validation checklist

1. Run the spike; review `docs/email-enrichment/spike-output.md` for redaction; commit it.

```
python -m scripts.gmail_mcp_spike --after YYYY-MM-DD --before YYYY-MM-DD
```

2. Compare the observed shapes against the Task 02 Section 1 assumptions. Flag in `spike-output.md` any of: `sender` carrying a display name, `date` carrying a time, `plaintextBody` empty for HTML mail, `attachments[]` missing ids, `nextPageToken` behavior, `resultCountEstimate` type. If step 2 flags a mismatch, hand the redacted output back to the architect **before** changing the mapping layer.

3. With `EMAIL_PROVIDER=gmail` and a narrow allowlist (one merchant domain), run:

```
python -m app.agent.cli --enrich --from YYYY-MM-DD --to YYYY-MM-DD --dry-run
python -m app.agent.cli --enrich --from YYYY-MM-DD --to YYYY-MM-DD
```

Use a one-week window for the non-dry run. Confirm evidence rows appear, `extraction` JSON contains no `body_text`, and the trace (if tracing is on) shows redacted inputs.

4. Run the live smoke test once (addopts deselects it by default):

```
pytest -o addopts= -m live_gmail
```
