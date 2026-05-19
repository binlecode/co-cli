# RESEARCH — Self-Learning: co vs. hermes (code-grounded)

Compares co-cli's "built-in learning loop" capability against `hermes-agent`'s claim of being "the self-improving AI agent." Every assertion below is grounded in source code in the two repos as scanned on 2026-05-18; every file path is real, every line number was opened and read.

Sibling docs (don't duplicate):
- `RESEARCH-tools-gaps-co-vs-hermes.md` — broader tool-surface delta
- `RESEARCH-tui-compare-hermes-and-co.md` — REPL / surface comparison
- `RESEARCH-context-management-comparison.md` — compaction-side

## 1. What "self-improving" means in hermes's pitch

Five claims in hermes's README:

1. "Creates skills from experience"
2. "Improves them during use"
3. "Nudges itself to persist knowledge"
4. "Searches its own past conversations"
5. "Builds a deepening model of who you are across sessions"

For each, the question this doc answers is: *what code actually implements that, and what does co have on the same axis*.

## 2. Hermes mechanisms — verified inventory

| # | Mechanism | File:line | Trigger | State written |
|---|---|---|---|---|
| H1 | Memory-nudge background reviewer | `run_agent.py:1645–1653`, `:10193–10199`, `:13472–13480` | turn counter `_turns_since_memory ≥ _memory_nudge_interval` (default 10), checked at top of `run_conversation`; spawned after final reply | `~/.hermes/memories/MEMORY.md`, `USER.md` |
| H2 | Skill-nudge background reviewer | `run_agent.py:1752–1755`, `:13456–13461` | tool-iteration counter `_iters_since_skill ≥ _skill_nudge_interval` (default 10), checked at end of turn | `~/.hermes/skills/<name>/SKILL.md` + support files |
| H3 | `_MEMORY_REVIEW_PROMPT` | `run_agent.py:3240–3249` (10 lines) | feeds H1 | — |
| H4 | `_SKILL_REVIEW_PROMPT` | `run_agent.py:3251–3325` (75 lines) | feeds H2 | — |
| H5 | `_COMBINED_REVIEW_PROMPT` | `run_agent.py:3327+` | used by H1+H2 when both trip in the same turn (`run_agent.py:3462–3467`) | — |
| H6 | `_spawn_background_review` | `run_agent.py:3446–3559` | forks a full `AIAgent` in a daemon thread (`threading`), auto-denies any dangerous-command guard, redirects stdout/stderr to `/dev/null`, runs `review_agent.run_conversation(prompt, conversation_history=messages_snapshot)`, then prints a one-line `💾 …` summary | spawns child, child writes |
| H7 | `skill_manage` tool actions | `tools/skill_manager_tool.py:663–696,749` | `enum: ["create", "patch", "edit", "delete", "write_file", "remove_file"]` | atomic SKILL.md / support-file writes |
| H8 | Session FTS5 search + per-result LLM summarization | `tools/session_search_tool.py:111–193,196–258,319–470` | called by model via `session_search(query, …)` | none persisted; results synthesized on demand |
| H9 | Honcho dialectic provider (5 tools) | `plugins/memory/honcho/__init__.py:36,63,91,130,154,184` (`ALL_TOOL_SCHEMAS`) | per-turn prefetch (`HonchoMemoryProvider`, line 191+) and on-demand tool calls | Honcho backend (peer cards, conclusions) |
| H10 | `USER.md` block in system prompt | `run_agent.py:1641,1652,4819–4823` | `_user_profile_enabled` flag; injected during prompt build | read-only at injection time; written by H1 via `memory` tool |
| H11 | Background skill curator | `agent/curator.py:39–42` | inactivity-triggered, default `DEFAULT_INTERVAL_HOURS = 24*7` (7 days) | `~/.hermes/skills/.curator_state` |
| H12 | Auxiliary LLM client | `agent/auxiliary_client.py` exporting `async_call_llm`, `extract_content_or_reasoning` | used by H8 and other side-tasks | — |

Hermes config defaults (loaded in `__init__`, runtime-gated):

```python
# run_agent.py:1645–1653
self._memory_enabled       = mem_config.get("memory_enabled", False)        # OFF default
self._user_profile_enabled = mem_config.get("user_profile_enabled", False)  # OFF default
self._memory_nudge_interval = int(mem_config.get("nudge_interval", 10))

# run_agent.py:1752–1755
self._skill_nudge_interval = int(skills_config.get("creation_nudge_interval", 10))
```

Two layers of gating apply to H1 (`run_agent.py:10193–10199`):

```python
_should_review_memory = False
if (self._memory_nudge_interval > 0
        and "memory" in self.valid_tool_names
        and self._memory_store):
    self._turns_since_memory += 1
    if self._turns_since_memory >= self._memory_nudge_interval:
        _should_review_memory = True
        self._turns_since_memory = 0
```

So H1 fires only when: nudge interval positive AND the `memory` tool is enabled AND a memory store is constructed. `memory_enabled=False` means no memory store is constructed (`run_agent.py:1654`), so the nudge never fires in default config.

## 3. Co mechanisms — verified inventory

| # | Mechanism | File:line | Trigger | State written |
|---|---|---|---|---|
| C1 | Session reviewer (combined skill + memory) | `co_cli/main.py:269–299` (`_post_turn_hook`), `co_cli/skills/session_review.py:138–168` (`run_session_review`) | counter `iterations_since_review ≥ review_nudge_interval` (default 5); background `asyncio.Task` (not OS thread) | session-review report at `~/.co-cli/session-reviews/<ts>-<id>/run.{json,md}` |
| C2 | `SESSION_REVIEW_INSTRUCTIONS` (combined prompt) | `co_cli/skills/session_review_prompts.py:5–35` (30 lines, one prompt covering both surfaces) | feeds C1 | — |
| C3 | `SESSION_REVIEW_SPEC` tool surface | `co_cli/skills/session_review.py:56–70` | `tool_names=("memory_view", "memory_search", "memory_manage", "skill_view", "skill_manage")` | — |
| C4 | Reviewer fork | `co_cli/deps.py:310` (`fork_deps_for_reviewer`), `co_cli/skills/session_review.py:148–166` | forked deps grant skill + memory write; `serialize_messages` strips tool results; `run_standalone` runs the spec with budget = `REVIEW_MAX_ITERATIONS` (8) | atomic write of `run.json` + `run.md` via `atomic_write_text` |
| C5 | `skill_manage` actions | `co_cli/tools/skills/manage.py` (similar verb set: create / patch / edit / write_file / remove_file — per prompt references) | called by reviewer or main agent | atomic SKILL.md / support-file writes |
| C6 | Session FTS5 search (BM25 chunk-cited, no LLM) | `co_cli/tools/session/recall.py:25–105,121–170` | model calls `session_search(query, limit)` | none |
| C7 | Skill curator with lifecycle states | `co_cli/skills/curator.py` (CURATOR_SPEC), `co_cli/main.py:243–266` (`_maybe_run_curator`), `co_cli/config/skills.py:16–17,30–31` | runs immediately after C1 (`co_cli/main.py:224`), time-gated by `curator_interval_hours` (default 168 = 7 days); `_curator_gate_passes` allows on first run | `~/.co-cli/skills/.curator_state.json` |
| C8 | Dream cycle (mining / merge / decay) | `co_cli/memory/dream.py:1–60,_DREAM_*` constants | session-end if `consolidation_enabled` + `consolidation_trigger=session_end`, or `/memory dream` manual | `memory/*.md` items, `memory/_archive/`, `memory/_dream_state.json` |
| C9 | Doctrine (auto-injected, never queried) | covered in `docs/specs/personality.md` | static at prompt build | — (read-only) |

Co config defaults:

```python
# co_cli/config/skills.py:27–31
review_enabled: bool         = Field(default=False)          # OFF default
review_nudge_interval: int   = Field(default=5, ge=1)
curator_enabled: bool        = Field(default=False)          # OFF default
curator_interval_hours: int  = Field(default=168, ge=1)      # 7 days

# co_cli/config/skills.py:13,17
REVIEW_MAX_ITERATIONS        = 8
CURATOR_STALE_AFTER_DAYS     = 30
CURATOR_ARCHIVE_AFTER_DAYS   = 90

# co_cli/config/memory.py:61–64
consolidation_enabled: bool                       = Field(default=False)  # OFF default
consolidation_trigger: Literal["session_end", "manual"] = Field(default="session_end")
consolidation_lookback_sessions: int              = Field(default=5,  ge=1)
consolidation_similarity_threshold: float         = Field(default=0.75, ge=0.0, le=1.0)
```

Gating for C1 (`co_cli/main.py:283–299`):

```python
deps.session.iterations_since_review += turn_iteration_count
if deps.session.iterations_since_review < settings.review_nudge_interval:
    return
task = deps.session.background_review_task
if task is not None and not task.done():
    return                                  # single in-flight
deps.session.iterations_since_review = 0
deps.session.background_review_task = asyncio.create_task(
    _maybe_run_session_review(deps, list(message_history))
)
```

Same posture as hermes: opt-in via config, single-in-flight guard, runs after the user reply lands.

## 4. Per-surface deep dive

### 4.1 Memory persistence nudge (claim #3)

**Hermes implementation (H1 + H3 + H6).** Turn counter incremented on each user turn (`run_agent.py:10196`). When it trips, `_should_review_memory = True` is recorded. After the agent loop completes and the user reply is delivered (`run_agent.py:13472`), `_spawn_background_review` forks a fresh `AIAgent` in a daemon thread (`run_agent.py:3500–3512`) limited to `max_iterations=8`, `quiet_mode=True`, with `enabled_toolsets=["memory", "skills"]`. The forked agent inherits the parent's model/provider/credentials, has its own nudge intervals zeroed out (`run_agent.py:3518–3519` — so the reviewer never recursively schedules another reviewer), and runs the appropriate prompt with the conversation history attached:

```python
review_agent.run_conversation(
    user_message=prompt,
    conversation_history=messages_snapshot,
)
```

Prompt body (`run_agent.py:3240–3249`):

> "Review the conversation above and consider saving to memory if appropriate. Focus on: (1) Has the user revealed things about themselves — their persona, desires, preferences, or personal details worth remembering? (2) Has the user expressed expectations about how you should behave, their work style, or ways they want you to operate? If something stands out, save it using the memory tool. If nothing is worth saving, just say 'Nothing to save.' and stop."

The fork's writes go to the shared `_memory_store`, which persists to `MEMORY.md` and `USER.md` on disk; the next session reads fresh state at startup.

**Co implementation (C1 + C2 + C3 + C4).** Same shape, one structural difference: there is one *combined* skill+memory reviewer rather than two separate triggers and three prompts.

- Trigger: tool-iteration counter `iterations_since_review` (counts `turn_iteration_count` accumulated per turn), threshold default 5 (`co_cli/config/skills.py:28`).
- Fork: `fork_deps_for_reviewer(deps)` grants write access to both stores (`co_cli/deps.py:310`).
- Tool surface explicitly includes the memory CRUD tools (`co_cli/skills/session_review.py:60–64`):

```python
tool_names=(
    "memory_view",
    "memory_search",
    "memory_manage",
    "skill_view",
    "skill_manage",
),
```

- Prompt body (`co_cli/skills/session_review_prompts.py:5–35`, excerpt): *"Scope: Skills: procedural knowledge (how to do tasks)… Knowledge: user preferences, corrections, rules, decisions. Create or update memory items for anything the user explicitly corrected or that reflects a reusable insight. Be ACTIVE — most sessions produce at least one update."*

**Difference (functionally minor):**
- Hermes has separate counters for memory (turn-based) and skill (tool-iteration-based) and three prompt variants. Co has a single counter and a single combined prompt.
- Hermes uses an OS daemon thread; co uses `asyncio.create_task` (no thread).
- The covered behavior — "every N units, fork a subagent, look at the conversation, write to memory and/or skills, output a one-line confirmation" — is the same on both.

**Verdict on claim #3:** PARITY in shape, both off-by-default, slight prompt-engineering richness advantage to hermes (the dedicated `_MEMORY_REVIEW_PROMPT` is tighter than co's combined instructions for the pure-memory case).

### 4.2 Autonomous skill creation + self-improvement (claims #1 + #2)

**Hermes (H2 + H4 + H7).** The skill reviewer (H2) is the same `_spawn_background_review` machinery, called with `review_skills=True`. Its prompt (`run_agent.py:3251–3325`) is the most opinionated artifact in the whole loop — 75 lines of preference-ordered guidance:

> "Be ACTIVE — most sessions produce at least one skill update… Signals to look for (any one of these warrants action): • User corrected your style, tone, format, legibility, or verbosity. Frustration signals like 'stop doing X', 'this is too verbose', 'don't format like this'… are FIRST-CLASS skill signals, not just memory signals. Update the relevant skill(s) to embed the preference so the next session starts already knowing. • A skill that got loaded or consulted this session turned out to be wrong, missing a step, or outdated. **Patch it NOW**."

Preference order is explicit (`run_agent.py:3276–3311`): patch a currently-loaded skill > patch an existing umbrella > add a `references/`/`templates/`/`scripts/` support file > create a new class-level skill. The verb set is enforced by the tool itself (`tools/skill_manager_tool.py:749`):

```python
"enum": ["create", "patch", "edit", "delete", "write_file", "remove_file"]
```

**Co (C1 + C2 + C5).** Same six-verb shape on `skill_manage`. The reviewer prompt is shorter (`co_cli/skills/session_review_prompts.py:5–35`):

> "Be ACTIVE — most sessions produce at least one update. A pass that does nothing is a missed learning opportunity, not a neutral outcome… Preference order for skills: 1. UPDATE a skill that was loaded in this session (if it had drift or gaps). 2. UPDATE an existing umbrella skill that covers this area. 3. CREATE a new class-level skill only if nothing applicable exists."

The structural shape — patch-loaded-first preference, class-level naming requirement, no-deletes constraint, support-files via `write_file` — is identical. Differences:

- Co's prompt is ~30 lines vs hermes's ~75. The hermes prompt explicitly calls out "frustration signals" as first-class skill signals and dictates "patch it NOW" for stale loaded skills. Co's prompt has the same posture but fewer concrete examples and no explicit "frustration → skill not just memory" framing.
- Co does not have a "support files = 3 kinds" framing (references / templates / scripts) in its reviewer prompt — though `skill_manage(action='write_file')` supports it.

**Verdict on claims #1 + #2:** PARITY in mechanism, hermes has richer prompt engineering for the corrective-signal case.

### 4.3 Skill curator / lifecycle (loop-closing maintenance)

**Hermes (H11):** `agent/curator.py`, fork-based, inactivity-triggered, default 7-day interval; transitions stale → archive via `skill_manage`, never auto-deletes (only archives), pinned skills bypass.

**Co (C7):** `co_cli/skills/curator.py`, runs as a second pass immediately after the session reviewer (`co_cli/main.py:224`), time-gated by `_curator_gate_passes` using `curator_interval_hours` (default 168 = 7 days). Stale at 30d, archive at 90d (`co_cli/config/skills.py:16–17`). Identical posture: archive not delete, pinning, atomic state at `.curator_state.json`.

**Verdict:** PARITY. Numbers and behavior align.

### 4.4 Cross-session search + LLM summarization (claim #4)

**Hermes (H8 + H12).** Three-stage pipeline:

1. **FTS5 lookup** (`tools/session_search_tool.py:358–365`): `db.search_messages(query, role_filter, exclude_sources, limit=50, offset=0)`. Returns raw rows.
2. **Per-session focus window** (`tools/session_search_tool.py:111–193`, `_truncate_around_matches`): for each unique session, loads full transcript, chooses a ~100k-char window centered on the match. Strategy is priority-ordered: full phrase match → 200-char co-occurrence of all terms → individual term positions → fallback to head. Window picked to maximize covered match positions (bias 25% before, 75% after).
3. **Per-session LLM summarization** (`tools/session_search_tool.py:196–258`, `_summarize_session`): for each prepared session, call the auxiliary LLM via `async_call_llm(task="session_search", temperature=0.1, max_tokens=MAX_SUMMARY_TOKENS)`. System prompt asks for a structured recap (what user wanted, actions taken, decisions, specific commands/paths/URLs, anything unresolved). Concurrency: `asyncio.Semaphore(min(MAX_CONCURRENCY, len(tasks)))` (`tools/session_search_tool.py:447–460`). Retries 3 times with backoff. Cheap auxiliary model (Gemini Flash fallback chain).

Result returned to the agent: per-session synthesized recap text, not raw chunks.

**Co (C6).** Strictly BM25 chunk recall:

```python
# co_cli/tools/session/recall.py:51–105 (excerpt)
def _search_sessions(ctx, query, span):
    store = ctx.deps.session_store
    raw = store.search(query, limit=_SESSIONS_CHANNEL_CAP * 5)
    …
    return [{
        "session_id": …,  "when": …, "source": …,
        "chunk_text": r.snippet or "",
        "start_line": r.start_line,
        "end_line": r.end_line,
        "score": r.score,
    } for …]
```

There is no auxiliary-model call anywhere in this path. The hardcoded span attributes at `co_cli/tools/session/recall.py:37–39` (`memory.summarizer.runs=0`, `failures=0`, `timed_out=False`) confirm the absence is structural, not just unwired. The agent receives BM25-ranked chunks with line citations, intended to be paired with `session_view(session_id, start_line, end_line)` for verbatim follow-up reads.

**Verdict on claim #4:** GAP. Co has the index but not the digest stage. The user's main agent has to do its own reading of chunks (or call `session_view`); hermes pre-digests.

### 4.5 USER.md / user-profile artifact (claim #5, prompt side)

**Hermes (H10).** `run_agent.py:4819–4823`:

```python
if self._memory_store:
    if self._memory_enabled:
        mem_block = self._memory_store.format_for_system_prompt("memory")
        if mem_block:
            prompt_parts.append(mem_block)
    # USER.md is always included when enabled.
    if self._user_profile_enabled:
        user_block = self._memory_store.format_for_system_prompt("user")
        if user_block:
            prompt_parts.append(user_block)
```

`USER.md` is a separate, dedicated artifact (distinct from `MEMORY.md`), always injected into the system prompt when the flag is on. The same memory-nudge reviewer (H1) can write to it via the `memory` tool.

**Co.** Absent in code. `grep -rn "USER.md\|user_profile\|user_model" co_cli/` returns nothing. The string appears only in `docs/specs/uat_evals.md` as a *planned* Phase-2 eval target (`eval_user_model`). Co stores user preferences as regular memory items with `kind='user'` (`co_cli/memory/item.py`), recalled through `memory_search` like any other kind.

**Functional difference:**
- Hermes always injects USER.md into the system prompt at static-prompt build time. The model sees the maintained user profile every turn for free, without spending tool calls.
- Co requires the model to `memory_search` for user-kind items at runtime. Doctrine (soul / mindsets) is auto-injected, but user-level facts are not.

**Verdict on claim #5 (prompt side):** GAP. Co has no equivalent of an always-injected user-profile block.

### 4.6 Dialectic user-modeling backend (claim #5, backend side)

**Hermes (H9).** `plugins/memory/honcho/__init__.py` exposes **five** tool schemas (`ALL_TOOL_SCHEMAS` at line 184):

1. `honcho_profile` (line 36) — read/write a peer card (curated list of fact strings about a peer; defaults to `peer='user'`)
2. `honcho_search` (line 63) — semantic search over stored peer context, returns excerpts (no LLM synthesis)
3. `honcho_reasoning` (line 91) — natural-language Q&A against Honcho's dialectic layer; `reasoning_level` ∈ {minimal, low, medium, high, max}
4. `honcho_context` (line 130) — full session-context snapshot (summary + peer representation + peer card + recent messages); no LLM synthesis
5. `honcho_conclude` (line 154) — persist or delete a conclusion (factual statement) about a peer; deletion is for PII removal only

`HonchoMemoryProvider` (line 191) runs per-turn prefetch with configurable cadence (`_context_cadence`, `_dialectic_cadence`), wires into the agent's system-prompt build via `MemoryManager.build_system_prompt()`, and the user-profile peer card is dynamically synthesized backend-side from observed conversation rather than maintained as a static file.

**Co.** No equivalent plugin, no dialectic backend, no peer-card abstraction. The closest co primitive is the personality system (doctrine / soul), but that is *author-curated and immutable per session*, not *learned from conversation*. (`docs/specs/personality.md`.)

**Verdict on claim #5 (backend side):** GAP. Co has nothing on this axis.

### 4.7 Dream / consolidation (co-unique surface)

`co_cli/memory/dream.py` (constants at `:46–60`): three-phase batch pass — mining (extract patterns from N most recent transcripts via `dream_miner.md` prompt), merge (cluster similar same-kind items by token-Jaccard ≥ 0.75 default, consolidated body via LLM, originals archived), decay (archive stale per `find_decay_candidates`). Caps: `_MAX_MINE_SAVES_PER_SESSION=5`, `_MAX_MERGES_PER_CYCLE=10`, `_MAX_CLUSTER_SIZE=5`, `_MAX_DECAY_PER_CYCLE=20`. Wall-clock cap: `_DREAM_CYCLE_TIMEOUT_SECS=60`.

Trigger gating: `memory.consolidation_enabled=False` default; if enabled, fires at session end. Cross-cycle state at `memory/_dream_state.json` (`processed_sessions`, `last_dream_at`, `total_*` counters).

**Hermes equivalent.** No direct equivalent. The skill curator (H11) is parallel for skills; for memory, hermes's H1 is *per-turn* incremental writes rather than a separate batch consolidation. Hermes does have a context-compressor (`agent/context_compressor.py`) but that operates on the live conversation buffer, not on disk memory.

**Net effect:** Co has a deeper "off-line memory hygiene" mechanism (mining + merge + decay as one pass), hermes has a shallower but more frequent "per-turn nudge" mechanism. Different shapes of the same compounding goal.

## 5. Defaults and gating — what's actually on

| Setting | Hermes default | Co default |
|---|---|---|
| Memory store loaded into agent | `memory_enabled=False` | `MemoryStore` always constructed (`co_cli/deps.py`) |
| User profile injected into system prompt | `user_profile_enabled=False` | n/a (no USER.md) |
| Memory nudge interval | `nudge_interval=10` turns | (combined w/ skill — see below) |
| Skill nudge interval | `creation_nudge_interval=10` tool iterations | n/a (combined) |
| Combined skill+memory reviewer enabled | n/a | `review_enabled=False` (default) |
| Combined reviewer interval | n/a | `review_nudge_interval=5` tool iterations (when enabled) |
| Reviewer budget | `max_iterations=8` (`run_agent.py:3502`) | `REVIEW_MAX_ITERATIONS=8` (`co_cli/config/skills.py:13`) |
| Reviewer timeout | best-effort, no explicit ceiling | `REVIEW_TIMEOUT_SECONDS=120` (`co_cli/config/skills.py:14`) |
| Skill curator enabled | inactivity-triggered (no flag) | `curator_enabled=False` |
| Skill curator interval | `DEFAULT_INTERVAL_HOURS = 24*7` (`agent/curator.py:39`) | `curator_interval_hours=168` |
| Stale skill threshold | `DEFAULT_STALE_AFTER_DAYS=30` (`agent/curator.py:41`) | `CURATOR_STALE_AFTER_DAYS=30` (`co_cli/config/skills.py:16`) |
| Archive skill threshold | `DEFAULT_ARCHIVE_AFTER_DAYS=90` (`agent/curator.py:42`) | `CURATOR_ARCHIVE_AFTER_DAYS=90` (`co_cli/config/skills.py:17`) |
| Cross-session search LLM summarization | always-on when `session_search` called | not implemented |
| Dialectic user-model backend | optional `HonchoMemoryProvider` | not implemented |
| Memory consolidation / dream cycle | not implemented | `consolidation_enabled=False` |

Symmetry observation: both projects ship learning-loop machinery *off* by default. The "self-improving" pitch in hermes is opt-in, same as it would be in co if co flipped `review_enabled` and `consolidation_enabled` on.

## 6. Confirmed gaps in co (grounded in absence of code)

### Gap A — LLM-summarized cross-session recall

**What hermes has:** `_summarize_session` (`tools/session_search_tool.py:196–258`) plus the `_truncate_around_matches` query-aware windowing (lines 111–193). Result: agent gets per-session digests focused on the query, not chunks.

**What co has:** BM25 chunk recall returning `(session_id, when, source, chunk_text, start_line, end_line, score)` (`co_cli/tools/session/recall.py:94–104`). No auxiliary-LLM call. Span attributes for `summarizer.*` are hardcoded zero literals at `co_cli/tools/session/recall.py:37–39`.

**Cost of the gap:** Main-agent context spend goes up on long-history queries (model must read raw chunks or chase `session_view` follow-ups). For short queries it's a wash.

### Gap B — Always-injected user profile in system prompt

**What hermes has:** `USER.md` injection at `run_agent.py:4819–4823` whenever `_user_profile_enabled` is set. Always present, no tool call required for the model to see "what we know about this user."

**What co has:** User-kind memory items in `~/.co-cli/memory/*.md` recalled through `memory_search`. Doctrine (soul/mindsets) is statically injected by the personality system, but it is *author-curated, not user-learned*.

**Cost of the gap:** Higher tool-call burden for any turn that needs user context; reliance on the model deciding to search; no canonical "this is who the user is" surface that the curator/reviewer writes back to.

### Gap C — Dialectic / backend user-modeling

**What hermes has:** Honcho integration (5 tools, per-turn prefetch with configurable cadence, dialectic Q&A). User model is *synthesized backend-side from observed conversation*.

**What co has:** Nothing on this axis. User facts are flat memory items; there is no dialectic Q&A layer, no peer-card abstraction, no per-turn user-context prefetch beyond the recall scoring already built into prompt assembly.

**Cost of the gap:** No deeper second-order user model (e.g., behavioral patterns, contradictions across sessions). Co's model of "who the user is" is exactly the union of memory items the agent has chosen to save.

### Non-gap surfaces (avoid investing here)

These appeared to be gaps in earlier rounds of analysis but are not, on code reading:

- **"Co has no memory nudge."** False. C1+C2+C3+C4 are the equivalent of H1+H3+H6 in combined form. Both are off-by-default. Co writes memory and skills from the same fork rather than from two distinct triggers, but the persisted behavior is the same.
- **"Co's skill self-improvement is weaker."** False on mechanism. `skill_manage` has the same verb set (`create / patch / edit / delete / write_file / remove_file`). The prompt is shorter and less emphatic about frustration signals — fixable in a prompt edit, not a code addition.
- **"Hermes has no batch memory consolidation."** True, but co's dream cycle covers it from a different angle. Not a co gap.
- **"Co's curator is weaker than hermes's."** Numbers (7d / 30d / 90d), state-file shape, and never-delete invariant match exactly.

## 7. Surfaces co has that hermes does not (for context)

These are not "self-improving" mechanisms per se, but they belong in the same map for honest comparison:

- **Personality doctrine** — soul/mindsets/critique injected statically each session, role-conditioned (Tars, Finch, Jeff…). Author-curated, not learned. (`docs/specs/personality.md`.)
- **Dream cycle** — single batch pass that mines transcripts → consolidates similar items → decays stale items, with archive-not-delete and `_dream_state.json` cross-cycle continuity. Hermes has no direct equivalent. (`co_cli/memory/dream.py`.)
- **Skill curator with lifecycle states** — same shape as hermes but spec'd as a state machine: `active → stale (30d) → archived (90d)`. Effective parity.
- **Atomic-write discipline** — `co_cli/fileio/atomic.atomic_write_text` used for every memory/skill/state-file write (`co_cli/skills/session_review.py:22,113,135`; same in dream). Hermes has parity (`hermes-agent/utils.py atomic_replace`).

## 8. Minimum-viable closures for the three gaps

Sketched against current co structure; no commitment, just shape.

### Gap A — LLM-summarized session recall (mechanical, small)

Add an optional summarization stage to `session_search`:

1. New helper in `co_cli/llm/call.py` or `co_cli/llm/aux.py`: `aux_call_llm(task, messages, …)` mirroring `async_call_llm`. Use the `auxiliary` model already configured in `docs/specs/config.md`.
2. In `co_cli/tools/session/recall.py`, after `_search_sessions` produces hits, group by `session_id` (already done), then for each unique session call `session_view`-equivalent to load a window around `(start_line, end_line)`, summarize via the aux model with a focused prompt.
3. Gate behind a config flag `sessions.summarize_results: bool = False` plus a `summarize_max_tokens` cap. Single-flight semaphore for concurrency.

Effort: small. ~150 lines net, one new prompt template, one config field, span attributes already exist as no-op placeholders so the span schema doesn't change.

### Gap B — `USER.md` artifact in the prompt (small)

1. New memory kind or named file: `~/.co-cli/memory/_user_profile.md` (or `kind='user_profile'` with a singleton invariant — pick one and document it).
2. In `co_cli/agent/prompt_assembly.py` (whichever module builds the static prompt), unconditionally inject the profile block if it exists.
3. Reviewer (C1) prompt addition: explicitly call out "consider updating the user profile" alongside the current "memory items" guidance. Or split into two prompts mirroring hermes's H3/H4/H5 layout.
4. Gate behind `memory.user_profile_enabled: bool = False` to match hermes's posture.

Effort: small. ~200 lines net, mostly prompt-assembly plumbing.

### Gap C — Dialectic user-modeling backend (medium-to-large)

Three possible shapes, in increasing scope:

1. **Embedded:** keep all user-modeling local. Periodic pass (separate from dream) that asks the auxiliary LLM "given recent transcripts and current user-profile, what's a better profile?" Writes back to `_user_profile.md`. Cheap, no external dep.
2. **Honcho plugin parity:** ship a `co_cli/memory/providers/honcho.py` MemoryProvider with the 5 tool schemas, same shape as hermes. Adds a network dependency.
3. **Generalized provider interface:** spec a `MemoryProvider` ABC (built-in + plug-in), with the embedded option as the default. This is the "right" answer if co ever wants Mem0, Letta, etc. integration.

(1) closes the Gap-C functional hole with no new dependency. (3) is the load-bearing one if co's mission Stage 2 ("durable user model") is treated as plugin-able infrastructure rather than a single feature.

Effort: (1) is medium, (3) is large. Mission language ("local, inspectable, reversible") argues against (2) as a default.

## 9. Bottom line

- **Two of hermes's five "self-improving" claims are at parity in code** (#1 skill creation, #2 skill self-improvement, with hermes ahead on prompt engineering but not mechanism).
- **One is at parity in shape but split differently** (#3 memory nudge — co does it in a combined reviewer, hermes in a dedicated one).
- **Two are genuine gaps** (#4 LLM-summarized session recall, #5 user profile + dialectic backend).
- **Both projects ship the learning loop off by default**; turning the existing co flags on already closes most of the perceived gap. The remaining structural delta is Gaps A, B, and C above.

The "self-improving" pitch is honest about hermes, but the gap to co on the core memory-and-skill loop is smaller than the marketing suggests. The real gap is on the *user-modeling and recall-summarization* surfaces, both of which co's mission already names as Stage 2.
