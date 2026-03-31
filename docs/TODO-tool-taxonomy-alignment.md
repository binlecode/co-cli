# TODO: Tool Taxonomy Alignment

**Slug:** `tool-taxonomy-alignment`
**Task type:** `code-refactor` + UX/diagnostics alignment
**Status:** Draft — awaiting Gate 1

---

## Context

Deep review of current `co` source and local peer systems found that the main gap is
not tool capability coverage. The gap is taxonomy shape.

`co` already has strong raw capability coverage:

- workspace file tools
- shell execution with approval policy
- memory and article retrieval
- session todos
- background subprocess tasks
- delegated subagents
- web search/fetch
- native integrations (Obsidian, Google)
- MCP toolsets

But the current grouping in source is still implementation-led rather than
capability-led.

**Current-state validation notes:**

- Native tool registration order in `co_cli/agent.py` groups tools as:
  background tasks, capability introspection, subagents, files, shell, memory
  writes, todos, memory/article reads, Obsidian, Google, and web.
- The main agent already has a separate source split between native and MCP
  toolsets: native tools are assembled in `_build_filtered_toolset()`, MCP
  toolsets in `_build_mcp_toolsets()`, then combined in `toolsets=[filtered_toolset] + mcp_toolsets`.
- `AgentCapabilityResult` currently returns only three fields:
  `agent`, `tool_names`, and `tool_approvals`. `build_agent()` and
  `build_task_agent()` seed `tool_names` from `list(tool_approvals.keys())`,
  so native registration metadata is currently limited to approval flags.
- `CoCapabilityState` currently stores only flat metadata:
  `tool_names`, `tool_approvals`, MCP discovery errors, and skill registries.
  It does not store tool family, tool source, or integration identity.
- `discover_mcp_tools()` currently returns only `(tool_names, errors)`.
  `initialize_session_capabilities()` appends those names into
  `deps.capabilities.tool_names`; it does not populate any structured MCP metadata.
- Runtime diagnostics (`check_capabilities`, bootstrap status, banner) derive
  counts and summaries from flat tool-name lists, not from a structured catalog.
- `check_capabilities()` currently derives MCP tool count by subtracting
  `len(tool_approvals)` from `len(tool_names)`, and its own inline comment
  explicitly warns that the formula breaks if tool metadata evolves.
- Skills are already separate from tool registration and should stay separate.
- There is no first-class VCS/git family; git access exists only through
  `run_shell_command`.

**Peer-system convergence used in this review:**

- Gemini CLI groups by primary capability family: Execution, File System,
  Interaction, Memory, Planning, System, Web.
- Codex separates internal vs MCP tool source while normalizing them through a
  unified routing surface.
- OpenClaw keeps MCP decoupled from the core runtime and treats skills as a
  separate platform, not a core tool family.
- Aider makes git/reversibility a first-class family in coding workflows.
- OpenCode and Claude Code both expose explicit workflow/state surfaces
  (plan/todo/subagent) rather than scattering them as unrelated utilities.

---

## Problem & Outcome

**Problem:** `co`'s current tool taxonomy is too close to implementation modules and
integration names. That creates three costs:

- The agent-facing capability surface is harder to reason about than necessary.
- Diagnostics and UI can only report flat tool counts, not meaningful families.
- New tools naturally drift into ad hoc buckets such as "Google", "memory write",
  or "read-only tools" instead of a stable, product-shaped classification.

**Outcome:** `co` should have a canonical, explicit tool taxonomy with two axes:

- **source axis:** `native` vs `mcp`
- **family axis:** stable capability families meaningful to the product and the model

Recommended initial family set:

- `workspace`
- `execution`
- `knowledge`
- `workflow`
- `delegation`
- `web`
- `connectors`
- `system`

Possible future extension:

- `git` as a first-class family, if `co` chooses to stay strong for coding tasks
  rather than treating VCS as generic shell execution forever.

---

## Scope

In scope:

- Canonical tool metadata model for source/family/integration
- Native tool registration metadata
- MCP discovery metadata plumbing
- Capability-state storage and diagnostics surfaces
- Minimal UI/status exposure of families
- Explicit decision on whether git remains implicit or becomes first-class

Out of scope:

- Adding browser control, ACP, or new major connectors
- Replacing pydantic-ai `FunctionToolset` / MCP integration patterns
- Renaming existing tool functions or changing tool schemas without a concrete need
- Folding skills into the tool taxonomy
- DESIGN doc edits as planned tasks; sync-doc happens after delivery

---

## Design Constraints

- Keep pydantic-ai usage idiomatic:
  native tools stay in `FunctionToolset`, MCP stays as MCP toolsets.
- Preserve the existing approval behavior and tool availability gates.
- Treat source and family as separate axes; do not collapse MCP into a family.
- Skills remain a separate layer from tools.
- Avoid speculative abstraction: add only the metadata needed to classify,
  report, and evolve the tool surface.
- Preserve backward compatibility for code paths that still rely on
  `tool_names` and `tool_approvals`.
- Do not infer deep semantic families for arbitrary MCP tools from prefixes alone;
  default them conservatively.

---

## High-Level Design

### Canonical taxonomy grounded in current code

Adopt one canonical catalog entry per tool with at least:

- tool name
- source (`native` or `mcp`)
- family (one of the canonical families)
- approval flag
- optional integration identity (`obsidian`, `google_drive`, `google_calendar`, server prefix, etc.)

In the current codebase, this means extending existing structures rather than
inventing a parallel registry:

- extend `AgentCapabilityResult` instead of replacing it
- extend `CoCapabilityState` instead of storing family metadata elsewhere
- extend `_build_filtered_toolset()` and `discover_mcp_tools()` return data
  instead of adding a second discovery pipeline

`tool_names` and `tool_approvals` remain compatibility views derived from the
catalog, not the primary data model.

### Post-fix capability surface

After this work, the capability surface should be explicit and backward-compatible.

`CoCapabilityState` should expose:

- `tool_catalog: dict[str, ToolConfig]`
- `tool_names: list[str]`
- `tool_approvals: dict[str, bool]`
- `mcp_discovery_errors: dict[str, str]`
- existing skill metadata fields unchanged

Where each `ToolConfig` carries at least:

- `name`
- `source` (`native` or `mcp`)
- `family` (`workspace`, `execution`, `knowledge`, `workflow`, `delegation`, `web`, `connectors`, `system`)
- `approval`
- optional `integration`

Effective family mapping target:

- `workspace`: file tools
- `execution`: shell tool
- `knowledge`: memories, articles, knowledge retrieval/mutation
- `workflow`: todos and background task lifecycle
- `delegation`: subagent tools
- `web`: web search/fetch
- `connectors`: Obsidian, Google, and MCP tools by default
- `system`: `check_capabilities`

`tool_names` and `tool_approvals` remain compatibility projections of
`tool_catalog` during migration. New diagnostics and reporting should derive
counts from `tool_catalog`, not from flat-list arithmetic.

### Post-rename target list

Future validation should check that the external tool surface converges on clear
verb-resource naming and removes the current ambiguous outliers.

Target names after rename work:

- `workspace`
  - `list_directory`
  - `read_file`
  - `find_in_files`
  - `write_file`
  - `edit_file`
- `execution`
  - `run_shell_command`
- `knowledge`
  - `save_memory`
  - `update_memory`
  - `append_memory`
  - `list_memories`
  - `search_memories`
  - `save_article`
  - `search_articles`
  - `read_article`
  - `search_knowledge`
- `workflow`
  - `write_todos`
  - `read_todos`
  - `start_background_task`
  - `check_task_status`
  - `cancel_background_task`
  - `list_background_tasks`
- `delegation`
  - `run_coding_subagent`
  - `run_research_subagent`
  - `run_analysis_subagent`
  - `run_reasoning_subagent`
- `web`
  - `web_search`
  - `web_fetch`
- `connectors`
  - `search_notes`
  - `list_notes`
  - `read_note`
  - `search_drive_files`
  - `read_drive_file`
  - `list_gmail_emails`
  - `search_gmail_emails`
  - `create_gmail_draft`
  - `list_calendar_events`
  - `search_calendar_events`
- `system`
  - `check_capabilities`

Validation rules for this target list:

- no `recall_*` names for article search tools
- no `*_detail` suffix when the tool is the canonical full read path
- no singular `todo_*` names for list-wide todo operations
- subagent names align with role names (`coding`, `research`, `analysis`, `reasoning`)
- connector tools in the flat global namespace are source-qualified when the
  generic name would otherwise be ambiguous
- `tool_names` compatibility output may carry legacy aliases temporarily during
  migration, but the long-term target set above is the canonical validation list

### Recommended native family mapping

- `workspace`: `list_directory`, `read_file`, `find_in_files`, `write_file`, `edit_file`
- `execution`: `run_shell_command`
- `knowledge`: memories, articles, local knowledge retrieval and mutation
- `workflow`: `todo_*`, background task lifecycle tools
- `delegation`: all subagent tools
- `web`: `web_search`, `web_fetch`
- `connectors`: native app/integration tools such as Obsidian and Google tools
- `system`: `check_capabilities`

### MCP handling

MCP tools should keep `source="mcp"` and receive a conservative family assignment.
Given the exact current flow in `discover_mcp_tools()` and
`initialize_session_capabilities()`, the pragmatic default is:

- `family="connectors"`
- `integration=<server prefix>`

This preserves a clean source split without pretending the runtime knows more
about a third-party tool's semantics than it actually does.

### Workflow vs delegation

Current code has todos, background tasks, and subagents as separate pockets.
Best-practice alignment does not require merging their implementations, but it
does require making their product shape legible:

- `workflow` = task tracking + long-running work
- `delegation` = bounded specialist agent work

That distinction should be visible in metadata, diagnostics, and comments.

---

## Implementation Plan

### TASK-1: Introduce a canonical tool catalog model

**prerequisites:** none

The current capability registry stores flat names and approval flags only. Add
one canonical metadata structure by extending the exact state objects that exist
today.

**What to do:**

- Add `ToolConfig`: a small immutable data descriptor for tool metadata.
- Extend `AgentCapabilityResult` to carry a native tool catalog in addition to
  the existing `tool_names` and `tool_approvals`.
- Extend `CoCapabilityState` to hold a tool catalog keyed by tool name.
- Keep `tool_names` and `tool_approvals` as compatibility fields for now.
- Keep the metadata minimal: name, source, family, approval, optional integration.
- Do not add a second capability registry outside `AgentCapabilityResult` /
  `CoCapabilityState`.

**files:**

- `co_cli/deps.py`
- `co_cli/agent.py`
- `co_cli/bootstrap/_bootstrap.py`

**done_when:**

- `deps.capabilities` can answer "what family is this tool?" and
  "is this native or MCP?" without parsing comments or tool-name prefixes.
- Existing callers using `tool_names` and `tool_approvals` still work unchanged.

---

### TASK-2: Make native tool registration declare family explicitly

**prerequisites:** TASK-1

Today family information exists only in comment blocks and registration order.
Move that information into the exact native registration path that exists now:
the local `_reg(...)` helper inside `_build_filtered_toolset()`.

**What to do:**

- Extend `_reg(...)` in `co_cli/agent.py` to accept family metadata and to
  populate both `tool_approvals` and the native tool catalog in one place.
- Extend `_build_filtered_toolset()` to return native catalog data alongside the
  existing filtered toolset and approval map.
- Update `build_agent()` and `build_task_agent()` to pass through the returned
  native catalog in `AgentCapabilityResult`.
- Register every currently native tool with an explicit canonical family.
- Remove taxonomy ambiguity such as separate "memory write" and "read-only tools"
  comments once the metadata exists.
- Keep conditional registration behavior unchanged for subagents, Obsidian,
  Google, and web policy gates.

**files:**

- `co_cli/agent.py`

**done_when:**

- No native tool's family is implied only by comment placement.
- A reader can inspect registration code and see family assignment directly.

---

### TASK-3: Normalize connectors without collapsing source and family

**prerequisites:** TASK-1, TASK-2

Current source mixes vendor buckets (`Google`, `Obsidian`) with capability
families. Normalize these as connector integrations using the exact discovery
and bootstrap flow that exists now.

**What to do:**

- Reclassify native Obsidian and Google tools under the `connectors` family.
- Attach integration identifiers such as `obsidian`, `google_drive`,
  `google_gmail`, and `google_calendar`.
- Extend `discover_mcp_tools()` beyond `(tool_names, errors)` so it also returns
  MCP catalog entries for each discovered tool.
- Update `initialize_session_capabilities()` so it extends
  `deps.capabilities.tool_catalog` at the same time it currently extends
  `deps.capabilities.tool_names`.
- During MCP discovery, populate catalog entries with `source="mcp"`,
  `family="connectors"`, and `integration=<server prefix>`.
- Do not try to infer deeper MCP families from tool names or server prefixes.

**files:**

- `co_cli/agent.py`
- `co_cli/bootstrap/_bootstrap.py`

**done_when:**

- The taxonomy no longer treats "Google" and "Obsidian" as peers of "files" or "web".
- MCP tools appear as external connector tools in the capability registry.

---

### TASK-4: Unify workflow-state surfaces under a coherent family model

**prerequisites:** TASK-1, TASK-2

`todo_*`, background tasks, and subagents are all workflow-adjacent, but the
current source exposes them as disconnected pockets across `_build_filtered_toolset()`
and their individual tool modules. The implementations can stay separate, but
the taxonomy and comments should make the product shape clearer.

**What to do:**

- Classify `todo_*` and background task tools under `workflow`.
- Keep subagent tools under `delegation`, not `workflow`.
- Tighten module comments and docstrings so the distinction is explicit:
  workflow manages task state and long-running work; delegation runs bounded
  specialist analysis/research.
- Update the registration comments in `_build_filtered_toolset()` so they match
  the new family model instead of the old implementation buckets.

**files:**

- `co_cli/agent.py`
- `co_cli/tools/todo.py`
- `co_cli/tools/task_control.py`
- `co_cli/tools/subagent.py`
- `co_cli/tools/capabilities.py`

**done_when:**

- The workflow/delegation split is visible in code comments, capability metadata,
  and runtime diagnostics.
- New workflow tools would have an obvious home without inventing another bucket.

---

### TASK-5: Add family-aware diagnostics and status reporting

**prerequisites:** TASK-1, TASK-2, TASK-3, TASK-4

Structured taxonomy only matters if the runtime can expose it. Today the
diagnostic surfaces are limited to flat counts and ad hoc summaries, and one of
them (`check_capabilities`) currently relies on a length-subtraction invariant.

**What to do:**

- Replace the current `check_capabilities()` MCP counting logic
  (`len(tool_names) - len(tool_approvals)`) with counts derived from the catalog.
- Extend `check_runtime()` status/capabilities output to include family counts
  and source counts derived from `deps.capabilities.tool_catalog`.
- Update `check_capabilities` metadata to return grouped tool information.
- Add compact family/source summaries to status surfaces where it adds signal.
- Keep the default UX concise; do not dump a long tool inventory into the banner.

**files:**

- `co_cli/tools/capabilities.py`
- `co_cli/bootstrap/_check.py`
- `co_cli/bootstrap/_render_status.py`
- `co_cli/bootstrap/_banner.py`
- `co_cli/commands/_commands.py` if `/status` output needs grouped display

**done_when:**

- `/doctor` and status paths can answer "how many tools are workspace vs web vs connectors?"
- MCP presence is reported as both a source dimension and a connector family count.

---

### TASK-6: Make an explicit git-family decision

**prerequisites:** TASK-1, TASK-2

Peer convergence on coding assistants strongly favors a first-class git/repo
surface. `co` currently routes all git work through shell execution. That may be
acceptable, but it should be an intentional product decision rather than an
unexamined omission.

**What to do:**

- Decide whether `co` should keep git inside generic shell execution or add a
  minimal first-class git family.
- Ground the decision in the current source reality: no git tools are registered
  in `_build_filtered_toolset()`, and no capability-state field tracks git
  separately today.
- If the answer is "stay implicit", record the rationale in code comments or a
  decision note tied to this TODO's delivery summary.
- If the answer is "add first-class git", implement only a minimal, high-signal
  set such as status/diff/log/commit staging helpers with the normal approval model.
- Do not add broad repo automation just because peers have it.

**files:**

- decision-only: delivery summary tied to this TODO
- implementation path if approved:
  `co_cli/agent.py`
  `co_cli/tools/` new git module if needed
  tests covering registration and approval behavior

**done_when:**

- Git's place in the taxonomy is explicit and justified.
- There is no longer an accidental gray area between coding workflow and generic shell use.

---

### TASK-7: Update tests around taxonomy and capability metadata

**prerequisites:** TASK-1, TASK-2, TASK-3, TASK-4, TASK-5

Tool-family alignment is easy to regress if tests still assert only flat name
presence. Add targeted checks for the new metadata and keep current behavior
contracts covered.

**What to do:**

- Extend `tests/test_agent.py` to validate family and source metadata for native tools.
- Keep the existing `tool_names` and `tool_approvals` assertions in
  `tests/test_agent.py`; add catalog assertions beside them rather than replacing them.
- Extend `tests/test_bootstrap.py` and `tests/test_capabilities_mcp.py` to
  validate MCP catalog population through `discover_mcp_tools()` and
  `initialize_session_capabilities()`.
- Extend `tests/test_capabilities.py` to validate grouped family/source reporting.
- Keep existing name/approval assertions unless the migration deliberately removes them.
- Run scoped tests first; run full suite before shipping.

**files:**

- `tests/test_agent.py`
- `tests/test_bootstrap.py`
- `tests/test_capabilities.py`
- `tests/test_capabilities_mcp.py`
- any additional focused test file created for the catalog model

**done_when:**

- The test suite catches taxonomy drift, not just missing tool registration.

---

### TASK-8: Align tool names with the canonical taxonomy surface

**prerequisites:** TASK-1, TASK-2, TASK-3, TASK-4

Several current tool names are semantically inconsistent even if their family
placement is corrected. The taxonomy should define not only where tools belong,
but also what the stable external names are.

**What to do:**

- Rename article tools:
  - `recall_article` -> `search_articles`
  - `read_article_detail` -> `read_article`
- Rename todo tools:
  - `todo_write` -> `write_todos`
  - `todo_read` -> `read_todos`
- Update `_ALWAYS_ON_TOOL_NAMES` in `co_cli/agent.py` when renaming todo tools:
  `frozenset({"check_capabilities", "todo_read", "todo_write"})` must become
  `frozenset({"check_capabilities", "read_todos", "write_todos"})` — these names
  control which tools remain visible during approval-resume turns; a silent mismatch
  drops them from the always-on set without any error at registration time.
- Rename subagent tools for role consistency:
  - `run_coder_subagent` -> `run_coding_subagent`
  - `run_thinking_subagent` -> `run_reasoning_subagent`
- Rename Gmail tools for source clarity in the flat namespace:
  - `list_emails` -> `list_gmail_emails`
  - `search_emails` -> `search_gmail_emails`
  - `create_email_draft` -> `create_gmail_draft`
- Grep the repo for all old names and update:
  registration, tests, commands, skill prompts, docs, and any compatibility assumptions.
- If compatibility aliases are kept temporarily, record them explicitly and keep
  the target list above as the end-state validation contract.

**files:**

- `co_cli/agent.py`
- `co_cli/tools/articles.py`
- `co_cli/tools/todo.py`
- `co_cli/tools/subagent.py`
- `co_cli/tools/google_gmail.py`
- tests and docs referencing old names

**done_when:**

- The exported tool surface matches the post-rename target list above.
- Repo-wide grep finds no stale references to superseded canonical names unless
  they are part of an intentional temporary alias path.
- Validation covers both registration and the final public tool-name set.

---

## Dependency Order

```text
TASK-1 canonical tool catalog
  -> TASK-2 explicit native family registration
  -> TASK-3 connectors normalization
  -> TASK-4 workflow/delegation coherence
  -> TASK-5 diagnostics and status reporting
  -> TASK-8 naming alignment
  -> TASK-7 tests

TASK-6 git-family decision can run after TASK-2
```

Recommended ship order:

1. TASK-1
2. TASK-2
3. TASK-3
4. TASK-4
5. TASK-5
6. TASK-8
7. TASK-6
8. TASK-7

---

## Notes For Delivery

- This TODO is intentionally taxonomy-first. It should not trigger a broad tool
  rewrite or connector expansion.
- After implementation, sync-doc should update the relevant DESIGN docs to match
  the new source-of-truth taxonomy.
