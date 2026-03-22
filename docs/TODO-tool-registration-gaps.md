# TODO: Tool Registration Gaps

Gaps identified from deep code scan of tool registration and agent integration.
Ordered high → low impact.

---

## T1 — Shell approval split-brain

**Files:** `agent.py`, `co_cli/tools/shell.py`, `co_cli/context/_orchestrate.py`

**Problem:** `run_shell_command` is registered `requires_approval=False` so pydantic-ai
calls it directly, but the tool body raises `ApprovalRequired` for non-safe commands.
This makes `tool_approvals["run_shell_command"] == False`, which falsely signals
"skill-grantable" — forcing a hardcoded name-carve-out in `_check_skill_grant`:

```python
if tool_name == "run_shell_command":  # smell: hardcoded
    return False
```

**Fix:** Extend `tool_approvals` value from `dict[str, bool]` to `dict[str, bool | str]`.
Add a third value `"conditional"` meaning: registered `requires_approval=False` but
may raise `ApprovalRequired` internally based on runtime policy.

- In `_register()`, add optional `approval` param (default `None`): when `"conditional"`,
  store `"conditional"` in `tool_approvals` and pass `requires_approval=False` to
  `agent.tool()`.
- Register shell: `_register(run_shell_command, False, approval="conditional")`
- In `_check_skill_grant`, replace hardcoded guard with:
  `if deps.session.tool_approvals.get(tool_name) in (True, "conditional"): return False`
- No changes to `shell.py` or approval flow — the internal `ApprovalRequired` raise and
  `ctx.tool_call_approved` check are correct conditional-approval pydantic-ai idiom and
  stay as-is.

**done_when:** `_check_skill_grant` has no hardcoded tool names; shell registers with
`approval="conditional"`; existing approval behavior unchanged.

---

## T2 — `_tool_args_display` hardcoded to one tool

**Files:** `co_cli/context/_orchestrate.py`

**Problem:** `_tool_args_display` returns `""` for every tool except `run_shell_command`.
Every invocation panel (web_search, read_file, write_file, delegate_coder, etc.) shows
no context to the user.

**Fix:** Replace the if/else with a lookup dict at the top of `_orchestrate.py`:

```python
_TOOL_DISPLAY_ARG: dict[str, str] = {
    "run_shell_command": "cmd",
    "web_search": "query",
    "web_fetch": "url",
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "find_in_files": "pattern",
    "list_directory": "path",
    "save_memory": "content",
    "recall_article": "query",
    "search_knowledge": "query",
    "search_memories": "query",
    "search_notes": "query",
    "read_note": "note_path",
    "delegate_coder": "task",
    "delegate_research": "query",
    "delegate_analysis": "question",
    "delegate_think": "problem",
    "start_background_task": "command",
    "check_task_status": "task_id",
}
```

`_tool_args_display` becomes:

```python
def _tool_args_display(tool_name: str, part: ToolCallPart) -> str:
    key = _TOOL_DISPLAY_ARG.get(tool_name)
    if not key:
        return ""
    val = part.args_as_dict().get(key, "")
    return str(val)[:120]  # cap at 120 chars to avoid flooding the panel
```

No abstractions needed — one dict, one lookup.

**done_when:** Every tool in `_TOOL_DISPLAY_ARG` shows its primary arg in the invocation
panel; unknown tools show empty string (safe default).

---

## T3 — Per-tool retry budget

**Files:** `agent.py`

**Problem:** `retries=config.tool_retries` is a single global applied to all tools.
Network-bound tools (web, Google APIs) need higher tolerance; write tools (`write_file`,
`save_memory`, `create_email_draft`) must not silently retry mutations.

**Fix:** Extend `_register()` to accept optional `retries: int | None = None`:

```python
def _register(fn, requires_approval: bool, retries: int | None = None, ...) -> None:
    kwargs = {"requires_approval": requires_approval}
    if retries is not None:
        kwargs["retries"] = retries
    agent.tool(fn, **kwargs)
    tool_approvals[fn.__name__] = ...
```

At registration sites, annotate by class — no need to tune every tool individually.
Three tiers are sufficient:

| Tier | Tools | `retries` |
|---|---|---|
| Network | `web_search`, `web_fetch`, `list_emails`, `search_emails`, `search_drive_files`, `read_drive_file`, `list_calendar_events`, `search_calendar_events` | `3` |
| Write-once | `write_file`, `edit_file`, `save_memory`, `append_memory`, `update_memory`, `save_article`, `create_email_draft` | `1` |
| Default | everything else | omit (uses global `config.tool_retries`) |

**done_when:** Write tools registered with `retries=1`; network tools with `retries=3`;
all others unchanged.

---

## T4 — Approval persistence is shell-only

**Files:** `co_cli/tools/_tool_approvals.py`

**Problem:** `approval_remember_hint` and `remember_tool_approval` only handle
`run_shell_command` with pattern-based persistence. All other approval-gated tools
fall into `session_tool_approvals` (a flat set that resets each session). A user who
approves `write_file` for a project is re-prompted every new session.

**Fix:** Extend `remember_tool_approval` and `approval_remember_hint` to cover file
tools using their `file_path` arg as a directory-prefix pattern — same mechanism as
shell patterns in `_exec_approvals.py`.

- Add `write_file` and `edit_file` to `approval_remember_hint`:
  ```python
  if tool_name in ("write_file", "edit_file"):
      path = args.get("file_path", "")
      parent = str(Path(path).parent) if path else ""
      hint = f"[always -> will remember: {parent}/**]" if parent else None
      return hint
  ```
- In `remember_tool_approval`, for file tools: call `add_approval` with the parent
  directory as the pattern, same `exec_approvals_path` store (tool name disambiguates
  in the stored record).
- In `is_shell_command_persistently_approved`, extract a parallel
  `is_file_tool_persistently_approved(file_path, tool_name, deps)` that matches
  stored directory-prefix patterns.
- `_collect_deferred_tool_approvals` calls the right persistence check per tool name
  before prompting the user.

No new persistence store needed — reuse `_exec_approvals.py`'s existing
`add_approval` / `find_approved` / `update_last_used` mechanism with tool-namespaced
records.

**done_when:** `write_file`/`edit_file` approval choices can be persisted as
directory-prefix patterns; re-prompt only when path doesn't match a saved pattern.

---

## T5 — MCP tool results don't render

**Files:** `co_cli/context/_orchestrate.py`

**Problem:** `FunctionToolResultEvent` handler passes `None` to `on_tool_complete`
for any result that isn't a bare non-empty string or a dict with `_kind=="tool_result"`.
MCP tools return raw dicts or JSON strings — they silently show nothing in the panel.

```python
elif isinstance(content, dict) and content.get("_kind") == "tool_result":
    frontend.on_tool_complete(tool_id, content)
else:
    frontend.on_tool_complete(tool_id, None)  # MCP lands here
```

**Fix:** Add a third branch for raw dict/string MCP results before the `None` fallback:

```python
elif isinstance(content, dict):
    # MCP tools return raw JSON — render as compact key: value summary
    summary = "; ".join(f"{k}: {v}" for k, v in list(content.items())[:5])
    if len(content) > 5:
        summary += f" (+{len(content) - 5} more)"
    frontend.on_tool_complete(tool_id, summary[:300])
elif isinstance(content, str) and content.strip():
    # Catch-all for non-empty strings that failed the first branch
    frontend.on_tool_complete(tool_id, content[:300])
else:
    frontend.on_tool_complete(tool_id, None)
```

**done_when:** MCP tool results show a non-empty summary in the tool panel instead of
being silently dropped.

---

## T6 — `ToolResult._kind` typed as `str` not `Literal`

**Files:** `co_cli/tools/_result.py`

**Problem:** `_kind: Required[str]` loses static discriminator enforcement. mypy/pyright
cannot narrow on `content.get("_kind") == "tool_result"` because `str` is too wide.

**Fix:** One-line change:

```python
# before
_kind: Required[str]

# after
from typing import Literal
_kind: Required[Literal["tool_result"]]
```

**done_when:** mypy narrows correctly on `_kind`; no other changes needed.

---

## T7 — Sub-agent tool inventory invisible to session tracking

**Files:** `co_cli/tools/_delegation_agents.py`, `co_cli/tools/capabilities.py`

**Problem:** `make_coder_agent`, `make_research_agent`, etc. register their own tools
internally. `check_capabilities` and `/tools` only report the delegation wrapper name
(e.g. `delegate_coder`), not the sub-agent's tool surface (`list_directory`,
`read_file`, `find_in_files`). Minor observability gap — no runtime impact.

**Fix:** Export a `TOOLS` constant from each factory:

```python
# _delegation_agents.py
CODER_TOOLS = ("list_directory", "read_file", "find_in_files")
RESEARCH_TOOLS = ("web_search", "web_fetch")
ANALYSIS_TOOLS = ("search_knowledge", "search_drive_files")
THINKING_TOOLS: tuple[()] = ()
```

In `check_capabilities`, include sub-agent tool surfaces in the display when the
corresponding role model is configured:

```python
lines.append(f"Delegation: coder={CODER_TOOLS}, research={RESEARCH_TOOLS}, ...")
```

No changes to session tracking — this is display-only.

**done_when:** `check_capabilities` output lists the tools each delegation sub-agent
has access to when its role model is configured.

---

## T8 — Dead MCP server tools still appear to the model

**Files:** `co_cli/agent.py`

**Problem:** MCP `toolsets` are attached to `Agent` at construction. When a server
fails to connect, its tools are absent from `session.tool_names` (discovery skips them),
but pydantic-ai still advertises them to the model via the toolset. The model can
attempt a call on a dead server and receive a runtime error rather than a clean
"unavailable" signal.

**Fix:** After `discover_mcp_tools`, cross-reference `discovery_errors` against
registered toolsets. For each failed server prefix, log a warning and call
`frontend.on_status()` so the user knows which MCP tools are unavailable. No programmatic
toolset removal (pydantic-ai doesn't support post-construction toolset removal) — the
fix is informational: surface the failure clearly so the user can reconfigure.

If pydantic-ai gains toolset removal support, revisit with actual removal.

**done_when:** Failed MCP server errors are surfaced in the banner or status output at
session start, not silently dropped.

---

## Delivery scope

| Task | Impact | Effort |
|---|---|---|
| T1 shell split-brain | High | Small |
| T2 tool args display | High | Small |
| T3 per-tool retries | High | Small |
| T4 file approval persistence | Medium | Medium |
| T5 MCP result rendering | Medium | Small |
| T6 `_kind` Literal type | Medium | Trivial |
| T7 sub-agent inventory | Low | Small |
| T8 dead MCP server signal | Low | Small |
