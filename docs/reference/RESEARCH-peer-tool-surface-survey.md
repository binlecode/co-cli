# RESEARCH: Peer Tool Surface Survey vs co-cli

Scan date: 2026-04-15 (updated against v0.7.149)

## 1. Scope

This document replaces the earlier peer-specific tool research notes with one tool-only survey and compares them to the current `co-cli` implementation.

Peer systems covered in this survey:

- `fork-cc`
- `gemini-cli`
- `codex`
- `opencode`
- `hermes-agent`

Current `co-cli` implementation checked directly:

- [co_cli/agent/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/agent/_core.py)
- [co_cli/agent/_native_toolset.py](/Users/binle/workspace_genai/co-cli/co_cli/agent/_native_toolset.py)
- [co_cli/agent/_mcp.py](/Users/binle/workspace_genai/co-cli/co_cli/agent/_mcp.py)
- [co_cli/deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [co_cli/context/tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- [co_cli/context/_deferred_tool_prompt.py](/Users/binle/workspace_genai/co-cli/co_cli/context/_deferred_tool_prompt.py)
- [co_cli/tools/files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py)
- [co_cli/tools/shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py)
- [co_cli/tools/tool_io.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_io.py)
- [co_cli/tools/web.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/web.py)
- [docs/specs/tools.md](/Users/binle/workspace_genai/co-cli/docs/specs/tools.md)

This pass intentionally excludes slash commands, personality overlays, and general capability surface except where they directly shape the tool interface exposed to the model.

## 2. co-cli Latest Baseline

The current `co-cli` tool surface is built from native registrations plus optional MCP toolsets, then exposed through a single filtered combined toolset. The SDK adds `search_tools` automatically for deferred discovery; `co-cli` does not register a native `search_tools` function itself.

### 2.1 Registration and execution axes actually present in code

`ToolInfo` is the single source of truth for all SDK wiring. All SDK flags passed to `FunctionToolset.add_function()` are derived directly from `ToolInfo` fields — there is no separate execution-time layer.

`ToolInfo` stores:

| Axis | Current `co-cli` shape |
|------|-------------------------|
| Visibility | `ALWAYS` or `DEFERRED` — drives `defer_loading` |
| Approval | `approval=True/False` — drives `requires_approval` |
| Source | `NATIVE` or `MCP` |
| Integration gate | optional integration label such as `obsidian`, `google_gmail`, or MCP server prefix |
| Result budget | per-tool `max_result_size` |
| Read-only | `is_read_only=True/False`; invariant: `is_read_only` implies `is_concurrent_safe` |
| Concurrency safety | `is_concurrent_safe=True/False` — drives `sequential` (False → sequential=True) |
| Retries | per-tool `retries` override, else agent default — drives `retries` |

Approval scoping is also more nuanced than the registration bit. `tool_approvals.py` can remember approvals across four subject kinds:

- `shell`
- `path`
- `domain`
- `tool`

Current native tools actively exercise `shell`, `path`, and `tool`. The `domain` branch exists in approval resolution, but no current native tool is registered with approval on a per-domain basis.

### 2.2 Current native and MCP loading shape

Current `co-cli` bootstrap builds:

- one native `FunctionToolset` in [co_cli/agent/_native_toolset.py](/Users/binle/workspace_genai/co-cli/co_cli/agent/_native_toolset.py)
- zero or more MCP `DeferredLoadingToolset` wrappers in [co_cli/agent/_mcp.py](/Users/binle/workspace_genai/co-cli/co_cli/agent/_mcp.py)
- one combined filtered toolset in [co_cli/agent/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/agent/_core.py)

Native registration currently breaks down as:

| Bucket | Tools |
|--------|-------|
| Always-visible reads and introspection | `check_capabilities`, `write_todos`, `read_todos`, `search_memories`, `search_knowledge`, `search_articles`, `read_article`, `list_memories`, `glob`, `read_file`, `grep`, `web_search`, `web_fetch`, `run_shell_command` |
| Deferred writes and state changes | `write_file`, `patch`, `save_article`, `start_background_task` |
| Deferred read/control tools | `check_task_status`, `cancel_background_task`, `list_background_tasks`, `session_search` |
| Deferred delegation | `delegate_coder`, `delegate_researcher`, `delegate_analyst`, `delegate_reasoner` |
| Deferred optional integrations | Obsidian, Google Drive, Gmail, Calendar tools when config is present |

If both optional native integration groups are enabled, the native surface is:

- 14 always-visible tools
- 12 deferred core tools
- 10 deferred integration tools
- 36 registered native tools total

MCP tools are separate from that count. They are discovered later, normalized into `ToolInfo`, and always enter the index as `visibility=DEFERRED`.

### 2.3 Structural profile

The current `co-cli` surface is best described as:

- read-first by default
- write tools hidden behind deferred discovery
- approval concentrated on file writes, article writes, background task start, and Gmail draft creation
- shell approval is hybrid: `run_shell_command` is always visible, but the tool body can still raise `ApprovalRequired` depending on command policy
- shell safety split between registration-time exposure and in-tool policy classification
- read-only and concurrency safety explicitly declared per tool in the registry (`is_read_only`, `is_concurrent_safe`); `is_read_only` implies `is_concurrent_safe` (enforced at registration)
- optional integrations only registered when credentials or paths exist
- delegation exposed as four fixed-role tools, not a general agent lifecycle API

## 3. Comparison Axes

The peer notes converge on six high-signal axes:

1. tool contract and schema metadata
2. model-visible surface and deferred discovery
3. approval and permission memory
4. execution and concurrency controls
5. tool result shaping and replay
6. catalog composition

## 4. Survey by Axis

### 4.1 Tool contract and schema metadata

| System | What is explicit in the tool contract | Comparison to current `co-cli` |
|--------|----------------------------------------|--------------------------------|
| `fork-cc` | rich per-tool traits: `isConcurrencySafe`, `isReadOnly`, `isDestructive`, `interruptBehavior`, aliases, result-size cap | richer contract than `co-cli`; `co-cli` encodes only visibility, approval, source, integration, and result budget in registry metadata |
| `gemini-cli` | tool `kind`, read-only classification, display fields, invocation object, validation path | richer taxonomy than `co-cli`; `co-cli` has no first-class tool kind or read-only enum |
| `codex` | typed `ToolSpec`, explicit tool variants, output schema, handler trait with `is_mutating()` | closest architectural peer to `co-cli` on registry separation, but `codex` has a stronger handler contract |
| `opencode` | compact tool definition with shared execution context and schema validation | less metadata-heavy than `fork-cc` and `gemini-cli`, but still exposes a common execution context not present in `co-cli` |
| `hermes-agent` | `ToolEntry` metadata plus toolset membership, `check_fn`, async flag, emoji, per-tool result-size cap | richer than `co-cli` on exposure metadata and execution decoration, but less explicit than peers with a first-class read/write/destructive taxonomy |
| `co-cli` | `ToolInfo` is the single source of truth for all SDK wiring; stores visibility, approval, source, integration, result budget, `is_read_only`, `is_concurrent_safe`, and retries; no destructive/interrupt-behavior flags and no tool-kind enum (read/edit/delete/search/agent) | `is_read_only` and `is_concurrent_safe` close the gap with `fork-cc`/`gemini-cli`/`codex` on those two axes; still missing destructive flag and tool-kind taxonomy |

High-signal read: `co-cli` now has explicit read-only and concurrency-safety axes in its registry, narrowing the gap with peers. The remaining contract gap is the absence of a destructive flag, interrupt-behavior hint, and a first-class tool-kind enum.

### 4.2 Model-visible surface and deferred discovery

| System | Deferred/discovered surface | Comparison to current `co-cli` |
|--------|-----------------------------|--------------------------------|
| `fork-cc` | explicit deferred tools plus a dedicated `ToolSearchTool`; search ranks with `searchHint` and prompt text | similar direction; `co-cli` also hides a class of tools and relies on search, but with a simpler category prompt + SDK search path |
| `gemini-cli` | active registry filtered before schema exposure; external command can discover more tools | broader discovery sources than `co-cli`; no inspected ranked free-text tool search path like `fork-cc` or `codex` |
| `codex` | explicit `defer_loading`; direct tool specs vs search-mediated staged exposure | strongest match to `co-cli` in staged exposure design |
| `opencode` | dynamic loading from built-ins, plugins, MCP; no inspected explicit deferred flag | more dynamic loading sources, less explicit visibility policy |
| `hermes-agent` | toolset-based exposure with enabled/disabled toolset resolution, then `check_fn` filtering; MCP and plugins can extend the registry after base discovery | broader and more dynamic than `co-cli`; it stages via toolsets rather than `ALWAYS` / `DEFERRED` visibility |
| `co-cli` | `ALWAYS` vs `DEFERRED`; SDK auto-adds `search_tools`; category-awareness prompt enumerates deferred native categories and integration labels | simpler than `fork-cc` and `codex`, but the same core pattern: keep mutation and integrations out of turn-one exposure |

High-signal read: `co-cli` is converging on the `fork-cc`/`codex` model of staged tool exposure, but with less ranking and less metadata attached to the search layer.

### 4.3 Approval and permission memory

| System | Approval shape | Comparison to current `co-cli` |
|--------|----------------|--------------------------------|
| `fork-cc` | multi-source permission system with persistent rule sources and permission modes | much broader policy surface than `co-cli` |
| `gemini-cli` | policy engine, confirmation bus, approval modes, tool-specific confirmation detail types | richer UI and policy state than `co-cli` |
| `codex` | handler-level mutation gating plus sandbox override request types | more explicit sandbox negotiation than `co-cli` |
| `opencode` | rule evaluation with pending permission requests and session-level `always` approval | closest behavioral match to `co-cli` on session-scoped remembered approval |
| `hermes-agent` | regex/pattern-based dangerous-command approval on terminal execution (per-session and permanent); separate `clarify` tool for mid-sequence open-ended or multiple-choice user input | cleaner separation than `co-cli`: approval handles danger, `clarify` handles ambiguity; `co-cli` approval is more structured per-subject but has no mid-sequence clarification path |
| `co-cli` | deferred approval loop with remembered session rules scoped to `shell`, `path`, `domain`, `tool`; current native approvals mainly use shell/path/tool | simpler than `fork-cc` and `gemini-cli`, but more structured than plain per-tool approval |

High-signal read: `co-cli`'s approval model is one of its stronger differentiators. It is not just "tool requires approval"; the resolver supports scoped remembered approvals for command utility, write directory, domain, and generic tool name, even though current native approvals mainly exercise shell/path/tool scopes.

### 4.4 Execution and concurrency controls

| System | Execution / concurrency surface | Comparison to current `co-cli` |
|--------|---------------------------------|--------------------------------|
| `fork-cc` | explicit `isConcurrencySafe` and orchestration partitioning | stronger declarative concurrency signal than `co-cli` |
| `gemini-cli` | scheduler-driven confirmation and execution flow | execution-heavy, but less obviously centered on file-level locking |
| `codex` | handler `is_mutating()`, tool-call gating, parallel-support registration | richer orchestration surface than `co-cli` |
| `opencode` | per-session tool wrappers, transcript tool-part state, doom-loop protection | richer execution-state tracking than `co-cli` |
| `hermes-agent` | central registry dispatch, sync/async bridging, plugin pre/post hooks, and agent-loop interception for stateful tools | broader dispatch surface than `co-cli`, but less explicit than `co-cli` on file-mutation correctness primitives |
| `co-cli` | `is_concurrent_safe` in `ToolInfo` drives `sequential` SDK wiring; `ResourceLockStore` shared across agents; file staleness checks (mtime); read-before-write enforcement; approval-resume narrowing | `is_concurrent_safe` is now a first-class registry axis, not an ad hoc SDK flag; correctness machinery remains concrete rather than declarative |

High-signal read: `co-cli` now declares concurrency safety per tool in the registry (`is_concurrent_safe`), which is the same signal that `fork-cc`'s `isConcurrencySafe` provides. The remaining gap is that peers express concurrency as a scheduling hint to an orchestrator, while `co-cli` translates it directly to SDK-level `sequential` execution. Additional correctness mechanisms:

- cross-agent resource locks (`ResourceLockStore`)
- read-mtime staleness checks
- read-before-write enforcement via `file_partial_reads`

### 4.5 Tool result shaping and replay

| System | Result shape | Comparison to current `co-cli` |
|--------|--------------|--------------------------------|
| `fork-cc` | per-tool UI components and richer result mapping hooks | more UI-specialized than `co-cli` |
| `gemini-cli` | explicit output distillation and markdown/output-update flags | more post-processing surface than `co-cli` |
| `codex` | typed outputs per handler, log previews, response-item conversion | richer output contract than `co-cli` |
| `opencode` | transcript tool-part lifecycle and replay normalization | more explicit replay state than `co-cli` |
| `hermes-agent` | JSON-string tool contract with `tool_result` / `tool_error` helpers and per-tool result-size caps | structurally simple like `co-cli`, but less typed and more uniformly JSON-first |
| `co-cli` | `tool_output()` wrapper, metadata side-channel, per-tool truncation budget, spill-to-storage for oversized results, centralized display formatting | simpler, but disciplined and uniform |

High-signal read: `co-cli`'s result model is intentionally minimal. The main strengths are:

- one return wrapper
- consistent metadata path
- per-tool result budgets
- centralized display shaping

The main thing peers expose that `co-cli` does not is richer typed output state per tool family.

### 4.6 Catalog composition

| System | Catalog emphasis | Comparison to current `co-cli` |
|--------|------------------|--------------------------------|
| `fork-cc` | broadest operational surface: files, shell, web, agents, plan mode, user interaction, MCP resources, worktrees, cron, LSP, browser | substantially broader than `co-cli` |
| `gemini-cli` | balanced core set: files, shell, web, memory, ask-user, skills, plan mode, trackers | broader control-plane surface than `co-cli` |
| `codex` | shell-heavy plus planning, explicit user input, multi-agent lifecycle, MCP resources, image generation | broader orchestration and connector surface than `co-cli` |
| `opencode` | compact core set with plugins, skills, LSP, plan mode, batch | closer in size to `co-cli`, broader in IDE-style tooling |
| `hermes-agent` | broad operational and personal-agent surface: web, terminal, files, browser, vision, image generation, memory, session search, clarify, code execution, delegation, cron, messaging, Home Assistant, MCP, plugins | broader than `co-cli` and notably more platform-oriented |
| `co-cli` | strong local knowledge and personal productivity surface: memories, articles, session search, todos, background tasks, Google tools, Obsidian | narrower core coding surface than peers, deeper built-in personal knowledge/inbox/calendar surface than most peers |

High-signal read: `co-cli` is not trying to match the widest coding-agent tool surface. Its catalog is comparatively narrow on IDE/browser/interactive-control tools and comparatively strong on memory, articles, transcript search, and personal integrations.

## 5. Direct Comparison Summary

### 5.1 Where `co-cli` already matches converged peer patterns

- staged exposure of non-core tools rather than exposing the full catalog on turn one
- centralized registry metadata instead of ad hoc tool registration; `ToolInfo` is the single source of truth for all SDK wiring
- explicit approval handling in the orchestration layer rather than inside tools
- delegation as part of the tool surface
- result-size control and standardized result wrapping
- `is_read_only` and `is_concurrent_safe` as first-class `ToolInfo` axes — matching `fork-cc`'s `isReadOnly`/`isConcurrencySafe` and `gemini-cli`'s read-only classification

### 5.2 Where `co-cli` is materially narrower than peers

- no LSP, browser, or image-generation tools
- no general multi-agent lifecycle API such as spawn/send/wait/close
- no in-app interrupt path for running delegation subagents — if a `delegate_*` call hangs or runs longer than expected, the only recourse is Ctrl+C on the whole process; hermes covers this via per-thread cooperative polling (`is_interrupted()`), fork-cc via per-tool `interruptBehavior='cancel'`; worth tracking if delegation becomes a primary workflow, but does not compromise current safety or effectiveness since delegation failures surface as `ModelRetry`, not silent data corruption

### 5.3 Where `co-cli` has a distinct profile

- approval rules are scoped to subject type, not just tool name (shell / path / domain / tool)
- `is_read_only` implies `is_concurrent_safe` — invariant enforced at registration, not by convention
- file mutation correctness reinforced by registry-declared sequential execution, shared cross-agent resource locks, and mtime staleness checks
- optional integrations are registered only when usable
- the default always-visible surface is deliberately read-first
- the built-in non-coding surface is unusually strong for a CLI agent:
  memories, articles, session search, todos, background tasks, Obsidian, Drive, Gmail, Calendar
- compared to Hermes specifically, `co-cli` prefers a smaller explicit registry over import-time self-registration and broad platform tool bundles
- the actual bootstrap path is "native function toolset + deferred MCP toolsets + one combined approval-resume filter", not a native-only registry
- no plan-mode entry/exit tools by design — plan mode is a TUI/slash-command concern, not a model-visible tool
- no destructive flag or interrupt-behavior hint by design — `approval=True` covers irreversible tools; interrupt handling is uniform (KeyboardInterrupt → `_build_interrupted_turn_result()`), so per-tool interrupt policy adds no value
- no mid-turn clarification tool by design — in a REPL with persistent transcript history, the model asks at turn boundaries via response text and re-entry cost is near zero; hermes needs `clarify` to bridge gateway/cron contexts where turns are expensive and session state is not guaranteed
- no tool-kind enum by design — `is_read_only` and `is_concurrent_safe` each drive concrete SDK behavior; a kind enum (`read/edit/delete/search/agent`) would be decorative metadata with no current consumer, violating the "add abstractions only when a concrete need exists" principle
- no first-class MCP resource tools by design — native tools (`read_file`, `web_fetch`, `read_drive_file`, etc.) cover the resource-reading patterns; connected MCP servers (context7) expose functionality as tools, not resource URIs; first-class resource tools would only matter for MCP servers that expose addressable resources rather than callable tools
- no peer-level hook runtime by design — `CoToolLifecycle` (`before_tool_execute` / `after_tool_execute`) handles the concrete behaviors that matter (path resolution, OTel enrichment); pluggable hooks are needed for plugin ecosystems (hermes), not for a personal CLI where tool behavior is extended directly in source
- no Hermes-style toolset abstraction by design — integration gating, ALWAYS/DEFERRED visibility, and MCP as separate deferred toolsets compose the model-visible surface without named toggleable groups; toolset switching is needed for plugin ecosystems, not for a personal CLI with a config-fixed surface

## 6. Bottom Line

If the question is "which peer is `co-cli` structurally closest to on tool loading and exposure?", the answer is `codex`, with `fork-cc` as the other strong reference point.

If the question is "what is the main difference between `co-cli` and the peers?", the answer is that current `co-cli` optimizes for a smaller, read-first, approval-scoped tool surface with personal knowledge integrations, while the peers generally expose a broader control plane with more explicit tool taxonomies, hook systems, and user-interaction primitives.
