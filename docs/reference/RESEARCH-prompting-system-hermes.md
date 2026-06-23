# RESEARCH: Hermes Agent Prompting System

**Source:** `~/workspace_genai/hermes-agent` — refreshed to HEAD (commit `bb7ff7dc3`, Mon Jun 22 2026)  
**Scope:** System prompt assembly, per-model routing, tool/skill/memory injection, agent loop, caching strategy, platform variation, architectural comparison

---

## 1. Top-Level Architecture

Hermes assembles the system prompt **once per session** (not per turn) and reuses it verbatim across all turns. This is the defining architectural constraint: upstream prompt caches stay warm as long as the system prompt bytes are identical. The one exception is context compression — a compaction event invalidates the cache and forces a rebuild.

**Assembly formula** (`agent/system_prompt.py:113-467`, `build_system_prompt_parts()`):

```
system_prompt = stable + "\n\n" + context + "\n\n" + volatile
```

Three ordered tiers:

| Tier | Content | Cache stability |
|---|---|---|
| `stable` | Identity (SOUL.md or DEFAULT_AGENT_IDENTITY), tool guidance, skills index, environment hints, platform hints, model-family operational guidance | Constant for the process lifetime (fully cacheable) |
| `context` | Caller-supplied `system_message` + context files loaded from cwd (AGENTS.md, .hermes.md, CLAUDE.md, .cursorrules) | Session-stable (changes only between sessions or when cwd changes) |
| `volatile` | Memory snapshot, USER.md profile, external memory provider block, timestamp line (date-only), session ID, model/provider identity | Changes per session; date-only precision to preserve byte-stability across turns |

The prompt is cached on `agent._cached_system_prompt`. The agent loop reads this cached string verbatim every turn — it is never rebuilt during a session unless `invalidate_system_prompt()` is called after compression (`agent/system_prompt.py:496-504`).

**Date precision is deliberately day-only** (`agent/system_prompt.py:454`): minute-precision timestamps would invalidate prefix-cache KV on every rebuild path, killing the caching benefit for the volatile tier.

---

## 2. Per-Model Prompt Routing

Hermes does **not** use separate per-model base prompt files. There is a single `DEFAULT_AGENT_IDENTITY` constant (`agent/prompt_builder.py:123-131`) that is the fallback if no `SOUL.md` exists. Instead, model-specific behavior is achieved through **additive guidance blocks** conditionally injected into the stable tier.

**SOUL.md as identity override** (`agent/prompt_builder.py:1713-1741`, `load_soul_md()`):  
The user can place a `SOUL.md` at `~/.hermes/SOUL.md` to completely replace the default identity. This is the highest-priority identity source. The Docker image ships a template `SOUL.md` at `docker/SOUL.md`.

**Model-family conditional blocks** (`agent/system_prompt.py:231-258`):  
Instead of separate files per model, the same system prompt gains or loses guidance blocks based on model name substrings:

```python
TOOL_USE_ENFORCEMENT_MODELS = ("gpt", "codex", "gemini", "gemma", "grok", "glm", "qwen", "deepseek")
```

When the configured model matches (or `tool_use_enforcement: true` in config), `TOOL_USE_ENFORCEMENT_GUIDANCE` is injected. Then:

- `gemini` or `gemma` in model name → also inject `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` (`agent/system_prompt.py:250-251`)
- `gpt`, `codex`, or `grok` in model name → also inject `OPENAI_MODEL_EXECUTION_GUIDANCE` (`agent/system_prompt.py:257-258`)

Config override: `agent.tool_use_enforcement` in `config.yaml` accepts `true`, `false`, `"auto"` (default), or a custom list of model-name substrings.

**Developer role for newer OpenAI models** (`agent/prompt_builder.py:612`):  
`DEVELOPER_ROLE_MODELS = ("gpt-5", "codex")` — when the model matches, the API boundary swaps the system message role from `"system"` to `"developer"`. Internal representation always stays `"system"`.

**Alibaba model name workaround** (`agent/system_prompt.py:297-304`):  
Alibaba's API always returns `"glm-4.7"` regardless of the requested model. Hermes injects an explicit identity line into the stable tier.

---

## 3. System Prompt Content — Stable Tier

The stable tier is assembled in order in `build_system_prompt_parts()` (`agent/system_prompt.py:147-402`):

1. **Identity** — `SOUL.md` from `~/.hermes/` (user-editable persona) or `DEFAULT_AGENT_IDENTITY` as fallback. Scanned for prompt injection via `threat_patterns.py` before injection.
2. **Hermes help pointer** — `HERMES_AGENT_HELP_GUIDANCE`: directs the agent to the docs URL and the `hermes-agent` skill for self-help (`agent/prompt_builder.py:133-143`).
3. **Task completion guidance** — `TASK_COMPLETION_GUIDANCE`: universal (all models), gated by config and only when tools are loaded. Anti-fabrication and anti-stub rules.
4. **Parallel tool-call guidance** — `PARALLEL_TOOL_CALL_GUIDANCE`: universal, tells the model to batch independent calls into one turn.
5. **Tool-aware behavioral guidance** (conditional on which tools are loaded):
   - `memory` tool → `MEMORY_GUIDANCE`
   - `session_search` tool → `SESSION_SEARCH_GUIDANCE`
   - `skill_manage` tool → `SKILLS_GUIDANCE`
   - `kanban_show` tool → `KANBAN_GUIDANCE` (Kanban worker lifecycle)
6. **Mid-turn steering note** — `STEER_CHANNEL_NOTE`: describes the `[OUT-OF-BAND USER MESSAGE]` marker for `/steer` mid-turn redirects.
7. **Computer-use guidance** — platform-aware (macOS/Windows/Linux) block when `computer_use` is loaded.
8. **Nous subscription block** — dynamic capability status (web, image gen, TTS, browser, Modal).
9. **Tool-use enforcement + model-family operational guidance** — see §2.
10. **Skills index** — `<available_skills>` XML block (see §4).
11. **Alibaba model name fix** — if `agent.provider == "alibaba"`.
12. **Environment hints** — OS, user home, cwd for local; backend probe result (via `uname`/`whoami`/`pwd`) for Docker/Modal/SSH; WSL path note.
13. **Coding-context blocks** — if in a git workspace on an interactive surface: operating brief + live git/workspace snapshot.
14. **Python toolchain probe** — single line only when environment is non-default.
15. **Active profile hint** — which Hermes profile is active.
16. **Platform hint** — from `PLATFORM_HINTS` dict or plugin registry, modified by `config.yaml` `platform_hints.<platform>` overrides.

---

## 4. Skills Block

**Index building** (`agent/prompt_builder.py:1334-1600`, `build_skills_system_prompt()`):

Skills live at `~/.hermes/skills/<category>/<skill-name>/SKILL.md`. External skill dirs configurable in `config.yaml`. The index is organized by category.

**Two-layer cache**:
1. In-process LRU dict (keyed by skills_dir, available tools, toolsets, platform, disabled list, compact categories — max 8 entries).
2. Disk snapshot `.skills_prompt_snapshot.json` validated by mtime/size manifest — survives process restarts without a full filesystem scan.

**Conditional activation** (`agent/prompt_builder.py:1303-1331`): Skill frontmatter declares `fallback_for_toolsets`, `requires_toolsets`, `platforms`, `conditions.environment`. A skill is hidden if its activation conditions aren't met.

**Coding-context demotion** (`agent/prompt_builder.py:1517-1536`): Under `focus` mode, non-coding skill categories are demoted to names-only lines. Never hidden entirely.

**System prompt format** (`agent/prompt_builder.py:1562-1591`):

```
## Skills (mandatory)
Before replying, scan the skills below. If a skill matches or is even partially 
relevant to your task, you MUST load it with skill_view(name) and follow its 
instructions. Err on the side of loading...

<available_skills>
  category1:
    - skill-name: description
</available_skills>
```

**Runtime skill loading**: Full SKILL.md is loaded when model calls `skill_view(name)`. Inline shell snippets `` !`cmd` `` are executed and substituted at load time. Template variables `${HERMES_SKILL_DIR}` and `${HERMES_SESSION_ID}` are substituted (`agent/skill_preprocessing.py:37-80`).

**Skill bundles** (`agent/skill_bundles.py`): YAML files at `~/.hermes/skill-bundles/*.yaml` define named sets of skills. `/<bundle-name>` loads all bundled skills into a single invocation message. Bundles take precedence over same-named skills.

---

## 5. Memory Injection

### 5a. Built-in memory store (volatile tier)

Flat `~/.hermes/memories/*.md` files (kind `memory` or `user`).

**System prompt injection** (`agent/system_prompt.py:426-435`):
- `memory` kind → always-injected block in volatile tier when `agent._memory_enabled`
- `user` kind (USER.md) → always injected when `agent._user_profile_enabled`

Both are always-on, not search-driven. The model sees all memory items every turn.

### 5b. External memory providers (plugin system)

`MemoryProvider` ABC (`agent/memory_provider.py`) — pluggable backends (Honcho, Hindsight, Mem0, etc.). Only ONE external provider allowed at a time.

Lifecycle:
- `system_prompt_block()` → static text for system prompt (volatile tier)
- `prefetch(query)` → called before each API call; returns recalled context injected into the **current user message** (not system prompt)
- `sync_turn(user, asst)` → called after each turn on background thread (never blocks the turn)
- `queue_prefetch(query)` → queues background recall for NEXT turn

**Prefetch injection** (`agent/conversation_loop.py:747-758`): External recall context is wrapped in `<memory-context>...</memory-context>` fence and appended to the user message at API-call time. This keeps the system prompt byte-stable (cache-safe) while providing per-turn dynamic recall.

**Streaming scrubber** (`agent/memory_manager.py:132-295`, `StreamingContextScrubber`): Stateful state machine that strips `<memory-context>` spans from the model's streaming output to prevent the model from echoing back injected context.

### 5c. Session search

Ripgrep-based lexical search over JSONL session transcripts. `SESSION_SEARCH_GUIDANCE` in the stable tier tells the model when to use it.

---

## 6. Tool Injection

Tools are **not embedded in the system prompt** — passed as structured definitions in the API `tools:` field.

**Guidance is tool-conditional** (`agent/system_prompt.py:173-206`): Which guidance blocks appear in the stable tier depends on `agent.valid_tool_names`. Guidance is conditional: `MEMORY_GUIDANCE` only when `memory` is in the tool set, etc.

**Core tools are reserved** (`agent/memory_manager.py:368-379`): Memory provider plugins cannot shadow built-in core tool names. Built-ins always win.

**Message sanitization before each API call** (`agent/conversation_loop.py:707-780`):
- Repair corrupted tool-call argument JSON
- Repair message-alternation violations
- Add `cache_control` breakpoints when prompt caching is on
- Strip unknown keys for strict APIs (Mistral, Fireworks)
- Strip lone surrogate characters (Ollama-returned surrogates crash `json.dumps`)
- Canonicalize tool-call argument JSON (`sort_keys=True`, minimal separators) for bit-perfect prefix matching

---

## 7. Agent Loop Structure

`run_conversation()` (`agent/conversation_loop.py:495-end`). Setup delegated to `build_turn_context()` (`agent/turn_context.py`).

**Per-turn setup (build_turn_context)**:
1. stdio guarding, retry-counter resets, message sanitization
2. System-prompt restore-or-build: restore from session DB (for stateless gateway) or build from scratch on first turn
3. Crash-resilience persistence, preflight compression check
4. `pre_llm_call` plugin hook, external memory prefetch

**Main while loop**:

```
while api_call_count < max_iterations and budget.remaining > 0:
  1. Check interrupt → break
  2. Consume iteration budget
  3. Drain pending /steer into last tool message
  4. Repair tool-call arguments and message-alternation
  5. Build api_messages: copy messages, inject ephemeral context into current user msg
  6. Prepend system message (cached prompt + ephemeral additions)
  7. Apply Anthropic cache_control breakpoints
  8. Sanitize, normalize, make API call
  9. Handle response: tool calls → execute → append → continue
  10. Handle finish_reason (stop / length / content_filter / etc.)
  11. On stop with no tool calls → collect final_response → break
  12. On context overflow → compress → rebuild prompt → continue
```

**Ephemeral system prompt** (`agent/conversation_loop.py:798-802`): `agent.ephemeral_system_prompt` is appended to the cached prompt at API-call time only — never stored, never invalidates the cache. Provides a runtime-only injection slot.

**Post-turn**: Persist session to DB, background memory sync, queue next-turn prefetch, background skill review nudge, memory curation review, session-end hooks.

---

## 8. Context Management / Compaction

`ContextCompressor` (`agent/context_compressor.py`): Self-contained class with its own auxiliary LLM client. Uses structured summary templates:
- `## Historical Task Snapshot`
- `## Historical In-Progress State`
- `## Historical Pending User Asks`
- `## Historical Remaining Work`

**Summary prefix** (`agent/context_compressor.py:43-68`): Explicit "REFERENCE ONLY — NOT active instructions" directive. Guards memory authority: "Your persistent memory (MEMORY.md, USER.md) in the system prompt is ALWAYS authoritative and active."

**Two error classes distinguished** (`agent/model_metadata.py:1030-1114`):
1. "Prompt too long" → compress history + optionally reduce `context_length`
2. "`max_tokens` too large" → reduce `max_tokens` only; do NOT touch `context_length`

After compression: `invalidate_system_prompt()` clears the cache and reloads memory from disk, so the rebuilt volatile tier captures any session writes.

**Summary end marker** (`agent/context_compressor.py:89-95`): `"--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"` appended to every summary to prevent weak models from reading the summarized tasks as active.

---

## 9. Prompt Caching

**Strategy: `system_and_3`** (`agent/prompt_caching.py:49-79`):  
4 `cache_control` breakpoints per request:
- System prompt message
- Last 3 non-system messages

All breakpoints use the same TTL (`5m` or `1h`, configurable via `agent._cache_ttl`).

**Auto-detection**: `agent._use_prompt_caching` set automatically when model is Claude on Anthropic, OpenRouter, or compatible gateway.

**Cache-friendliness by design**:
- System prompt built ONCE per session, byte-stable across turns
- Date-only timestamp in volatile tier (not minute-precision)
- External prefetch injected into user message (not system prompt)
- Plugin context injected into user message (not system prompt)
- Tool-call argument JSON canonicalized before each API call (`sort_keys=True`, minimal separators) — `agent/conversation_loop.py:860-874`

**Gateway path** (`agent/conversation_loop.py:254-375`): Fresh `AIAgent` per HTTP turn. System prompt persisted to session DB on first build and restored verbatim on subsequent requests. Stale prompts (model/provider mismatch) trigger a rebuild with a WARNING log.

---

## 10. Per-Gateway / Per-Platform Variation

`PLATFORM_HINTS` (`agent/prompt_builder.py:614-819`): Large dict mapping 20 platform keys to multi-paragraph format/behavior instructions, injected into the **stable tier**.

Built-in platforms include: `whatsapp`, `whatsapp_cloud`, `telegram`, `discord`, `slack`, `signal`, `sms`, `email`, `cron`, `cli`, `webui`, `api_server`, `mattermost`, `matrix`, `feishu`, `weixin`, `wecom`, `qqbot`, `yuanbao`, `bluebubbles`.

**Config-driven per-platform overrides** (`agent/system_prompt.py:64-110`, `_resolve_platform_hint()`): `config.yaml` `platform_hints.<platform>` supports `replace` (full substitution) or `append` (add to default). A bare string is treated as `append`.

**Plugin-registered platforms** (`gateway/platform_registry.py`): Plugins register `PlatformEntry` with a `platform_hint` string. Hermes checks the plugin registry when a platform key isn't in the hardcoded dict.

**Trade-off**: Platform hints in the stable tier mean sessions on different platforms produce different system prompt bytes — they share no prefix cache with each other. This is a deliberate correctness-over-caching tradeoff.

---

## 11. Context File Loading Priority

`build_context_files_prompt()` (`agent/prompt_builder.py:1841-1888`) implements first-match-wins for project context files:

1. `.hermes.md` or `HERMES.md` — walks up to git root
2. `AGENTS.md` or `agents.md` — cwd only
3. `CLAUDE.md` or `claude.md` — cwd only
4. `.cursorrules` + `.cursor/rules/*.mdc` — cwd only

Only ONE project context type is loaded. SOUL.md (from `HERMES_HOME`) is independent and always included unless `skip_soul=True`.

Each loaded file is scanned for prompt injection, YAML frontmatter stripped, and truncated to a cap derived from the model's context window (6% of context window, min 20K chars, max 500K chars — `agent/prompt_builder.py:1094-1116`). Truncation uses head/tail (70%/20%) with a marker in the middle and surfaces a warning to the user.

---

## 12. Key File Index

| File | Purpose | Key functions / ranges |
|---|---|---|
| `agent/system_prompt.py` | Top-level prompt assembly | `build_system_prompt_parts()` (113-467), `build_system_prompt()` (470-493), `invalidate_system_prompt()` (496-504) |
| `agent/prompt_builder.py` | All prompt constants and build helpers | `DEFAULT_AGENT_IDENTITY` (123), `PLATFORM_HINTS` (614-819), `build_skills_system_prompt()` (1334-1600), `load_soul_md()` (1713-1741), `build_context_files_prompt()` (1841-1888), `build_environment_hints()` (964-1086) |
| `agent/model_metadata.py` | Context length resolution, provider detection | `DEFAULT_CONTEXT_LENGTHS` (191-320), `get_model_context_length()` (1613+), `parse_context_limit_from_error()` (982-1007) |
| `agent/memory_manager.py` | External memory provider orchestration | `MemoryManager` (314-1032), `build_memory_context_block()` (297-311), `StreamingContextScrubber` (132-295) |
| `agent/memory_provider.py` | Abstract base for memory providers | `MemoryProvider` ABC (43-316) |
| `agent/prompt_caching.py` | Anthropic cache_control injection | `apply_anthropic_cache_control()` (49-79) |
| `agent/conversation_loop.py` | Main agent loop | `run_conversation()` (495-end), `_restore_or_build_system_prompt()` (254-375) |
| `agent/context_compressor.py` | Conversation compression | `SUMMARY_PREFIX` (43-68), structured heading constants (37-40) |
| `agent/skill_bundles.py` | Skill bundle loading | `get_skill_bundles()`, `build_bundle_invocation_message()` |
| `agent/skill_preprocessing.py` | Skill content preprocessing | `substitute_template_vars()` (37-60), `run_inline_shell()` (63-80) |
| `agent/coding_context.py` | Coding-posture detection | `coding_system_blocks()`, `INTERACTIVE_CODING_PLATFORMS` (70) |
| `gateway/platform_registry.py` | Plugin platform registration | `PlatformEntry` (39-161) |
| `tools/threat_patterns.py` | Injection scanning | `scan_for_threats()` — used by context file scanner |

---

## 13. Architectural Comparison: Hermes vs co-cli

| Dimension | Hermes Agent | co-cli |
|---|---|---|
| **Base prompt** | Single `DEFAULT_AGENT_IDENTITY`; user replaces via `~/.hermes/SOUL.md` | Single model-agnostic BASE; per-profile overlay (additive) |
| **Per-model variation** | Additive guidance blocks gated by model-name substring matching; no separate files | Additive overlay on top of BASE — same pattern |
| **Composition formula** | `stable + context + volatile` (three ordered tiers, explicit cache boundary) | `BASE + overlay` — implicit tier separation |
| **Prompt rebuild frequency** | Once per session; only after context compression | Static instructions assembled once per session; dynamic instruction suffix (skills, deferred tools, time, safety, wrap-up) recomputed per request |
| **Date in prompt** | Day-only (`%A, %B %d, %Y`) for byte-stability | Day-only (`Current date: %A, %B %d, %Y`) via `current_time_prompt` dynamic instruction — same byte-stability rationale |
| **Skills injection** | Full `<available_skills>` index in stable tier; mandatory-load framing; LRU + disk snapshot cache | `<available_skills>` manifest rendered per-turn as the `skill_manifest_prompt` dynamic instruction (outside the cached prefix, re-reads the live skill index); on-demand load |
| **Built-in memory injection** | Volatile tier: flat `.md` files always-injected; USER.md always-injected | USER.md always-injected; FTS5 BM25 recall for memory items |
| **Memory recall mechanism** | External provider `prefetch()` → user message injection | `memory_search` tool (model calls it) |
| **External memory injection point** | User message (not system prompt) — cache-safe | N/A today |
| **Streaming memory scrubber** | `StreamingContextScrubber` strips `<memory-context>` from output | N/A |
| **Tool passing** | Structured function-call definitions (not in prompt text) | Same |
| **Prompt caching** | Explicit `system_and_3` `cache_control`; day-only timestamp; tool-call JSON canonicalized | No manual `cache_control`; relies on pydantic-ai's static/dynamic `InstructionPart` split — the Anthropic provider caches the last static block, Ollama prefix-cache reuses the static prefix; day-only timestamp keeps that prefix byte-stable across same-day turns |
| **Context compaction** | `ContextCompressor` with aux LLM, structured headings, `REFERENCE ONLY` prefix | Sliding-window history processor + `/compact`; LLM summarizer with structured `##` headings; `[CONTEXT COMPACTION — REFERENCE ONLY]` marker + static-marker fallback |
| **Platform variation** | `PLATFORM_HINTS` dict (30+ platforms); config-driven overrides; plugin-registerable | No platform-level prompt variation |
| **Ephemeral system additions** | `ephemeral_system_prompt` appended at API-call time only (not persisted) | Dynamic `@agent.instructions` are ephemeral — recomputed per request, never persisted to history (`safety_prompt`, `wrap_up_prompt`, `current_time_prompt`); condition-gated, not an arbitrary runtime injection slot |
| **Mid-turn steering** | `/steer` appended to last tool result with `[OUT-OF-BAND USER MESSAGE]` fence | No mid-turn steering today |
| **Skill bundles** | YAML-defined multi-skill aliases; bundles win over same-named skills | No bundles today |
| **Inline shell in skills** | `` !`cmd` `` executed at load time | No inline shell today |
| **Prompt injection defense** | `threat_patterns.py` scans context files before injection; blocks on match | No explicit injection scanning today |
| **Gateway system prompt persistence** | System prompt persisted to session DB; restored verbatim for stateless gateway turns | N/A (single-process) |
| **Coding posture** | Auto-detected (git workspace + interactive surface); brief + git snapshot baked into stable tier | No auto-detected coding posture today |
| **Skill conditional activation** | `fallback_for`, `requires`, `platforms`, `environment` conditions per skill | Flat list; no conditional activation today |

---

## 14. Key Takeaways for co-cli Design

**Directly applicable patterns:**

1. **Three-tier composition with explicit cache boundary. (Now implemented.)** The `stable/context/volatile` split is more explicit than co-cli's `BASE+overlay`. The critical rule: volatile content (memory, timestamps) must NOT be inside the cached prefix. co-cli now realizes this via pydantic-ai's `InstructionPart` static/dynamic flag — the `BASE+overlay` literal is the cached static block; volatile content (timestamp, skill manifest, deferred-tool list, safety/wrap-up) lives in per-turn `@agent.instructions` callbacks (`dynamic=True`), kept outside the cached prefix. See `prompt-assembly.md` §2.3.

2. **Day-only timestamp. (Now implemented.)** Minute-precision timestamps invalidate prefix-cache KV on every rebuild. co-cli's `current_time_prompt` emits `Current date: %A, %B %d, %Y` at day-only granularity so the system block stays byte-stable across same-day turns; the Ollama prefix cache then extends through it into history.

3. **External recall context injected into user message, not system prompt.** Hermes' memory prefetch arrives as `<memory-context>...</memory-context>` appended to the current user message. This keeps the system prompt stable (cache-safe) while providing per-turn dynamic grounding. If co-cli ever adds an external memory provider, this is the injection pattern to use.

4. **`StreamingContextScrubber` is needed if memory is injected into user messages.** When recalled context is injected into the user message, naive models echo it back in their visible response. The stateful streaming scrubber pattern is the clean fix.

5. **Explicit `cache_control` + canonicalized content.** Hermes sorts tool-call argument JSON keys and strips whitespace before each API call for bit-identical prefixes. If co-cli adds explicit Anthropic caching, adopt this.

6. **Skills `fallback_for` / `requires` conditions keep the index clean.** co-cli's flat `<available_skills>` list could benefit from per-skill conditional activation without manual maintenance.

7. **Coding-posture auto-detection.** Detecting git workspace + interactive surface and injecting a brief + git snapshot makes the model coding-aware without user action. The snapshot is baked into the stable tier (built once at session start, never re-probed per turn).

8. **Compaction `REFERENCE ONLY` prefix + memory authority assertion.** When compaction removes history, the summary must explicitly disclaim itself as non-active and assert that persistent memory in the system prompt is always authoritative. Without this, models treat the summarized tasks as current instructions.

**Patterns co-cli already matched or surpassed:**

9. **BASE+overlay is equivalent** to Hermes' single-constant + additive blocks. Both are DRY; neither uses separate per-model files (unlike OpenCode).

10. **USER.md as always-injected profile** is exactly co-cli's approach. Hermes' `user` memory kind maps directly.

11. **`session_search` via ripgrep** is the same pattern in both systems.

12. **Memory recall as model-driven tool call** (co-cli's `memory_search`) vs Hermes' always-on injection + external provider prefetch — co-cli's approach is lighter on token cost when the model doesn't need memory.

**Patterns to avoid:**

13. **The mandatory-load framing in skills is aggressive.** Hermes instructs the model to load a skill if it "matches or is even partially relevant." For shorter tasks this is noisy overhead. co-cli's lighter framing may be better calibrated.

14. **Platform hints in the stable tier cause cache fragmentation.** Different platforms produce different system prompt bytes and share no prefix cache with each other. This is a deliberate Hermes tradeoff; co-cli should be aware before adding platform variation to the stable tier.
