---
title: Memory Lifecycle
nav_order: 14
---

# Memory Lifecycle System

Cross-session memory involves more than storage and retrieval. This doc covers the lifecycle behaviors: how signals are detected and saved automatically, how memories are kept healthy over time (dedup, consolidation, decay), and how retrieval is filtered. Auto-triggered signal detection, precision edits, tag/temporal filtering, and decay are all implemented. For the broader knowledge system architecture (storage layout, FTS5 index, tool surface, and deferred enhancements), see `DESIGN-knowledge.md`.

---

## Auto-Triggered Signal Detection

### 1. What & How

The signal detector is a post-turn hook in the chat loop that detects behavioral signals in user messages and persists them as memories automatically — without requiring an explicit "remember this" instruction. Signals include explicit corrections plus preference-class signals (preferences, habits, decisions, migrations), all classified by an LLM mini-agent on each successful turn. A former keyword precheck gate was removed so detection no longer depends on brittle substring heuristics.

Confidence gates the save path: high-confidence signals (explicit corrections, and definitive decisions/migrations) are saved immediately and silently; non-high-confidence signals surface to the user for approval. `tag` categorizes the memory — it does not determine whether approval is required.

```
run_turn() → TurnResult
    │
    ├── interrupted=True or outcome="error" → skip
    │
    └── analyze_for_signals(messages, agent.model)
             │
             ├── found=False → skip
             │
             ├── found=True but candidate/tag missing → skip
             │
             └── found=True + candidate + tag
                   ├── confidence="high"
                   │     tags = [tag] + (["personality-context"] if inject else [])
                   │     _save_memory_impl(deps, candidate, tags, None)
                   │     on_status("Learned: …")
                   │
                   └── confidence!="high"
                         prompt_approval("Worth remembering: …")
                              "y"/"a" → tags = [tag] + (["personality-context"] if inject else [])
                                        _save_memory_impl(deps, candidate, tags, None)
                              "n"     → discard
```

### 2. Core Logic

**Per-turn trigger (no keyword gate)**

After each completed turn (`not interrupted` and `outcome != "error"`), the system runs `analyze_for_signals()` directly on the recent conversation window. This avoids false negatives from hardcoded phrase lists and keeps signal policy in one place (`signal_analyzer.md`).

The post-analysis save path in `main.py` is additionally guarded by `signal.found and signal.candidate and signal.tag` before confidence handling.

**Window builder (`_build_window`)**

Extracts recent turns from message history as alternating `User: {text}` / `Co: {text}` lines, capped at 10 lines (~5 turns). Provides enough context for the mini-agent to evaluate the signal without bloating the prompt.

**Signal analyzer mini-agent (`analyze_for_signals`)**

A standalone pydantic-ai `Agent` with structured `output_type=SignalResult` and no tools. Reuses `agent.model` from the main chat agent — no separate model config. System prompt loaded from `co_cli/prompts/agents/signal_analyzer.md` at call time. The agent evaluates the conversation window and returns a `SignalResult`.

Error handling: any exception in `analyze_for_signals` is caught and returns `SignalResult(found=False)`. The mini-agent never crashes the main chat loop.

**`SignalResult` schema:**

```
found: bool
candidate: str | None   — 3rd-person memory (≤150 chars), e.g. "User prefers pytest over unittest"
tag: "correction" | "preference" | None
confidence: "high" | "low" | None
inject: bool            — True when signal is a durable user fact (correction, stated name,
                          tool/style preference, habit) that should be always in-context across
                          sessions; False for ephemeral or session-scoped signals
```

**Confidence classification (from `signal_analyzer.md`):**

*High confidence — explicit corrections, decisions, and migrations. Save immediately, no prompt.*
- "Don't use X", "Do not X", "Stop doing/using X"
- "Never X", "Avoid X"
- "Revert/undo that", "Not like that", "I didn't ask for X", "Please don't X"
- User actively undoing the assistant's output
- "We decided to use X", "I decided to go with X", "We chose X", "We're going with X", "From now on we use X", "Our standard is X"
- "We switched/moved/migrated from X to Y", "We replaced/dropped X", "We stopped using X"

*Low confidence — implicit preferences, frustrated reactions, and habit disclosures. Model surfaces to user for approval.*
- "Why did you X?", "That was wrong", "That's not what I wanted"
- "I prefer X", "Please use X", "Always use X", "Use X instead"
- "I always/usually/tend to X", "We typically/normally X"
- Repeated frustration about the same topic

Note: The `tag` field categorizes the memory type. The `confidence` field determines whether approval is required. The code auto-saves only `confidence == "high"`; any other confidence value follows the approval path.

**`inject` routing:** The `inject` field determines whether a saved signal joins the
always-in-context injection layer. When `True`, `"personality-context"` is appended to the
tags written to `_save_memory_impl()`. `_load_personality_memories()` (called on every turn
via `add_personality_memories` in `agent.py`) injects up to 5 `personality-context`-tagged
items as a `## Learned Context` block. `add_personality_memories` is a no-op (`return ""`)
when `ctx.deps.personality` is not set — `personality-context` memories are only injected
while a personality is active. Corrections always `inject: True`. Durable preferences
(name, tool, style, habit) → `True`. Ephemeral decisions ("use X for this task") → `False`.
The top-5 count cap in `_load_personality_memories()` is the active budget control; char-cap
tag-eviction was evaluated and deferred as over-engineered at current scale.

**Guardrails — do NOT flag:**

Hypotheticals ("if you were to use X..."), teaching moments ("here's what NOT to do"), capability questions ("can you use X?"), single negative word without behavioral correction context, general conversation, and any sensitive content (health, credentials, financial, personal data). These constraints are encoded in the `signal_analyzer.md` system prompt — enforced at the LLM layer, not in code. Sensitive content prevention is therefore probabilistic; model misclassification is the accepted risk at MVP stage.

**`_save_memory_impl(deps, content, tags, related)`**

Extracted from `save_memory()` so the signal path can write without a `RunContext`. Takes `CoDeps` directly. Shared write path for both the explicit tool and the auto-detector:

```
load memories from .co-cli/knowledge/
dedup-check against recent memories (window=memory_dedup_window_days, threshold=memory_dedup_threshold)
    compare against recent subset (last 7-day window by default, capped to 10 newest)
    duplicate found (similarity ≥ threshold) → update existing entry (merge tags, overwrite content)
    no duplicate → write new {id:03d}-{slug}.md
```

Does **not** trigger decay. Decay checks run only inside the explicit `save_memory()` tool call. Decay strategy is configurable (`summarize` concatenation or `cut` deletion) and uses `RunContext` because it reads `ctx.deps` settings and may perform index maintenance.

**Post-turn hook placement (`main.py`)**

The signal check runs immediately after `message_history = turn_result.messages`. Guard conditions: `not turn_result.interrupted` and `turn_result.outcome != "error"`. Interrupted or error turns skip detection — conversation state is incomplete and signals would be unreliable.

### 3. Config

No dedicated signal detection settings. The detector reuses `agent.model` from the main chat agent and writes through `_save_memory_impl` using existing memory settings.

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `memory_dedup_window_days` | `CO_CLI_MEMORY_DEDUP_WINDOW_DAYS` | `7` | Lookback window for duplicate detection in `_save_memory_impl` (applies to both explicit saves and auto-saved signals) |
| `memory_dedup_threshold` | `CO_CLI_MEMORY_DEDUP_THRESHOLD` | `85` | Fuzzy similarity threshold (0–100) for duplicate consolidation in `_save_memory_impl` |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_signal_analyzer.py` | `_build_window`, `analyze_for_signals`, `SignalResult` |
| `co_cli/prompts/agents/signal_analyzer.md` | System prompt: signal types, confidence rules, guardrails, output format, examples |
| `co_cli/tools/memory.py` | `_save_memory_impl` — shared write path used by both `save_memory()` tool and signal detector |
| `co_cli/main.py` | Post-turn hook integration in `chat_loop()`, after `message_history = turn_result.messages` |

---

## Precision Memory Edit Tools

### 1. What & How

Two agent-facing tools for targeted in-place edits by slug under `.co-cli/knowledge/*.md`
(typically memories; article files are not blocked by kind checks). `save_memory` is a
full-overwrite-by-dedup-slug path — it replaces whole bodies and risks
hallucinated content in sections the model didn't intend to change. `update_memory` and
`append_memory` give the agent a safe, surgical write path for corrections and extensions.

### 2. Core Logic

**`update_memory(ctx, slug, old_content, new_content)`**

Guards applied in order before the write:
0. Slug lookup — `next(glob("*.md"), None)` for matching stem; missing slug raises
   `FileNotFoundError`. Caller must use `save_memory` to create new memories.
1. Line-number prefix rejection — rejects `old_content`/`new_content` containing Read-tool
   artifacts (`\d+→ ` or `Line \d+: `). Without this guard, an agent that copies text from a
   line-numbered read output would pass a non-matching string and receive an opaque
   "not found" error it can't self-diagnose.
2. Tab normalization — `expandtabs()` on both body and arguments before matching. Files written
   by different code paths may use tabs where the agent's argument uses spaces.
3. Existence check — zero occurrences: raise `ValueError`. Silent no-op would let the agent
   believe a correction succeeded when it didn't.
4. Uniqueness check — more than one occurrence: raise `ValueError` with body line numbers.
   `str.replace()` without this guard rewrites every match silently.
5. Replace first occurrence. Write back with `fm["updated"] = now`. FTS re-index if index present.

Returns `{"display": "Updated memory '{slug}'.\n{updated_body}", "slug": slug}`.

**`append_memory(ctx, slug, content)`**

Slug lookup → missing slug raises `FileNotFoundError` (append to non-existent memory is a
caller bug; use `save_memory` to create). Strips trailing whitespace from the body
(`rstrip()`), then appends `"\n" + content`, writes back with
`fm["updated"] = now`. FTS re-index if index present.

Returns `{"display": "Appended to '{slug}'.", "slug": slug}`.

Both tools: `requires_approval=False` — agent-managed knowledge state; equivalent to
`save_memory` from a trust model perspective, but targeted rather than full-body replacement.

### 3. Config

No dedicated configuration. Both tools write through the same file path as `save_memory`.

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/memory.py` | `update_memory`, `append_memory`, module-level `_LINE_PREFIX_RE` / `_LINE_NUM_RE` guards |
| `co_cli/agent.py` | Tool registration: `agent.tool(update_memory, requires_approval=False)`, same for `append_memory` |

---

## Tag Filtering and Temporal Search

### 1. What & How

`KnowledgeIndex.search()` supports exact multi-tag filtering and date-range filtering via `created_after` / `created_before`. These parameters are exposed by `recall_memory` and `recall_article` (not `search_knowledge`).

### 2. Core Logic

**Python-side tag filtering**

Tags are stored as space-separated TEXT in `docs.tags`. No SQL junction table exists. Tag filtering is applied in Python after the FTS5 query returns rows.

When a `tags` filter is requested, `_fts_search()` over-fetches from SQLite (`LIMIT = limit * 20`) to give the Python filter enough candidates. After fetching, rows are filtered in a list comprehension:

- `tag_match_mode="all"`: `tag_set <= {t for t in (row["tags"] or "").split() if t}` — Python subset check (every requested tag must be present)
- `tag_match_mode="any"`: `tag_set & {t for t in (row["tags"] or "").split() if t}` — Python intersection (at least one requested tag must match)

The same Python-side filtering logic is applied in `_vec_search()` for the hybrid backend.

Duplicate tags in the filter list are deduplicated with `dict.fromkeys(tags)` before query construction — prevents a doubled tag from causing false zero results when the post-filter set check compares against a tag set.

There is no `doc_tags` table in the schema — confirmed absent from `_SCHEMA_SQL` in `knowledge_index.py`.

**Temporal filtering**

`created_after` / `created_before` accept ISO 8601 strings (date-only or datetime) and are added as `AND d.created >= ?` / `AND d.created <= ?` WHERE clauses in both FTS and vector search paths. In grep fallback mode, `recall_memory` and `recall_article` apply equivalent in-Python filters on `m.created`.

The `updated` field is intentionally excluded from date-range filtering. In memory recall, gravity updates `updated` timestamps of pulled direct matches, so filtering by `updated` would be unstable for temporal queries.

### 3. Config

No dedicated config. `tag_match_mode` and date params are per-call arguments.

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/knowledge_index.py` | `_fts_search()` Python-side tag filtering (over-fetch + list comprehension), `_vec_search()` same pattern, temporal WHERE clauses |
| `co_cli/tools/memory.py` | `recall_memory` — `tag_match_mode`, `created_after`, `created_before` params |
| `co_cli/tools/articles.py` | `recall_article` — same params |

---

## Design Decisions — Not Adopted

Patterns from Letta's production memory system that were considered and explicitly not adopted:

| Pattern | Rationale |
|---------|-----------|
| **Full Block infrastructure** | `decay_protected` frontmatter + `personality-context` tag cover the always-in-context use case without a Block/PostgreSQL stack |
| **Sleeptime consolidation agent** | co-cli's lifecycle (gravity + decay + dedup-on-write) is the consolidation mechanism. Letta needs sleeptime because its archival store is permanent with no decay |
| **PARTIAL_EVICT mode** | One context governance mode (`truncate_history_window`) is sufficient; a second mode with different triggering semantics introduces ambiguous state |
| **`memory_apply_patch` multi-block diff** | `update_memory` + `append_memory` cover all realistic edit cases with simpler contracts |
| **Heartbeat chaining** | pydantic-ai's natural multi-tool turns are equivalent; no explicit heartbeat parameter needed |
| **Session summary indexing** | Adds file persistence + config wiring at compaction time. Deferred: no observed recall gap that justifies the added complexity at current scale |
| **Middle-truncation summarizer fallback** | `_run_summarization_with_policy` already classifies and retries transient errors; `_static_marker` is the terminal fallback. Truncate-and-retry adds complexity without observed failure cases |
| **Parallel-safe tool execution** | pydantic-ai's tool execution model is not verified to support custom dispatchers. Deferred pending measured tool latency bottleneck |
