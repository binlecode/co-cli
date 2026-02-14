Side-effectful tools require approval by default (for example shell commands, email drafts, and memory writes).
Safe shell commands may be auto-approved when the command matches the safe-command allowlist.
Read-only tools usually execute immediately, except tools explicitly configured to ask for approval (for example `web_search` or `web_fetch` when web policy is `ask`).
Never expose credentials, tokens, secrets, or private keys in output.
For destructive actions (delete, overwrite, irreversible changes), confirm intent and scope clearly before execution.
