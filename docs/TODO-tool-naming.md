# TODO: Standardise Tool Function Names

## Problem

Tool function names lack a consistent verb vocabulary. Cross-system research
(Codex, Gemini-CLI, OpenCode) shows convergence on a small verb set:

| Verb       | Meaning                        |
|------------|--------------------------------|
| `read`     | Fetch a single item by ID      |
| `list`     | Enumerate a collection          |
| `search`   | Filtered / keyword query        |
| `create`   | Write-side: produce new object  |
| `send`     | Write-side: deliver a message   |
| `run`      | Execute a command               |

All three systems use `snake_case` and `verb_noun` ordering with no service
prefix on the verb.

## Renames

| Current                    | New                       | Reason                                                  |
|----------------------------|---------------------------|---------------------------------------------------------|
| `draft_email`              | `create_email_draft`      | `draft` is not a standard verb; `create` + object noun  |
| `post_slack_message`       | `send_slack_message`      | `post` → `send` (converged verb for messaging)          |
| `get_slack_channel_history`| `list_slack_messages`     | `get` is vague; this returns a collection → `list`      |
| `get_slack_thread_replies` | `list_slack_replies`      | Same `get` → `list` normalisation                       |
| `search_drive`             | `search_drive_files`      | Noun should be the object type, not the service name    |

## Files touched per rename

Each rename touches exactly three locations:

1. **Tool module** — function `def` + internal docstring cross-refs
2. **`co_cli/agent.py`** — import + `agent.tool()` registration + `tool_names` list
3. **Test file** — import + all call sites + test function names / docstrings

No string-based tool-name references outside tests exist (pydantic-ai derives the
tool name from `fn.__name__` at registration time).

## Not renamed (already consistent)

`run_shell_command`, `search_notes`, `list_notes`, `read_note`,
`read_drive_file`, `list_emails`, `search_emails`,
`list_calendar_events`, `search_calendar_events`,
`list_slack_channels`, `list_slack_users`
