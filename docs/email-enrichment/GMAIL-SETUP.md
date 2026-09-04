# Gmail setup

Two adapters share the same OAuth client, `gmail.readonly` scope, and Gmail search syntax. **Path 1 is the primary provider.** Path 2 remains in the tree for enrolled Workspace accounts.

This adapter may invoke only four Gmail REST GETs under `users/me` (`messages` list, `messages/{id}` metadata, `messages/{id}` full, `profile`), or — on Path 2 — only `search_threads`, `get_message`, and `list_labels`. Request scope `https://www.googleapis.com/auth/gmail.readonly` only.

## Path 1 — `gmail_rest` (any Google account)

Works with personal `@gmail.com` accounts. No Developer Preview enrollment.

### 1. GCP project and Gmail API

1. Create or select a GCP project.
2. Enable the Gmail API (`gmail.googleapis.com`) on that project (`gcloud services enable gmail.googleapis.com --project=PROJECT_ID`).

### 2. OAuth consent screen (External, readonly)

1. Configure the OAuth consent screen as **External**. Add your own address as a test user.
2. Under Data Access, add **only** `https://www.googleapis.com/auth/gmail.readonly`. Do not add `gmail.compose` or `mail.google.com`.

An External app in *Testing* status issues refresh tokens that expire after 7 days. Moving the app to *Production* (accepting the unverified-app warning for a personal, ≤100-user app) removes that expiry — confirm current Google policy at the [OAuth consent screen documentation](https://developers.google.com/identity/protocols/oauth2/production-readiness) rather than treating this as settled.

### 3. Desktop OAuth client and refresh token

1. Create an OAuth client of type **Desktop app**. Copy the client ID and client secret.
2. Desktop clients use the authorization-code flow with `access_type=offline` and `prompt=consent` so Google returns a refresh token.

Open this URL in a browser (replace `CLIENT_ID`):

```
https://accounts.google.com/o/oauth2/v2/auth?client_id=CLIENT_ID&redirect_uri=http://127.0.0.1:8753/&response_type=code&scope=https://www.googleapis.com/auth/gmail.readonly&access_type=offline&prompt=consent
```

After consent, the browser lands on `http://127.0.0.1:8753/?code=...`. Copy the `code` query parameter.

Exchange it (replace `CLIENT_ID`, `CLIENT_SECRET`, `CODE`):

```
curl -s -X POST https://oauth2.googleapis.com/token \
  -d client_id=CLIENT_ID \
  -d client_secret=CLIENT_SECRET \
  -d code=CODE \
  -d grant_type=authorization_code \
  -d redirect_uri=http://127.0.0.1:8753/
```

Store `refresh_token` from the JSON. Do not commit it.

A one-shot listener for the redirect: `python -m http.server 8753` in an empty directory, or any tool that prints the inbound query string.

### 4. Environment (Path 1)

Copy from `.env.example`. Never commit values.

| Variable | Purpose |
|---|---|
| `EMAIL_PROVIDER=gmail_rest` | Selects `GmailRestEmailSource`. |
| `EMAIL_SENDER_ALLOWLIST` | Comma-separated sender patterns. Start with one merchant domain. |
| `GMAIL_REST_BASE_URL` | Default `https://gmail.googleapis.com/gmail/v1/users/me/`. |
| `GMAIL_REST_TIMEOUT_S` | Default `20`. |
| `GMAIL_ACCESS_TOKEN` | Optional static access token; if set, it wins. |
| `EMAIL_MCP_ACCESS_TOKEN` | Fallback static token, honored by both adapters. |
| `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` / `GMAIL_OAUTH_REFRESH_TOKEN` | Used when no static token is set. |

### 5. Live validation checklist (Path 1)

1. Run the REST spike; review `docs/email-enrichment/spike-output-rest.md` for redaction.

```
python -m scripts.gmail_rest_spike --after YYYY-MM-DD --before YYYY-MM-DD
```

2. With `EMAIL_PROVIDER=gmail_rest` and a narrow allowlist (one merchant domain), run:

```
python -m app.agent.cli --enrich --from YYYY-MM-DD --to YYYY-MM-DD --dry-run
python -m app.agent.cli --enrich --from YYYY-MM-DD --to YYYY-MM-DD
```

Use a one-week window for the non-dry run. Confirm evidence rows appear, `extraction` JSON contains no `body_text`, `provider` is `gmail_rest`, and `external_ref` is `gmail:<id>`.

3. Run the live smoke test once (addopts deselects it by default):

```
pytest -o addopts= -m live_gmail_rest
```

## Path 2 — `gmail` MCP (Workspace accounts enrolled in Developer Preview only)

The Gmail MCP server is a **Developer Preview**. Personal `@gmail.com` accounts are not admitted. Schemas and availability may change; do not treat this as a stable Google API. Independently, the tool has open defects for enrolled users since April 2026.

Reference:

- MCP server reference: https://developers.google.com/workspace/gmail/api/reference/mcp
- Configure MCP servers: https://developers.google.com/workspace/guides/configure-mcp-servers
- Developer Preview Program: https://developers.google.com/workspace/preview

### 1. Enroll and enable

1. Enroll the Workspace account / GCP org in the [Workspace Developer Preview Program](https://developers.google.com/workspace/preview).
2. Create or select a GCP project.
3. Enable `gmail.googleapis.com` and `gmailmcp.googleapis.com` on that project (`gcloud services enable gmail.googleapis.com gmailmcp.googleapis.com --project=PROJECT_ID`).

### 2. OAuth client (Desktop, readonly)

Same Desktop client and `gmail.readonly` scope as Path 1. Internal consent screen if possible; otherwise External with yourself as a test user.

### 3. Refresh token

Same authorization-code flow as Path 1.

### 4. Environment (Path 2)

| Variable | Purpose |
|---|---|
| `EMAIL_PROVIDER=gmail` | Selects `McpEmailSource`. Requires Developer Preview enrollment. |
| `EMAIL_SENDER_ALLOWLIST` | Comma-separated sender patterns. Start with one merchant domain. |
| `EMAIL_MCP_URL` | Default `https://gmailmcp.googleapis.com/mcp/v1`. |
| `EMAIL_MCP_TIMEOUT_S` | Default `20`. |
| `EMAIL_MCP_ACCESS_TOKEN` | Optional static access token; if set, it wins (after `GMAIL_ACCESS_TOKEN`). |
| `GMAIL_OAUTH_CLIENT_ID` / `GMAIL_OAUTH_CLIENT_SECRET` / `GMAIL_OAUTH_REFRESH_TOKEN` | Used when the static tokens are unset. |

### 5. Live validation checklist (Path 2)

1. Run the MCP spike; review `docs/email-enrichment/spike-output.md` for redaction.

```
python -m scripts.gmail_mcp_spike --after YYYY-MM-DD --before YYYY-MM-DD
```

2. With `EMAIL_PROVIDER=gmail` and a narrow allowlist:

```
python -m app.agent.cli --enrich --from YYYY-MM-DD --to YYYY-MM-DD --dry-run
python -m app.agent.cli --enrich --from YYYY-MM-DD --to YYYY-MM-DD
```

3. Run the live smoke test once:

```
pytest -o addopts= -m live_gmail
```
