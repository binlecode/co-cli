# Exec Plan: Explicit Knowledge Save

_Created: 2026-04-28_
_Slug: explicit-knowledge-save_
_Task type: code-feature_

## Problem

When the user explicitly says "remember this", the agent has no prompt guidance to act on it
in the same turn. The result: the request is ignored, or saved with wrong/missing fields,
or deferred to the dream cycle (which is post-session, best-effort, and not for explicit requests).

**Scope**: sync session → knowledge save triggered by explicit user instruction.
Covers all artifact kinds (`preference`, `decision`, `rule`, `feedback`, `reference`, `note`).
Not a pipeline — one synchronous `memory_create` call in the same turn.
The dream cycle (implicit pattern extraction) is a separate mechanism; out of scope.

### Root cause — four gaps in `04_tool_protocol.md § Memory`

1. **No trigger recognition**: no instruction to call `memory_create` when the user says
   "remember X", "always do Y", "save this", "we decided Z".

2. **DEFERRED tool not named**: `memory_create` has `visibility=DEFERRED` — it is not in the
   initial tool list. The agent must call `search_tools` to discover it. Without an explicit
   name in the protocol rule, the agent has no reason to look for it and will conclude the
   capability doesn't exist.

3. **No kind-selection heuristics**: the docstring lists all 7 kinds but gives no guidance
   on which to pick. Ambiguous cases (`feedback` vs `rule`, `reference` vs `article`) require
   an explicit decision table in the protocol.

4. **No tagging rule for `personality-context`**: the tag controls auto-injection into future
   sessions via `load_personality_memories`. Without guidance, the agent either tags everything
   (bloating the injected prompt) or nothing (defeating injection). The rule is: tag only
   `preference` and `feedback` (and conditionally `rule`); never tag `reference`, `decision`,
   `article`, `note`.

### Background — dream miner tagging (resolved externally, context only)

Fixed in memory-surface-unification TASK-3 (2026-04-28). The miner now tags `preference` and
`feedback` artifacts with `tags=["personality-context"]`. Not part of this plan's delivery.

### Note on shared write path

The sync save calls `memory_create` directly — the same `KnowledgeStore` write path the dream
pipeline uses (`co_cli/memory/store.py`). No parallel service, no new abstraction.
The only difference is timing: same-turn (sync) vs. post-session (dream).

## Out of Scope

- Dream cycle and `dream_miner.md` changes — already done.
- A dedicated "save preference" tool — `memory_create` with correct kind + tag already covers it.
- Cross-session recall — `memory_search` already covers all tiers.
- `load_personality_memories` (`loader.py:56`) — must not change; only the protocol rule changes.

## Phases

### ✓ DONE — Phase 1: Dream miner tagging (resolved externally, context only)

Absorbed into memory-surface-unification TASK-3 (2026-04-28).

| Artifact kind | Tag personality-context? |
|---|---|
| `preference` | Always |
| `feedback` | Always |
| `rule` | Conditional — only if it reads as a forward-acting standing instruction |
| `reference`, `decision`, `article`, `note` | Never |

---

### Phase 2: Add explicit-save guidance to `04_tool_protocol.md`

#### Design

Append to `## Memory` in `co_cli/context/rules/04_tool_protocol.md`. The block must cover
all four gaps in order: trigger, DEFERRED discovery, kind selection, tagging rule.

```markdown
### Explicit saves

When the user explicitly asks to remember or save something — "remember I prefer X",
"always do Y", "we decided Z", "save this URL", "remember this note" — call `memory_create`
synchronously in the same turn. Do not defer to the dream cycle; dream handles implicit
patterns only.

`memory_create` is a deferred tool — call `search_tools("memory_create")` to load its schema
before invoking it.

**Kind selection:**

| User intent | artifact_kind |
|---|---|
| Stable personal preference | `preference` |
| Behavioral correction / "always / never / stop" | `feedback` |
| Forward-acting standing rule or constraint | `rule` |
| Recorded decision (project or design) | `decision` |
| URL or external resource to save | `reference` |
| Web article to index | `article` |
| Free-form note | `note` |

**Tagging rule — `personality-context`:**
Add `tags=["personality-context"]` only for `preference` and `feedback`.
For `rule`, add it only if the rule reads as a forward-acting standing instruction.
Never add it to `reference`, `decision`, `article`, or `note` — those are recalled
on demand via `memory_search`, not injected into every session prompt.
```

#### TASK-3 — Append explicit-save guidance to `04_tool_protocol.md § Memory`

```
files:
  - co_cli/context/rules/04_tool_protocol.md

done_when:
  - grep -c "memory_create" co_cli/context/rules/04_tool_protocol.md returns >= 1
  - grep -c "search_tools" co_cli/context/rules/04_tool_protocol.md returns >= 1
  - grep -c "personality-context" co_cli/context/rules/04_tool_protocol.md returns >= 1
  - The ## Memory section contains the kind-selection table
  - The ## Memory section contains the tagging rule

success_signal:
  - "remember I prefer pytest" → memory_create(artifact_kind="preference",
    tags=["personality-context"]) same turn, no dream wait
  - "save this URL" → memory_create(artifact_kind="reference") same turn, no personality-context tag
  - "we decided to use PostgreSQL" → memory_create(artifact_kind="decision") same turn, no tag
```

---

### Phase 3 — Pinning / cap increase (deferred — measurement-gated)

`load_personality_memories` (`loader.py:64`) takes `[:5]` by recency. Real risk once users
accumulate > 5 `personality-context` artifacts — an early standing rule falls off the prompt.

**Preconditions before any implementation:**
1. Count `personality-context` artifacts in `~/.co-cli/knowledge/`. If ≤ 5, skip.
2. Confirm a real loss case — artifact present in store but absent from the injected prompt.

If both are met: Option A — increase cap (`[:5]` → `[:10]`). Option B — `pinned: true`
frontmatter (requires schema + loader + tool changes). Try A first.

**Re-evaluation trigger:** artifact count > 5 after Phase 2 ships, or a confirmed miss.

---

## Key File Locations

| Component | File |
|---|---|
| Tool protocol rules | `co_cli/context/rules/04_tool_protocol.md` |
| `memory_create` / `memory_modify` | `co_cli/tools/memory/write.py` |
| `KnowledgeStore` write path | `co_cli/memory/store.py` |
| Personality-context loader | `co_cli/personality/prompts/loader.py:39–72` |
| Dream miner prompt (context only) | `co_cli/memory/prompts/dream_miner.md` |
