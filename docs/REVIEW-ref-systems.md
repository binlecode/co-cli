# Reference Systems: Deep Comparison
_Last updated: 2026-02-28_

This report evaluates 9 peer systems against co-cli's actual architecture and roadmap. Each system is analyzed for what it specifically teaches — grounded in co-cli's current code, not generic patterns.

---

## Co-cli Architecture Snapshot

Co-cli is a personal AI assistant CLI (Python 3.12+, pydantic-ai) with these active work areas:

| Area | Current State | Planned |
|------|--------------|---------|
| **Agent loop** | `run_turn()` state machine — stream → approval while-loop → retry/reflect | Sub-agent delegation (Phase A–C) |
| **Memory** | Grep-based recall; signal detection (keyword precheck + mini-agent); dedup (85%/7d), decay, gravity | FTS5 + sqlite-vec hybrid; articles as first-class kind |
| **Context governance** | Tool output trimming (2K chars); sliding-window at 40 messages; LLM summarisation; background pre-computation | Token-count trigger; tail preservation |
| **Personality** | Static soul seed + 6-mindset pre-classification + planted base memories | — |
| **Approval** | `requires_approval=True` → DeferredToolRequests; safe-prefix bypass; per-tool session "always" | Persistent approval rules to settings.json |
| **Tools** | 17 native tools; `dict[str, Any]` with `display` field; `ToolErrorKind(TERMINAL/TRANSIENT/MISUSE)` | — |
| **MCP** | 3 default servers; stdio transport; tool prefixing; approval inheritance | — |

**Key constraints to keep in mind:**
- `CoDeps` flat scalars only — no Settings objects into tools
- Tools never import settings; access via `ctx.deps`
- Approval UX lives in `_orchestrate.py`, not inside tools
- All tests are functional / no mocks
- No global state in tools

---

## 1. codex (Rust)

**Primary value for co-cli:** Command safety depth + sub-agent thread forking

### What it does

Multi-frontend Rust CLI (TUI + exec + app-server + CLI dispatcher) sharing a monolithic `codex-core`. Actor model: `submit(Op)` queues user input; `next_event()` polls output via async channels. History stored as JSONL rollout files in `~/.codex/threads/`.

### Agent loop (`codex-rs/core/src/codex.rs`)

```
submit(UserTurn) → tx_sub channel
                 → agent processes: build prompt → call model → stream ResponseEvents
                 → safety check → maybe approval → next_event()
```

- **Approval re-entry**: `request_command_approval()` suspends the agent until `notify_approval(id, decision)` — cleaner than co-cli's while-loop because the agent state is truly suspended, not polled
- **Auto-compaction**: Triggers when `total_usage_tokens >= auto_compact_limit`; removes oldest message, retries — token-precise, unlike co-cli's message-count trigger
- **Hooks** (`codex-rs/hooks/src/registry.rs`): `after_agent` and `after_tool_use` Rust function callbacks; result is `Success | FailedContinue | FailedAbort`

### Command safety (`codex-rs/shell-command/src/command_safety/`)

The gold standard. Allowlist approach with deep per-command flag inspection:
- `find`: rejects `-exec`, `-execdir`, `-delete`, `-fls`
- `git`: detects `-c core.pager=...` config override bypasses
- `bash -lc`: recursively parses inner script and validates it
- `rg`: blocks `--pre` (arbitrary subprocess), `-z`/`--search-zip`
- `base64`: rejects `-o`/`--output`

**CVE-2025-66032 direct lesson**: `bash: "allow"` auto-approves `bash -c "rm -rf /"`. Only `bash -lc "single-safe-cmd"` where the inner command is itself safe is acceptable.

### Approval UX (`codex-rs/tui/src/bottom_pane/approval_overlay.rs`)

6 options: `y` (once) / `a` (session) / `p` (write rule to `config.toml` permanently) / `d` (deny) / `n` (abort turn) / network policy approval separate. Syntax-highlighted command display, full context (thread, reason, permission rule). `Ctrl-A` expands to full-screen for long commands.

### Sub-agent thread forking (`codex-rs/core/src/agent/control.rs`)

```rust
spawn_agent_with_options(config, items, SessionSource::ForkParent)
  → materialize parent JSONL rollout
  → snapshot into child: RolloutRecorder::get_rollout_history()
  → inject fork context message into child history
  → create child thread with forked history
```

Child agent gets parent's full history verbatim + a synthetic "you are the newly spawned agent" message. Sub-agent nicknames from `agent_names.txt`, quota enforced by `AgentControl`. This is the pattern co-cli needs for Phase A sub-agent delegation.

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| Command tokenizer + flag inspection | Shell tool safety before approval | High (post-CVE risk) |
| `p` option: write rule to config permanently | Persistent allow rules in `settings.json` | High |
| Token-based compaction trigger | Replace 40-message count in `_history.py` | Medium |
| History forking for sub-agent spawn | `TODO-subagent-delegation.md` Phase A | High |

---

## 2. gemini-cli (TypeScript)

**Primary value for co-cli:** Production approval engine + hierarchical memory with JIT loading + sub-agent isolation

### Agent loop (`packages/core/src/scheduler/scheduler.ts`)

Event-driven state machine: `Validating → Scheduled → AwaitingApproval → Executing → Success|Error|Cancelled`.

Key patterns:
- **Read-only parallelism**: `_isParallelizable()` runs contiguous read-only tools concurrently — `recall_memory` + `web_search` + `read_file` in one turn could run in parallel in co-cli
- **Tail-call chaining**: Tools emit `tailToolCallRequest` to chain without a second LLM round-trip
- **Tool modification**: `ToolModificationHandler` callback lets user edit a shell command in diff view before execution
- **Live output streaming** with PID tracking for process management

### Approval engine (`packages/core/src/scheduler/policy.ts`)

5-tier `ToolConfirmationOutcome`: `ProceedOnce` / `ProceedAlways` / `ProceedAlwaysAndSave` / `ProceedAlwaysTool` / `ProceedAlwaysServer`. MCP-aware: separate per-tool and per-server allowlisting with wildcard patterns (`serverName__*`). `ProceedAlwaysAndSave` writes to disk — the persistent-rules feature co-cli's "a" option should eventually become.

4 approval modes session-wide: `DEFAULT` / `AUTO_EDIT` / `YOLO` / `PLAN`. PLAN mode is read-only — disables all write tools.

### Memory (`packages/core/src/services/contextManager.ts`)

3-tier hierarchy with JIT loading:
1. **Global** (`~/.gemini/GEMINI.md`) — user-wide facts, loaded once
2. **Extension** — plugin-injected context
3. **Project** (`./.gemini/GEMINI.md`) — JIT-loaded on directory access; traverses upward to project root caching each level

**JIT discovery** (`loadJitSubdirectoryMemory`): When agent accesses `X/Y/Z/file.ts`, walks up from `X` loading `.gemini/GEMINI.md` at each level. No up-front scanning. This pattern is relevant for co-cli's `.co-cli/instructions.md` — could extend to project-tree traversal for nested project contexts.

### Built-in tools (36+ in `packages/core/src/tools/`)

Full I/O suite: read/write/edit/glob/ls/grep/ripGrep/shell/web-search/web-fetch/memoryTool/enter-plan-mode/exit-plan-mode/activate-skill/mcp-client/subagent-tool. All inherit `BaseDeclarativeTool`, register JSON schema, have `requires_approval` flag.

### Sub-agents (`packages/core/src/agents/subagent-tool.ts`)

Sub-agents run in isolated `Scheduler` instances with their own tool set + system prompt. Read-only detection: if all sub-agent tools are read-only, marks sub-agent read-only automatically. Sub-agent definitions can be remote or local. Parent calls via `activate_agent` tool, result flows back as tool output.

### System prompt

Layered: preamble → core mandates → tool list (dynamic, includes MCP) → memory sections → sub-agent definitions → skills list → model-specific variants. Per-turn: discovered facts appended as hints. No personality traits — purely task-focused. co-cli's personality system is significantly richer.

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| `ProceedAlwaysAndSave` → write to settings.json | Persistent approval rules | High |
| Read-only tool parallelism (`_isParallelizable`) | Parallelize `recall_memory`+`web_search`+`read_file` in one turn | Medium |
| JIT subdirectory memory loading | Extend `.co-cli/instructions.md` to walk project tree | Low |
| PLAN approval mode (read-only session) | Useful for research turns | Low |

---

## 3. opencode (TypeScript)

**Primary value for co-cli:** Rule-based permission system with 16 types + cascading rejection + session lineage for sub-agents

### Architecture

Multi-surface: CLI + desktop + cloud console. Built on Vercel AI SDK (not proprietary LLM abstraction). Plugin system via NPM. Lightweight per-session state — no database, no history compression.

### Permission system (`packages/opencode/src/permission/next.ts`)

Rule objects: `{permission, pattern, action}` with wildcard matching. 16 named permission types: `read, edit, glob, grep, list, bash, task, skill, lsp, todoread, todowrite, webfetch, websearch, codesearch, external_directory, doom_loop`. Approval outcomes: `once / always / reject`.

**Cascading rejection** (lines 183-193): When user rejects any tool, all pending permissions in the session are auto-rejected. This is the right UX for co-cli — when the user says "no" mid-run, they want the whole chain to stop, not to be asked about each remaining tool.

**Per-agent rulesets** (`packages/opencode/src/agent/agent.ts`): Built-in "plan" agent auto-disables all edit tools except `.opencode/plans/*.md`. Preset permission profiles per agent mode — co-cli's personality mindsets could gate tool permissions similarly (exploration mindset = no `save_memory` without approval).

### Session lineage

Parent session approval auto-approves child sessions. Walks session hierarchy to find first explicit approval/denial. When co-cli implements sub-agent delegation, this pattern prevents asking the same approval twice.

### Multi-provider (`packages/opencode/src/provider/provider.ts`)

20+ bundled providers via Vercel AI SDK. Provider-specific loaders: GitHub Copilot uses `.responses()` for GPT-5+; Azure toggles between completion and chat endpoints; model string `"anthropic/claude-sonnet-4-6"` → `{providerID, modelID}`. Co-cli uses pydantic-ai which covers co-cli's current providers (Anthropic, Gemini, Ollama); opencode's loader pattern is the reference if more providers are needed.

### Config precedence

Managed (enterprise) → `OPENCODE_CONFIG_CONTENT` env → `.opencode/` dirs → project → `OPENCODE_CONFIG` env → global `~/.config/` → remote `/.well-known/opencode`. Co-cli's config is simpler and appropriate for personal use; the `.well-known` pattern is useful if co-cli ever needs org-wide defaults.

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| Cascading rejection on deny | `_handle_approvals`: on "n", cancel all remaining deferred requests | High |
| Session lineage for sub-agents | Parent approval transfers to child agent session | High |
| Per-agent permission presets | Mindset → tool permission profile (e.g., emotional mindset = no shell) | Low |

---

## 4. claude-code (TypeScript)

**Primary value for co-cli:** Hook-based declarative rules + skills as composable agents + post-CVE hardening patterns

### Hook-based permission engine (`plugins/hookify/`)

Rules stored as `.claude/hookify.*.local.md` — YAML frontmatter + markdown body. Events: `bash`, `file`, `stop`, `prompt`, `all`. Actions: `warn` | `block`. Multiple conditions use AND logic. No server restart needed — rules activate on next tool call.

Example:
```yaml
name: block-dangerous-rm
event: bash
pattern: rm\s+-rf
action: block
```

This is more scalable than per-turn approval: learned safety patterns become persistent rules without code changes. Co-cli's current "a" session approval could graduate to a hookify-style rule file in `.co-cli/rules/`.

### Skills as composable agents

Each skill is a mini-agent (markdown frontmatter + system prompt + tool allowlist). Skills can call sub-skills. `/hookify` (no args) analyzes recent conversation and suggests rules — the closest existing system to co-cli's signal detection, but externalized as slash-command rather than automatic.

### Post-CVE-2025-66032 patterns

Policy evaluation before execution (not after). `ToolModificationHandler`: diff-style view lets user edit a shell command before it runs — stronger than co-cli's current yes/no prompt because the user can fix the command rather than just reject it.

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| Persistent declarative rules file (hookify pattern) | `.co-cli/rules/*.md` for approved patterns | Medium |
| Diff view + edit before execution for shell | Enhancement to shell approval UX | Low |
| Skills analysis → rule suggestion | Signal detection → auto-suggest rule instead of just saving memory | Low |

---

## 5. aider (Python)

**Primary value for co-cli:** Recursive summarisation with tail preservation + ConfirmGroup batch approval + tree-sitter repo map pattern

### Agent loop (`aider/coders/base_coder.py`)

REPL → `run_one()` → `send_message()` with reflection loop (up to `max_reflections=3`). Edit format abstraction: udiff / wholefile / editblock / search-replace. Reflection: on failed edit, inject error + retry. URL auto-detection mid-chat.

### Summarisation (`aider/history.py`)

The most refined summarisation algorithm across all peers:

1. Count tokens in all messages (`too_big()`)
2. Preserve tail: calculate how many recent messages fit in `max_tokens // 2` — keep verbatim
3. LLM summarises the head (older messages)
4. If `summary + tail` still over budget: recurse (max depth 3)
5. Aggressive fallback at depth >3 or ≤4 messages: concatenate all as `# USER / # ASSISTANT` blocks, single LLM call
6. **Background threading**: Summarisation spawns in a thread during user think time; result joined at start of next turn

Co-cli's current context governance uses message count (40) not tokens, and lacks tail preservation and background threading. The background pre-computation is already partially implemented (`deps.precomputed_compaction`) — aider's threading pattern is the right completion of that.

### `confirm_ask()` approval UX (`aider/io.py:807-926`)

- `(question, subject)` tuple as question ID for "don't ask again" tracking
- `never_prompts` set: questions permanently suppressed
- **ConfirmGroup**: Groups related confirmations (e.g., "create these 5 files?"). User selects "All" → `group.preference = "all"` → subsequent calls in group skip prompt. Relevant for co-cli when agent needs to save multiple memories or create multiple files.
- `allow_never=True` parameter: adds "(D)on't ask again" to options

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| Token-based trigger + tail preservation | Replace 40-message count in `_history.py`; always preserve last N messages verbatim | High |
| Background summarisation threading | `deps.precomputed_compaction` is the right place; complete the pattern | Medium |
| ConfirmGroup batch approval | When agent saves multiple memories, ask once for batch | Medium |
| `never_prompts` permanent suppression | "Don't ask again for this tool" that writes to settings | Medium |

---

## 6. openclaw (TypeScript)

**Primary value for co-cli:** Production FTS5 + sqlite-vec hybrid — the reference implementation for `TODO-sqlite-tag-fts-sem-search-for-knowledge.md`

### Hybrid search pipeline (`src/memory/manager.ts`, `hybrid.ts`, `manager-search.ts`)

Full query flow:

1. **Query sanitization** → mode selection (FTS-only if no provider, hybrid otherwise)
2. **Vector path**: embed query → `vec_distance_cosine(v.embedding, ?)` → score = `1 - distance`
3. **Keyword path**: `buildFtsQuery()` tokenizes via Unicode word boundaries → quote each token → AND them → BM25 score = `1 / (1 + rank)`
4. **Merge**: deduplicate by chunk ID, `score = vectorWeight * cosineScore + textWeight * BM25score` (default 0.7/0.3)
5. **Temporal decay**: `score *= exp(-λ * age_days)`, λ = `ln(2) / halfLifeDays`
6. **MMR**: Maximal Marginal Relevance — `λ * relevance - (1-λ) * maxJaccardSimilarityToSelected` (Jaccard on token sets)
7. Filter `minScore`, slice to `maxResults`

### Embedding abstraction (`src/memory/embeddings.ts`)

`EmbeddingProvider` interface: `{id, model, maxInputTokens, embedQuery(), embedBatch()}`. Providers: OpenAI, Gemini, Voyage, Mistral, local (node-llama-cpp with EmbeddingGemma-300M). **Auto mode**: try local first → try remotes in order → FTS-only if all keys missing. `fallbackFrom` + `fallbackReason` tracked in status for transparency.

**Embedding cache** (`memory-schema.ts`): SQLite table keyed on `(provider, model, provider_key, hash)`. `provider_key` = hash of `{baseUrl, model, headers-without-auth}` — invalidates when provider config changes, not just when content changes. LRU eviction by `updated_at`, batch upserts in groups of 400.

### Multilingual query expansion (`src/memory/query-expansion.ts`, 806 lines)

For FTS-only mode: 7-language stop word filtering (English 120+, Spanish 60+, Chinese 100+, Japanese 60+, Korean 100+, Arabic 70+, Portuguese 60+). Chinese bigrams; Korean particle stripping; Japanese script separation. After filtering, searches each remaining keyword independently, merges highest score per chunk. Makes FTS useful for conversational queries like "that thing about the API" → extracts ["API"] and searches that.

### Temporal decay (`src/memory/temporal-decay.ts`)

- Path-based dating: `/memory/(\d{4})-(\d{2})-(\d{2})\.md` regex → extract date from filename
- **Evergreen**: `MEMORY.md`, `memory.md`, undated `memory/*.md` files → no decay (return null timestamp)
- Fallback: file `stat.mtimeMs`
- Formula: `score *= exp(-ln(2)/halfLifeDays * age_days)`

This maps directly to co-cli's planned decay: `.co-cli/knowledge/memories/YYYY-MM-DD-slug.md` is already dated; evergreen exemption would apply to `kind: article` entries.

### MMR (`src/memory/mmr.ts`)

Jaccard similarity on token sets (regex `/[a-z0-9_]+/g`). `MMRScore = λ * relevance - (1-λ) * maxJaccardToSelected`. Greedy selection loop. Default `λ=0.7` (relevance-biased). Applied after decay, before returning to caller.

### Data model

`chunks` table: `(id, path, source, start_line, end_line, hash, model, text, embedding, updated_at)`. `source` is `"memory"` or `"sessions"` — co-cli's equivalent would be `"memories"` or `"articles"`. `MemorySearchResult` type: `{path, startLine, endLine, score, snippet, source, citation}`.

### No consolidation — by design

openclaw does not merge similar chunks. Philosophy: trust human curation. Hash-based dedup at write time (same text = same hash = no re-embed). Search-time dedup by ID. For co-cli, this means the signal detection + consolidation work is genuinely novel — openclaw doesn't do it.

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| FTS5 + sqlite-vec weighted merge | Direct implementation target for Phase 1-2 of knowledge TODO | Critical |
| Embedding cache table `(provider, model, provider_key, hash)` | Add alongside Phase 2 sqlite-vec work | High |
| FTS-only graceful fallback | Phase 1 works without embedding provider | High |
| Temporal decay with evergreen exemption | `kind: article` = evergreen; dated memories decay | High |
| MMR post-processing | Optional quality enhancement after Phase 2 | Low |
| Query expansion stop-word filter | Improve FTS-only recall for conversational queries | Medium |

---

## 7. letta (Python)

**Primary value for co-cli:** Three-tier memory model + sleeptime background agents + Anthropic line-numbering for precise memory edits

### Agent loop (`letta/agents/letta_agent_v2.py`, 1435 lines)

Factory-based versioning: `AgentLoop.load()` routes to V2/V3/SleeptimeV4 based on `agent_type` and `enable_sleeptime`. `BaseAgentV2` contract: `build_request()` (dry-run, returns payload) + `step()` (blocking) + `stream()` (async generator). Adapter pattern: `LettaLLMAdapter → LettaLLMRequestAdapter/StreamAdapter` — separates streaming logic from execution model.

Initialized with 10 manager classes: AgentManager, ArchiveManager, BlockManager, RunManager, MessageManager, PassageManager, StepManager, TelemetryManager, etc.

### Three-tier memory

**Tier 1 — Core blocks** (in-context, 20K chars max per block):
- Named blocks: "human", "persona", custom
- Compiled into system prompt via `Memory.compile()`
- Tools: `core_memory_replace` (surgical string swap — requires exact match), `memory_rethink` (bulk rewrite — rejects input containing line-number prefixes to prevent artifacts)

**Tier 2 — Message buffer** (sliding window with LLM summarisation):
- Trigger: `token_usage > context_window_size * 0.9` (`SUMMARIZATION_TRIGGER_MULTIPLIER`)
- `PARTIAL_EVICT_MESSAGE_BUFFER`: evict bottom ~30% → ephemeral LLM generates summary → inject as message[1]; previous summaries appended to next summarisation prompt (recursive)
- `STATIC_MESSAGE_BUFFER`: drop oldest messages (simpler, lossy)

**Tier 3 — Archival passages** (`letta/orm/passage.py`):
- Table `archival_passages`: id, text, embedding, tags (JSON + junction table), metadata, created_at, archive_id
- Dual tag storage: JSON column (fast retrieval) + `passage_tags` junction table (efficient DISTINCT / `any`/`all` query modes)
- Tools: `archival_memory_insert(text, tags)`, `archival_memory_search(query, tags, tag_match_mode, start_datetime, end_datetime, top_k)`
- Agent-initiated: no auto-save, no decay — agent decides what to archive

### Sleeptime background agents (`letta/groups/sleeptime_multi_agent_v4.py`)

Foreground agent (user input) + background agents (timed, independent). Background triggered by `bump_turns_counter_async()` with `sleeptime_agent_frequency`. Background tasks run via `_issue_background_task()` in `finally` block after stream completes. Return list of `run_ids` for tracking.

This is the pattern for co-cli's signal detection: instead of running in the post-turn hook synchronously, signal detection could run as a background task that doesn't delay the next user turn.

### Anthropic line-numbering (`letta/schemas/memory.py:426-451`)

When `model_endpoint_type == "anthropic"` AND agent type is one of `sleeptime_agent, memgpt_v2_agent, letta_v1_agent`: memory blocks rendered with `N→` prefix (Unicode arrow U+2192). Warning banner instructs agent not to include arrows in tool calls. `memory_rethink()` validates no line-number prefixes in input (regex check + warning string check) — prevents artifact leakage.

**Why this matters for co-cli**: When agent edits a memory block (`save_memory` with update), giving it line-numbered context reduces "edit the wrong section" failures. Could apply to co-cli's memory update UX when switching to FTS-indexed storage.

### Layered prompt architecture (`letta/prompts/system_prompts/memgpt_chat.py`)

1. Realism layer: "You are Letta... act like a real person"
2. Control flow layer: heartbeat event system, brain model
3. Functions layer: inner monologue + send_message()
4. Memory layer: describes core/recall/archival access patterns

Each layer has a distinct concern. Co-cli's prompt is closer to this than any other peer (soul seed + rules + per-turn injection), but letta's explicit memory access-pattern documentation in the prompt is missing in co-cli — tools' docstrings cover this but it's not in the system prompt itself.

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| 90% token trigger for summarisation | Replace 40-message count; trigger at model context capacity | High |
| Partial evict + recursive LLM summarisation | Upgrade `_history.py`; pair with tail preservation from aider | High |
| Dual tag storage (JSON + junction) | When FTS5 index is built, add `tags` junction table for efficient `any`/`all` filtering | Medium |
| Sleeptime background pattern | Move signal detection off the synchronous post-turn hook | Medium |
| `memory_rethink` vs `core_memory_replace` semantics | Distinguish macro-edit (rewrite whole entry) from micro-edit (fix substring) in `save_memory` | Low |
| Anthropic line-numbering | For memory update UX once entries have persistent IDs in SQLite | Low |

---

## 8. sidekick-cli (Python)

**Primary value for co-cli:** Closest peer — validates co-cli's pydantic-ai + MCP design choices; provides direct comparison baseline for approval UX and project guide pattern

### Architecture (`src/sidekick/agent.py`, 176 lines)

Minimal pydantic-ai agent: `Agent(model, system_prompt, tools, mcp_servers, deps_type=ToolDeps)` wrapped in `MCPAgent`. Process request: `process_request()` → iterate `agent.run_sync()` nodes → detect `CallToolsNode` vs. text → display thinking panels. Message history maintained via `MessageHistory` dataclass.

The entire agent is 176 lines — co-cli's `_orchestrate.py` alone is ~500 lines, reflecting the additional complexity of approval chaining, retry logic, budget management, and streaming.

### Message history patching (`src/sidekick/messages.py`)

`patch_on_error()` (lines 44-81): on tool failure, inserts synthetic `ToolReturnPart` for every unanswered tool call. This mirrors co-cli's `_patch_dangling_tool_calls()` exactly — independent convergence validates the pattern. Both systems arrived at the same solution for maintaining pydantic-ai message validity after interrupts.

### MCP approval wiring (`src/sidekick/mcp/servers.py`)

`_create_confirmation_callback()` (agent.py:75-109): three-option (y / a / n). `"a"` disables confirmation for that tool by adding to `session.disabled_confirmations`. MCP tools route through same callback via `mcp_tool_confirmation_callback`. Per-tool session disable is simpler than co-cli's auto_approved_tools dict — both achieve the same effect.

### Project guide injection (`src/sidekick/messages.py:92-100`)

Single YAML/text file prepended to every `get_messages_for_agent()` call. Lightweight: no JIT traversal, no hierarchy. Co-cli's `.co-cli/instructions.md` is exactly this pattern — sidekick confirms it's sufficient for single-project use.

### Signal handling (`src/sidekick/repl.py`)

SIGINT → `self.current_task.cancel()` → kill child processes via psutil/pkill recursively. More robust than co-cli's current interrupt handling which relies on pydantic-ai's cancellation — sidekick explicitly kills child processes started by tools, preventing zombies.

### No persistent memory

Sidekick has no memory system. This is the architectural fork: sidekick is stateless + MCP-first; co-cli has signal detection + lifecycle management. The absence of memory in sidekick confirms it's a deliberate scope decision, not a gap — and that co-cli's memory work is the true differentiator.

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| psutil recursive child process kill on SIGINT | Shell tool process cleanup; upgrade `kill_process_tree()` | Medium |
| Validation that `patch_on_error` pattern is correct | Nothing to change — confirms existing `_patch_dangling_tool_calls` | None (validated) |
| Validation that project guide injection is sufficient | `.co-cli/instructions.md` design is confirmed | None (validated) |

---

## 9. mem0 (Python)

**Primary value for co-cli:** LLM-driven fact extraction + contradiction resolution — the implementation pattern for W2/W6 signal detection upgrade

### Architecture (`mem0/memory/main.py`, 2325 lines)

Memory layer (not an agent framework). `add()` → `search()` → `update()` → `delete()`. Concurrent: vector store ops + graph store ops run in parallel `ThreadPoolExecutor`. Returns `{"results": vector_results, "relations": graph_entities}`.

### Two-stage `add()` pipeline (lines 423-589)

**Stage 1 — Fact extraction** (lines 423-456):
- Build narrative from conversation messages
- LLM called with `get_fact_retrieval_messages()` system prompt: "Extract key facts. Return JSON `{facts: [...]}`. Include facts only — no instructions, opinions, or actions."
- Fallback JSON parsing with `extract_json()` if direct parse fails
- Each extracted fact is individually embedded

**Stage 2 — Contradiction resolution** (lines 461-521):
- For each new fact, vector search finds up to 5 similar existing memories
- **UUID hallucination guard** (lines 490-494): Before sending to LLM, replace real UUIDs with integers (`{0: real-uuid-0, 1: real-uuid-1, ...}`). LLM sees `[{"id": "0", "text": "..."}, ...]`. After LLM returns, map integers back to real UUIDs. Prevents LLM from fabricating IDs.
- LLM called with `get_update_memory_messages()`: "Given existing memories and new facts, return JSON with event per memory: `ADD | UPDATE | DELETE | NONE`. For UPDATE include `old_memory` field."
- Execute decisions: ADD → `_create_memory()`, UPDATE → `_update_memory()` with `previous_memory` audit trail, DELETE → `_delete_memory()`, NONE → metadata-only update

**Why this matters for co-cli**: Co-cli's current signal detection (W2/W6) detects signals and saves them. It does NOT check whether a new signal contradicts an existing memory. Mem0's two-stage pipeline — extract facts first, then compare against existing — is the upgrade path. The UUID guard is a one-liner that should be added whenever memory IDs appear in LLM prompts.

### `search()` pipeline (lines 758-856)

1. Embed query
2. Vector search via `_search_vector_store()` (threaded)
3. Optional graph search (threaded, parallel with vector)
4. Optional reranking: `reranker.rerank(query, memories, limit)`
5. Apply `threshold` filter
6. Return `{"results": memories, "relations": graph_entities}`

Advanced filters: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`, `contains`, `icontains`, logical `AND`/`OR`/`NOT`. Session scoping: `user_id`, `agent_id`, `run_id` — at least one required for query.

### Graph layer (`mem0/memory/graph_memory.py`)

LLM extracts entities + relationships from text. Neo4j (production) or Kuzu (embedded). BM25 reranking of graph search results via `rank_bm25`. Runs in parallel with vector ops. Provides entity relationship tracking that pure vector search misses: "the user's sister" → `user --sister--> Alice`. Co-cli doesn't need graph now, but the parallel execution pattern is reusable.

### Session scoping

`_build_filters_and_metadata()` returns `(base_metadata_template, effective_query_filters)`. All three IDs (`user_id`, `agent_id`, `run_id`) stored on write; any subset used for filtering on search. This maps to co-cli's potential multi-context future: `user_id` = global memories, `agent_id` = per-personality memories, `run_id` = per-session ephemeral.

### 24+ vector store backends (`mem0/vector_stores/`)

`VectorStoreFactory` selects backend from config. Base interface: `insert()`, `search(query, vectors, limit, filters)`, `update()`, `delete()`, `get()`, `reset()`. All return `OutputData(id, score, payload)`. For co-cli's Phase 2: this is the abstraction pattern for sqlite-vec, keeping the door open for Qdrant/pgvector if scale requires it.

### What co-cli can adopt

| Pattern | Maps to co-cli | Priority |
|---------|---------------|----------|
| Two-stage: fact extraction → contradiction check | Upgrade W2/W6 signal detection to check against existing memories | Critical |
| UUID hallucination guard (int mapping) | Apply whenever memory IDs sent to LLM | High |
| Parallel vector + graph ops threading | Background signal detection — run async alongside main turn | Medium |
| `user_id`/`agent_id`/`run_id` scoping | Future: per-personality memory namespacing | Low |
| `VectorStoreFactory` abstraction | sqlite-vec backend for Phase 2; keep door open for others | Low |

---

## Cross-Cutting Comparisons

### Approval UX spectrum

| System | Options | Persistent? | Cascade reject? | Batch? |
|--------|---------|------------|-----------------|--------|
| **codex** | y / a / p / d / n / abort | Yes (`p` writes config.toml) | No | No |
| **gemini-cli** | ProceedOnce / ProceedAlways / ProceedAlwaysAndSave / ProceedAlwaysTool / ProceedAlwaysServer | Yes (save to disk) | No | MCP-aware grouping |
| **opencode** | once / always / reject | Yes (config rules) | **Yes** | No |
| **claude-code** | Declarative rules (regex/conditions) | Yes (markdown rules files) | Per-rule block | No |
| **sidekick-cli** | y / a / n | Session (per-tool disable) | No | No |
| **aider** | y / a (all) / s (skip all) / d (never) / n | never_prompts set | No | **Yes** (ConfirmGroup) |
| **co-cli now** | y / a / n | Session only | No | No |
| **co-cli target** | y / a / p / n (with cascade + batch) | Yes → settings.json | Yes | Yes (memory batch) |

### Memory architecture spectrum

| System | Storage | Retrieval | Self-learning | Decay |
|--------|---------|-----------|---------------|-------|
| **openclaw** | SQLite FTS5 + sqlite-vec | Hybrid BM25 + cosine + MMR | No (human curated) | Temporal decay (30d half-life) |
| **letta** | Core blocks (20K) + archival passages (pgvector) | Semantic + tag (any/all) + date range | Agent-initiated | None |
| **mem0** | 24+ vector backends + Neo4j/Kuzu graph | Vector + graph + reranking | LLM fact extraction + contradiction | None |
| **gemini-cli** | Hierarchical markdown files (JIT) | Grep (FTS planned) | MemoryTool (approval-gated) | None |
| **co-cli now** | Flat markdown files (grep) | Substring + fuzzy dedup + one-hop | Signal detection (keyword + mini-agent) | summarize/cut oldest |
| **co-cli target** | SQLite FTS5 + sqlite-vec + markdown | Hybrid BM25 + cosine; LLM contradiction check | Signal detection → two-stage fact/contradiction | Temporal decay with evergreen |

### Context governance

| System | Trigger | Strategy | Tail | Pre-compute |
|--------|---------|----------|------|-------------|
| **letta** | 90% token budget | Partial evict (30%) + recursive LLM summary | Yes (eviction targets oldest) | No |
| **aider** | Token budget exceeded | Recursive: summarise head, keep tail; aggressive fallback | Yes (tail never summarised) | Background thread |
| **codex** | Token count >= auto_compact_limit | Remove oldest + LLM summary | Implicit | No |
| **gemini-cli** | Approaching model context limit | Summarise old turns to facts | Yes | No |
| **co-cli now** | 40 messages (count) | Sliding window + LLM summarisation | Partial | Yes (`precomputed_compaction`) |
| **co-cli target** | 90% token budget | Partial evict + recursive summary | Yes (verbatim) | Background thread (complete pattern) |

### Agent loop model

| System | Model | Parallelism | Sub-agents |
|--------|-------|-------------|------------|
| **codex** | Actor channels (submit/next_event) | Sequential | Thread fork with history snapshot |
| **gemini-cli** | Event-driven state machine + queue | Read-only tools parallel | Isolated Scheduler instances |
| **opencode** | ACP event subscription | Sequential | Config-based with permission inheritance |
| **letta** | Factory-versioned step loop | N/A | Sleeptime background agents |
| **sidekick-cli** | pydantic-ai nodes | N/A | None |
| **co-cli now** | pydantic-ai stream loop | N/A | None |
| **co-cli target** | pydantic-ai + optional parallelism | Read-only tool parallelism | Phase A–C delegation |

---

## Priority Adoptions for co-cli

Ranked by impact × alignment with existing roadmap:

### Tier 1 — Critical (unblocks roadmap)

1. **FTS5 + sqlite-vec hybrid search** (openclaw)
   - Implement `score = 0.7 * cosine + 0.3 * BM25`; graceful FTS-only fallback if no embedding provider
   - Embedding cache: `(provider, model, provider_key, hash)` composite key, LRU by `updated_at`
   - Temporal decay: `exp(-ln(2)/halfLife * age_days)`; `kind: article` = evergreen
   - Files: `co_cli/knowledge/_index.py` (KnowledgeIndex class), `co_cli/tools/memory.py` (update `recall_memory`)

2. **Two-stage signal detection: fact extraction + contradiction check** (mem0)
   - Stage 1: extract candidate facts from conversation via LLM (current signal analyzer does this)
   - Stage 2: before saving, vector search existing memories → LLM decides ADD/UPDATE/DELETE/NONE
   - UUID hallucination guard: map real memory IDs to integers in LLM prompt
   - Files: `co_cli/_signal_analyzer.py` (add Stage 2 after FTS5 index exists)

### Tier 2 — High impact, clear implementation

3. **Token-based context trigger + tail preservation** (letta + aider)
   - Replace 40-message count with 90% token budget
   - Always preserve last `max(4, window // 2)` messages verbatim before summarising head
   - Recursive summarisation (max depth 3) before aggressive fallback
   - Files: `co_cli/_history.py` (`truncate_history_window`)

4. **Persistent approval rules** (codex `p` option / gemini-cli `ProceedAlwaysAndSave`)
   - `"p"` option writes pattern to `.co-cli/settings.json` under `approved_patterns`
   - Shell auto-approve checks `approved_patterns` (longest-prefix match, same as safe_commands)
   - Files: `co_cli/_orchestrate.py` (`_handle_approvals`), `co_cli/config.py` (add `approved_patterns` setting)

5. **Cascading rejection on deny** (opencode)
   - When user selects "n" for any tool, cancel all remaining `DeferredToolRequests` in the turn
   - Currently each deferred request is asked independently — if user says "no" to shell, still asks about `save_memory`
   - Files: `co_cli/_orchestrate.py` (`_handle_approvals`: break loop on first denial, deny all remaining)

### Tier 3 — Medium impact, worthwhile

6. **ConfirmGroup batch approval** (aider)
   - When signal detection proposes saving N memories in one turn, ask "save all N? [y/a/n]"
   - `ConfirmGroup` equivalent: group by tool name in same approval batch
   - Files: `co_cli/_orchestrate.py` (group deferred requests by tool_name before prompting)

7. **Sleeptime pattern for signal detection** (letta)
   - Move signal detection from synchronous post-turn hook to `asyncio.Task` (like `precomputed_compaction`)
   - Join result at start of next `run_turn()` before history processors
   - Files: `co_cli/main.py` (post-turn hook), `co_cli/deps.py` (add `pending_signal_task` field to CoDeps)

8. **Read-only tool parallelism** (gemini-cli)
   - When `recall_memory`, `web_search`, `list_notes` appear consecutively in one turn, run in parallel
   - pydantic-ai's `DeferredToolRequests` doesn't support this natively — would require custom scheduler
   - Defer until after sub-agent work (lower priority than delegation)

### Tier 4 — Low impact or deferred

9. **Session lineage for sub-agents** (opencode) — required for Phase B/C but not Phase A
10. **Query expansion stop-word filter** (openclaw) — valuable for FTS-only mode; add in Phase 1
11. **JIT subdirectory instructions loading** (gemini-cli) — useful for mono-repos; low current demand
12. **Declarative rules file** (claude-code hookify) — good UX, but post-persistent-rules work
13. **MMR diversity re-ranking** (openclaw) — add as optional step in Phase 2 hybrid search
