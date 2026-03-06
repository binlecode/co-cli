# Tools

Native agent tools: shell execution, workspace files, background tasks, todo session state, Obsidian, Google (Drive/Gmail/Calendar), web search and fetch, memory persistence, capability introspection, and sub-agent delegation. See [DESIGN-mcp-client.md](DESIGN-mcp-client.md) for external MCP tool servers, and [DESIGN-prompt-design.md](DESIGN-prompt-design.md) for loop/prompt architecture that consumes these tools.

## Common Conventions

**Registration:** All native tools use `agent.tool()` with `RunContext[CoDeps]`. Zero `tool_plain()` remaining. Approval is set per tool at registration (not inferred): some side-effectful tools require approval (`write_file`, `start_background_task`), while a few write/cancel paths currently do not (`update_memory`, `append_memory`, `cancel_background_task`). `run_shell_command` is registered without approval (`requires_approval=False`) — policy enforcement (DENY/ALLOW/REQUIRE_APPROVAL) lives inside the tool, which raises `ApprovalRequired` when needed. The chat loop handles the `[y/n/a]` prompt via `DeferredToolRequests`.

**Return shape:** Most user-facing tools return `dict[str, Any]` with a `display` field (pre-formatted string, shown verbatim) plus metadata (`count`, `has_more`, etc.). Some tools intentionally return plain text (`run_shell_command` success path, `read_note`, `read_drive_file`, `create_email_draft`). Error helper results use `{"display": "...", "error": True}`.

**Error classification:** Two strategies:
- `ModelRetry(msg)` — pre-request validation failures and retry-worthy operational failures. pydantic-ai retries the tool call.
- `terminal_error(msg)` — non-retryable failures where retrying the same call will not help (for example missing credentials/config, policy-denied requests, unsupported content types). Returns `{"display": ..., "error": True}` so the model can stop looping and route to an alternative.

**Approval table:**

| Tool | Approval | Rationale |
|------|----------|-----------|
| `run_shell_command` | Conditional | Policy in tool: DENY → terminal_error, ALLOW → execute, else `ApprovalRequired`. |
| `create_email_draft` | Yes | Creates Gmail draft on user's behalf |
| `save_memory` | Yes | Writes to `.co-cli/knowledge/` |
| `save_article` | Yes | Writes to `.co-cli/knowledge/` |
| `write_file` | Yes | Writes files to the workspace |
| `edit_file` | Yes | Modifies existing workspace files |
| `start_background_task` | Yes | Spawns a subprocess in the background; pre-execution approval gate |
| `update_memory`, `append_memory` | No | Writes to existing memory files; currently registered without approval |
| `todo_write`, `todo_read` | No | In-memory session state only — no external side effects |
| `check_task_status`, `cancel_background_task`, `list_background_tasks` | No | Read-only or self-contained task ops |
| Most other native tools | No | Read-only operations |

**Docstring standard:** Every tool docstring addresses four dimensions:

| Dim | Name | What it covers |
|-----|------|----------------|
| **D1** | What it does | One action sentence — verb + object + return shape |
| **D2** | What it returns | Key fields, format, how to present to user |
| **D3** | When/how to use | Cross-tool routing, alternatives, pagination, use-case enumeration, scope boundaries, conditional behavior, fallback guidance |
| **D4** | Caveats | Limits, failure modes, silent failures, what NOT to do |

D1 and D2 are required for every tool. D3 and D4 are required unless there is genuinely nothing to say. D3 sub-patterns:

| Code | Sub-pattern | When to apply |
|------|-------------|---------------|
| **D3a** | Cross-tool references | Any tool that routes to or from another tool — reference it by name |
| **D3b** | When-to-use vs alternatives | When two+ tools solve similar problems — disambiguate use cases |
| **D3c** | Pagination / looping as capability | Any tool that returns paginated or truncated results |
| **D3d** | Use-case enumeration | When correct use cases are non-obvious — explicit DO / DO NOT list |
| **D3e** | Scope boundaries | When the tool does NOT do something users might expect |
| **D3f** | Conditional behavior | When behavior changes based on input type or state |
| **D3g** | Fallback guidance | When the tool can fail silently or partially — what to try instead |

Anti-patterns to avoid:

| AP | Rule |
|----|------|
| **AP1** | Describe pagination as agent capability, not user-driven |
| **AP2** | Routing is bidirectional: if A mentions B, B should mention A |
| **AP3** | Silent result caps must be documented in D3 or D4 |
| **AP4** | Never document framework-injected params (`RunContext`, `CoDeps`) |
| **AP5** | Simple tools stay 1–4 lines; don't add dimensions that don't apply |
| **AP6** | If a tool creates but doesn't send, say so explicitly (D3e) |

Additional: CAPS reserved for safety-critical constraints only (write/delete tools). Each param description: type context + example value. Paginated tools embed next-action hints in the `display` field.

**Cross-tool routing map:**

```
User says "find X"
  ├── in memories (preferences, decisions)  → search_knowledge(kind=memory) → list_memories
  ├── in Obsidian notes (personal notes)    → search_knowledge(source=obsidian) → list_notes → read_note
  ├── in Google Drive (cloud docs)          → search_drive_files → read_drive_file
  ├── in Gmail (emails)                     → search_emails
  ├── in Calendar (events)                  → search_calendar_events
  ├── on the web                            → web_search → web_fetch
  ├── in workspace files (content)          → find_in_files → read_file
  └── on the filesystem / other             → list_directory → run_shell_command
```

Bidirectional routing: `search_knowledge ↔ list_notes ↔ search_drive_files` (tri-directional disambiguation), `list_notes → read_note`, `search_drive_files → read_drive_file`, `web_search → web_fetch → run_shell_command` (curl fallback), `list_emails ↔ search_emails`, `list_calendar_events ↔ search_calendar_events`. New tools must wire into this graph — update bidirectional refs in both directions.

**Skills:** See `docs/DESIGN-skills.md` for the full skills subsystem architecture.

**Remaining docstring improvements** (apply when tools are next modified):
- `search_notes`: `has_more` is returned but `display` doesn't embed "More results available — increase limit or narrow with folder/tag." Add per the `search_drive_files` pattern.
- New file-write/edit tools: add MUST/NEVER emphasis constraints per AP6.

## Tool Families

| Family | Doc | Scope |
|--------|-----|-------|
| Execution | [DESIGN-tools-execution.md](DESIGN-tools-execution.md) | Shell, file, background tasks, todo, capabilities |
| Integrations | [DESIGN-tools-integrations.md](DESIGN-tools-integrations.md) | Memory, Obsidian, Google (Drive/Gmail/Calendar), web |
| Delegation | [DESIGN-tools-delegation.md](DESIGN-tools-delegation.md) | Coder, research, analysis sub-agents |
