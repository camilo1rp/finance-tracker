# Gmail REST spike output

Redacted. Owner review before commit.

## 1. profile
- account_hint: `c***@gmail.com`
- response_keys: ['emailAddress', 'historyId', 'messagesTotal', 'threadsTotal']

## 2. list_messages
- gmail_query: `after:####/##/## before:####/##/## -in:draft`
- id_count: 5
- nextPageToken_present: True
- nextPageToken_meta: {'length': 20, 'charset': 'digit'}
- resultSizeEstimate_type: int

## 3. metadata
- message_id_meta: {'length': 16, 'charset': 'lower+digit'}
- from_had_display_name: True
- sender_kind: display-name
- internalDate_iso: 2026-09-03T23:03:11+00:00
- headers_present: ['Date', 'From', 'Message-ID', 'Subject', 'To']

## 4. full
- part_types: ['multipart/alternative', 'text/html', 'text/plain']
- mime_tree: `{'mimeType': 'multipart/alternative', 'depth': 0, 'parts': [{'mimeType': 'text/plain', 'depth': 1, 'parts': []}, {'mimeType': 'text/html', 'depth': 1, 'parts': []}]}`
- body_source: text/plain
- body_bytes: 2082
- attachment_count: 0
- plaintext_preview_redacted_80: `In one hour,
​
​I’ll show you how to survive the AI & Bond Bubble.
​
​→ Join`
