# Outcome: Gmail MCP server unavailable for personal Google accounts (Developer Preview enrollment required). Primary provider is `gmail_rest`; see README.

# Gmail MCP spike output

Redacted. Owner review before commit.

## 1. tools/list
- tool_count: 23
- tool_names: create_draft, list_drafts, get_draft, get_thread, get_message, search_threads, label_thread, unlabel_thread, apply_sensitive_thread_label, trash_thread, untrash_thread, mark_thread_spam, unmark_thread_spam, list_labels, label_message, update_message_labels, unlabel_message, apply_sensitive_message_label, trash_message, untrash_message, mark_message_spam, unmark_message_spam, create_label
- get_message input_schema_keys: ['messageFormat', 'messageId']
- list_labels input_schema_keys: []
- search_threads input_schema_keys: ['includeTrash', 'pageSize', 'pageToken', 'query', 'view']

## 2. search_threads
- gmail_query: `in:inbox`
- isError: `Access to this tool requires that your Google Cloud project (############) is enrolled in the Google Workspace Developer Preview Program. Please see https://developers.google.com/workspace/preview`
