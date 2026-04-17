# Plan: Memory Layer Alignment (Package + Tool Surface)

**Task type: refactor (package rename + tool-surface cleanup)**

## Context

**Prerequisite:** `2026-04-17-fold-extractor-into-knowledge.md` must land first — it deletes `co_cli/memory/` entirely by moving its only meaningful file (`_extractor.py`) into `co_cli/knowledge/`. This plan takes the post-fold state as its baseline.

**The seam this plan closes:** After fold-extractor lands, the two cognitive layers are named inconsistently at the module level:
- `co_cli/knowledge/` — knowledge layer storage (correctly named for its role)
- `co_cli/session_index/` — memory layer storage (named for its mechanism, not its role)

At the tool surface, the agent sees two episodic-recall tools (`session_search` + `search_memories`) for one operation, with a stale comment misidentifying `search_articles` as a deprecated alias.

**Baseline (post-fold-extractor):**
- `co_cli/session_index/` — `SessionIndex` class, FTS5 over session transcripts. Imported by `bootstrap/core.py`, `deps.py`, `tools/session_search.py`, tests.
- `co_cli/tools/session_search.py` — `session_search(query, limit)` — actual implementation.
- `co_cli/tools/memory.py` — `search_memories(query, limit=5)` — one-line wrapper over `session_search`.
- `co_cli/agent/_native_toolset.py:142-151` — registers `session_search`, `search_memories` (marked deprecated), and `search_articles` under a stale `# Deprecated aliases` comment.
- `co_cli/deps.py:180` — `session_index: SessionIndex | None` field on `CoDeps`.

---

## Problem & Outcome

**Problem — two layers:**
- `co_cli/session_index/` names an implementation mechanism. The module's architectural role is the memory layer. `co_cli/knowledge/` is named for its role; `session_index` should follow the same convention.
- The agent sees `session_search` and `search_memories` as two distinct tools for one operation. "Session" is a storage unit; "memory" is the cognitive layer. Leaking storage vocabulary into the agent's decision surface creates unnecessary ambiguity.

**Outcome:** After this refactor:
- `co_cli/memory/` is the memory-layer module (renamed from `session_index/`). The layout is now symmetric: `co_cli/memory/` ↔ `co_cli/knowledge/`.
- `CoDeps.session_index` field → `CoDeps.memory_index`. `_init_session_index()` → `_init_memory_index()`. `SessionIndex` class → `MemoryIndex`.
- The LLM sees exactly one episodic-recall tool: `search_memory` (singular — consistent with `search_knowledge`; domain-as-layer naming per letta's `archival_memory_search` / `recall_memory_search`).
- `session_search` remains as an internal Python function in `tools/session_search.py`, not registered as an agent tool.
- `search_memories` (plural) is gone; `search_memory` (singular) replaces it with a first-class tool description.
- `# Deprecated aliases` comment block in `_native_toolset.py` is removed entirely. `search_articles` is re-commented as canonical (it is not an alias — it has its own article-slug workflow required by `read_article`).

**Naming rationale (singular):** Tool naming in co-cli follows **action_domain** where domain names the cognitive layer as a whole. `search_knowledge` is singular yet returns a collection — "knowledge" is the layer. Apply the same: `search_memory` = "search the memory layer." Letta uses `archival_memory_search(query)` (singular for search-on-layer) and `list_archival_memories()` (plural for item enumeration). CRUD/REST plural-for-collection is a separate convention for REST endpoints, not agent tool names.

---

## Scope

**In scope:**

*Package / module rename:*
- `git mv co_cli/session_index/ co_cli/memory/`
- Rename class `SessionIndex` → `MemoryIndex`, `SessionSearchResult` stays (result type name is fine).
- Rename `CoDeps.session_index` field → `CoDeps.memory_index`.
- Rename `_init_session_index()` → `_init_memory_index()` in `bootstrap/core.py` and its call site in `main.py`.
- Update all import sites: `from co_cli.session_index.*` → `from co_cli.memory.*`.
- Rename `tests/test_session_index.py` — merge or move to `tests/test_memory_index.py` (check for collision with existing `tests/test_memory.py`).

*Tool surface:*
- Rename `search_memories` → `search_memory` in `co_cli/tools/memory.py`. Update docstring to target text specified in TASK-2.
- Remove `session_search` from `_register_tool()` in `_native_toolset.py`.
- Promote `search_memory` out of `# Deprecated aliases` block; that block is removed entirely.
- Move `search_articles` registration above, with its own canonical comment.
- Update module docstring in `co_cli/tools/memory.py`.

*Grep audit (full scope):*
- `co_cli/`, `tests/`, `evals/`, `docs/specs/`, `README.md`, `co_cli/prompts/` (including `rules/04_tool_protocol.md`), `co_cli/context/tool_display.py`, `docs/reference/`.
- No references to `session_index` (Python module path) remain after rename.
- No references to `search_memories` remain.
- No `session_search` string literals as agent-tool names (Python imports/calls are fine).

*Spec sync:* `docs/specs/cognition.md`, `docs/specs/memory.md`, `docs/specs/flow-bootstrap.md`, `docs/specs/system.md`, `docs/specs/tools.md`, `docs/specs/observability.md` — all reference `session_index` or `search_memories` as canonical today. `/sync-doc` updates these post-delivery. The plan explicitly acknowledges this spec shift is part of the outcome.

**Out of scope:**
- `session_index.db` filename on disk — user-global data file at `~/.co-cli/`; renaming it is a migration concern outside this refactor.
- FTS5 schema, session JSONL format, or any storage behavior.
- `search_articles` tool behavior or registration (canonical, stays).
- Knowledge-layer tool names or registrations.

---

## Behavioral Constraints

- **No storage change.** `session_index.db` filename on disk, FTS5 schema, session JSONL format all untouched. `MemoryIndex` opens the same DB path as `SessionIndex` — no migration.
- **No result-shape change.** `search_memory` returns the same `ToolReturn` payload as `search_memories` — same fields, same formatting.
- **One tool-surface change visible to LLM.** `{session_search, search_memories}` → `{search_memory}`. Short-term prompt-history churn in tool-calling evals; personality/behavior evals unaffected.

---

## Regression Surface

- **6 Python import sites** for `co_cli.session_index` — mechanical update.
- **`deps.session_index` field name** — every `ctx.deps.session_index` reference becomes `ctx.deps.memory_index`. Grep needed across `co_cli/`, `tests/`, `evals/`.
- **`_init_session_index` function** — called from `main.py:287` and `bootstrap/core.py`; renamed to `_init_memory_index`.
- **`SessionIndex` class name** — referenced in `deps.py`, `bootstrap/core.py`, `tests/test_session_search_tool.py`, `tests/test_bootstrap.py`; renamed to `MemoryIndex`.
- **`04_tool_protocol.md`** — in-system-prompt every turn; any `search_memories` or `session_search` tool-name references must be updated. Highest-leverage miss if stale.
- **Tool-calling evals** — any eval asserting LLM picked `session_search` or `search_memories` must update to `search_memory`.
- **Over-triggering guard** — the crafted `search_memory` docstring uses strong proactive language borrowed from hermes. TASK-4 includes a neutral-prompt smoke check.

---

## Implementation Plan

### ✓ DONE — TASK-0 — Rename `co_cli/session_index/` → `co_cli/memory/`

**files:**
- `co_cli/session_index/` → `co_cli/memory/` (git mv)
- `co_cli/memory/_store.py` — rename class `SessionIndex` → `MemoryIndex`
- `co_cli/deps.py` — field `session_index` → `memory_index`, import update
- `co_cli/bootstrap/core.py` — function `_init_session_index` → `_init_memory_index`, import update
- `co_cli/main.py` — call site and import update
- `co_cli/tools/session_search.py` — `ctx.deps.session_index` → `ctx.deps.memory_index`
- `tests/test_session_index.py` → rename/merge; update imports
- `tests/test_session_search_tool.py` — import + field reference update
- `tests/test_bootstrap.py` — import + field reference update
- `evals/eval_bootstrap_flow.py` — import + field reference update

Steps:
1. `git mv co_cli/session_index co_cli/memory`
2. In `co_cli/memory/_store.py`: rename class `SessionIndex` → `MemoryIndex`. Update internal import `from co_cli.session_index._extractor` → `from co_cli.memory._extractor`.
3. In `co_cli/deps.py`: update import and field `session_index: SessionIndex | None` → `memory_index: MemoryIndex | None`.
4. In `co_cli/bootstrap/core.py`: rename function `_init_session_index` → `_init_memory_index`, update import.
5. In `co_cli/main.py`: update import and call site `_init_session_index(` → `_init_memory_index(`.
6. In `co_cli/tools/session_search.py`: update `ctx.deps.session_index` → `ctx.deps.memory_index`.
7. Rename `tests/test_session_index.py` → `tests/test_memory_index.py` (verify no collision with `tests/test_memory.py` — they test different modules). Update imports inside.
8. Update `tests/test_session_search_tool.py` and `tests/test_bootstrap.py` imports + field references.
9. Update `evals/eval_bootstrap_flow.py` imports + field references.
10. Full grep audit: `grep -rn "session_index" co_cli/ tests/ evals/` returns only `deps.session_index.db` path string references (the on-disk DB filename is NOT renamed) and plan/doc files. Zero Python module path references.

**done_when:** `from co_cli.memory._store import MemoryIndex; print('ok')` exits 0. `grep -rn "co_cli\.session_index\|co_cli/session_index" co_cli/ tests/ evals/` returns zero matches. `deps.session_index` references are zero in Python files (field renamed to `memory_index`).

**success_signal:** `uv run python -c "from co_cli.memory._store import MemoryIndex; print('ok')"` prints `ok`.

**prerequisites:** []

---

### ✓ DONE — TASK-1 — Rename `search_memories` → `search_memory`, rewrite docstring

**files:**
- `co_cli/tools/memory.py`

Steps:
1. Rename the function `search_memories` → `search_memory`.
2. Replace the docstring with the target text below. Patterns borrowed from hermes-agent's `session_search_tool.py` (proactive-trigger bullets, FTS5 syntax guidance, cost transparency) and fork-claude-code's anti-pattern voice. Sibling disambiguation against `search_knowledge` is mandatory.

    ```python
    async def search_memory(ctx: RunContext[CoDeps], query: str, *, limit: int = 5) -> ToolReturn:
        """Search episodic memory — past conversation transcripts across all sessions in this project. Returns ranked excerpts with session ID, date, and matching snippet.

        USE THIS PROACTIVELY when:
        - The user says "we did this before", "remember when", "last time", "as I mentioned"
        - The user asks about a topic you've worked on but don't have in current context
        - The user references a project, person, decision, or concept that seems familiar but isn't in the current session
        - You want to check if a similar problem has been solved before
        - The user asks "what did we do about X?" or "how did we fix Y?"

        Don't hesitate — search is local FTS5 (BM25), fast, zero LLM cost. Better to search and confirm than to guess or ask the user to repeat themselves.

        Search syntax (FTS5): keywords joined with OR for broad recall (auth OR login OR session), phrases for exact match ("connection pool"), boolean (python NOT java), prefix (deploy*). IMPORTANT: FTS5 defaults to AND between terms — use explicit OR for broader matches. If a broad OR query returns nothing, try individual keywords.

        Do NOT use for saved preferences, rules, project conventions, or reusable knowledge artifacts — use search_knowledge for those. This tool searches what was SAID in past conversations; search_knowledge searches what was DISTILLED from them.

        Args:
            query: FTS5 keyword query (see syntax above).
            limit: Max results to return (default 5).
        """
        return await session_search(ctx, query, limit)
    ```

3. Update the module docstring: `"""Memory tools — episodic recall over session transcripts."""`

**done_when:** `co_cli/tools/memory.py` defines only `search_memory`. First docstring line reads as a standalone action sentence, under 200 chars, no "delegates to" or "alias of" wording.

**success_signal:** N/A (mechanical).

**prerequisites:** [TASK-0]

---

### ✓ DONE — TASK-2 — Fix registration + comments in `_native_toolset.py`

**files:**
- `co_cli/agent/_native_toolset.py`

Steps:
1. Update import: `from co_cli.tools.memory import search_memory` (was `search_memories`).
2. Drop import of `session_search` from `_native_toolset.py` if it's only used for registration (verify — if used elsewhere, keep).
3. Delete `_register_tool(session_search, ...)` call.
4. Rename `_register_tool(search_memories, ...)` → `_register_tool(search_memory, ...)`.
5. Remove the `# Deprecated aliases — delegate to canonical tools; remove in a future pass` comment block entirely. Replace with:
   - A clean `# Episodic memory recall` comment above `search_memory`'s registration.
   - A clean `# Article index` comment above `search_articles`' registration, making clear it's canonical (required by the `search → read_article` slug-lookup workflow).
6. Run `uv run ruff check co_cli/agent/_native_toolset.py` — fix any unused import warnings.

**done_when:** `grep -n "session_search\|search_memories\|Deprecated aliases" co_cli/agent/_native_toolset.py` returns zero matches. `search_memory` appears once as a registration. `search_articles` is registered with a canonical comment.

**success_signal:** N/A.

**prerequisites:** [TASK-1]

---

### ✓ DONE — TASK-3 — Full grep sweep and in-prompt rule update

**files:**
- `co_cli/prompts/rules/04_tool_protocol.md` (highest priority — in system prompt every turn)
- `co_cli/context/tool_display.py`
- `README.md`
- `docs/reference/` (research docs — update for accuracy)
- `tests/` — tool-name assertions in test body strings
- `evals/` — eval assertions on LLM tool selection

Steps:
1. `grep -rn "search_memories" co_cli/ tests/ evals/ docs/ README.md` — update every hit to `search_memory`.
2. `grep -rn "session_search" co_cli/ tests/ evals/ docs/ README.md` — for each hit: (a) Python import/function call → leave alone; (b) string literal referring to agent tool name → update to `search_memory`.
3. `grep -rn "SessionIndex\|session_index" co_cli/ tests/ evals/` — confirm zero Python-module references remain (only DB filename strings are acceptable).
4. In `co_cli/prompts/rules/04_tool_protocol.md`: update any `session_search` or `search_memories` tool-name references. This file is injected into system prompt every turn — stale names here actively mislead the LLM.

**done_when:** `grep -rn "search_memories" co_cli/ tests/ evals/ docs/ README.md` = zero. `grep -rn "\"session_search\"\|'session_search'" co_cli/ tests/ evals/` = zero. `04_tool_protocol.md` references only `search_memory` for episodic recall.

**prerequisites:** [TASK-2]

---

### ✓ DONE — TASK-4 — Full test suite + smoke checks

**files:** none (verification only)

1. Full suite: `mkdir -p .pytest-logs && uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-memory-alignment.log`
2. Import smoke — package rename: `uv run python -c "from co_cli.memory._store import MemoryIndex; print('ok')"`
3. Tool-surface smoke: verify `search_memory` is registered and old names are not:
   ```bash
   uv run python -c "
   from co_cli.agent._native_toolset import _build_native_toolset
   from co_cli.deps import CoDeps
   # adjust if _build_native_toolset needs deps — goal is to list registered tool names
   print('check tool registration directly via import')
   from co_cli.tools.memory import search_memory
   print('search_memory ok')
   try:
       from co_cli.tools.memory import search_memories
       print('FAIL: search_memories still exists')
   except ImportError:
       print('search_memories gone ok')
   "
   ```
4. Over-triggering guard — neutral prompt must NOT trigger `search_memory`:
   Run a quick agent turn with a neutral math query ("what is 2 + 2?") and confirm `search_memory` does not appear in the tool calls. This guards against the strong "don't hesitate" + proactive-trigger-bullets language causing the agent to recall memory on every turn.
   ```bash
   uv run co chat --once "what is 2 + 2?" 2>&1 | grep -c "search_memory" | xargs -I{} test {} -eq 0 && echo "ok" || echo "FAIL: search_memory over-triggered"
   ```
   If `--once` flag doesn't exist, use the equivalent single-turn invocation. Adjust command to match actual CLI surface.

**done_when:** `uv run pytest` exits 0 AND all 4 smoke checks pass.

**prerequisites:** [TASK-3]

---

## Testing

- **Full suite:** `uv run pytest` exits 0.
- **Package audit:** `from co_cli.memory._store import MemoryIndex` succeeds. `co_cli/session_index/` directory does not exist.
- **Tool-surface audit:** `search_memory` is the only episodic-recall tool registered. `session_search` and `search_memories` are not registered.
- **Grep audit:** zero `co_cli.session_index` module references, zero `search_memories` references, zero `session_search` agent-tool-name references.
- **In-prompt rule:** `04_tool_protocol.md` references `search_memory` only.
- **Over-triggering:** neutral prompt does not invoke `search_memory`.

---

## Risks and Mitigations

- **`session_index.db` filename is intentionally unchanged.** It is impl-level (same rationale as `search.db` not being named `knowledge.db`). `MemoryIndex` opens the same path as `SessionIndex` did — confirm the path constant in `memory/_store.py` is not touched during the rename.
- **`deps.memory_index` field rename** touches every `ctx.deps.session_index` reference. Grep sweep in TASK-0 is the mitigation; any miss surfaces as an `AttributeError` at runtime.
- **Test file collision:** `tests/test_session_index.py` → `tests/test_memory_index.py` may conflict with `tests/test_memory.py`. Check before renaming — if both cover overlapping territory, merge rather than having two files.
- **Over-triggering:** "don't hesitate" framing in `search_memory` docstring is intentionally strong. The TASK-4 neutral-prompt smoke check is the guard. If the agent fires on neutral input, soften the proactive language — "search when context clearly warrants" rather than "don't hesitate."
- **Sequencing:** fold-extractor-into-knowledge must land first. If both land together, confirm `co_cli/memory/` doesn't exist before this plan's TASK-0 runs the `git mv`.

---

## Follow-ups (not in this plan)

- Consider whether `search_articles` should eventually fold into `search_knowledge` article-mode — tracked separately, not here.

---

## Implementation Review — 2026-04-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-0 | `from co_cli.memory._store import MemoryIndex` exits 0; zero `co_cli.session_index` module refs; zero `deps.session_index` field refs | ✓ pass | `co_cli/memory/_store.py:80` — class `MemoryIndex`; `co_cli/deps.py:180` — `memory_index: MemoryIndex | None`; `co_cli/bootstrap/core.py:304` — `from co_cli.memory._store import MemoryIndex`; grep audit returns zero |
| TASK-1 | `co_cli/tools/memory.py` defines only `search_memory`; first docstring line under 200 chars, no "delegates to" wording | ✓ pass | `co_cli/tools/memory.py:10` — `async def search_memory`; module docstring `memory.py:1` is "Memory tools — episodic recall over session transcripts"; `ImportError` on `search_memories` confirmed |
| TASK-2 | zero matches for `session_search\|search_memories\|Deprecated aliases` in `_native_toolset.py`; `search_memory` registered once; `search_articles` has canonical comment | ✓ pass | `_native_toolset.py:31` — `from co_cli.tools.memory import search_memory`; `_native_toolset.py:141-142` — `search_memory` registered ALWAYS; `_native_toolset.py:144` — canonical comment for `search_articles` |
| TASK-3 | zero `search_memories` in code+refs; zero `session_search` string literals in tests; `04_tool_protocol.md` has `search_memory` only | ✓ pass | grep audit zero; `04_tool_protocol.md:50-51` — `search_memory`; README.md:164 updated |
| TASK-4 | `uv run pytest` exits 0; all 4 smoke checks pass | ✓ pass | 600 passed; import smoke, tool_index smoke, over-trigger smoke all confirmed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale module docstring path `~/.co-cli/co-cli-search.db` (knowledge store path, not session index path) | `co_cli/memory/_store.py:3` | minor | Updated to `~/.co-cli/session-index.db` |
| Stale module docstring "Session index" after package rename | `co_cli/memory/__init__.py:1` | minor | Updated to "Memory layer — FTS5 keyword search..." |

### Tests
- Command: `uv run pytest -x`
- Result: 600 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: full — sync-doc run during delivery across 7 spec files
- Result: fixed: `cognition.md`, `memory.md`, `tools.md`, `context.md`, `flow-bootstrap.md`, `system.md`, `observability.md` — all `session_index`/`SessionIndex`/`search_memories`/`session_search` references updated

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM online, all subsystems nominal
- Tool registry smoke: `search_memory` registered ALWAYS; `session_search` not registered; `search_memories` not registered; `search_articles` canonical ✓

### Overall: PASS
All tasks implemented as specified. Package rename complete with zero stale references. Tool surface reduced from `{session_search, search_memories}` to `{search_memory}`. 600 tests green. Ship directly.
