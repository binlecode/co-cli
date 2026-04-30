# Exec Plan: Explicit Knowledge Save

_Created: 2026-04-28_
_Updated: 2026-04-30 — blocker resolved; Phase 3 retired; tagging rationale corrected; tagging subsection dropped (tags= removed from memory_create)_
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

2. ~~**DEFERRED tool not named**~~ — **resolved**: `memory_create` and `memory_modify` promoted
   to `visibility=ALWAYS` in `write.py`. Both are now first-tier tools, no discovery step needed.

3. **No kind-selection heuristics**: the docstring lists all 7 kinds but gives no guidance
   on which to pick. Ambiguous cases (`feedback` vs `rule`, `reference` vs `article`) require
   an explicit decision table in the protocol.

4. ~~**No tagging rule for `personality-context`**~~ — **void**: `memory_create` no longer
   accepts `tags=`. The `personality-context` tag and all tagging logic were removed by
   `remove-tagging-logic` (2026-04-30). Gap #4 no longer applies.

### Note on shared write path

The sync save calls `memory_create` directly — the same `KnowledgeStore` write path the dream
pipeline uses (`co_cli/memory/service.py:save_artifact`). No parallel service, no new abstraction.
The only difference is timing: same-turn (sync) vs. post-session (dream).

### Note on tagging

`memory_create` does not accept `tags=`. The `personality-context` tag and all tagging logic
were removed by `remove-tagging-logic` (2026-04-30). Gap #4 from the original problem statement
is void. The `## Memory` section in `04_tool_protocol.md` must NOT include any tagging guidance.

## Out of Scope

- Dream cycle and `dream_miner.md` changes — already done.
- A dedicated "save preference" tool — `memory_create` with correct kind + tag already covers it.
- Cross-session recall — `memory_search` already covers all channels.
- `personality/prompts/loader.py` — `load_personality_memories` was deleted by `fix-prompt-assembly-order`; loader.py now exposes `load_soul_seed`, `load_soul_critique`, `load_soul_mindsets` only. Not touched by this plan.

## Phases

### ~~Phase 1: Dream miner tagging~~ — VOID (2026-04-30)

`dream_miner.md` previously contained a `personality-context` tagging table. That tagging
logic was removed by `remove-tagging-logic` (2026-04-30). No tagging guidance exists in
`dream_miner.md` or anywhere else — and none is needed. Phase 1 is void.

---

### Phase 2: Add explicit-save guidance to `04_tool_protocol.md`

**Current state (2026-04-30):** `04_tool_protocol.md` has no `## Memory` section at all.
`memory_create` is `visibility=ALWAYS` (gap #2 resolved). Gaps #1 and #3 remain: no trigger,
no kind-selection table. Gap #4 (tagging) is void — `tags=` was removed from `memory_create`.

#### Design

Append a `## Memory` section to `co_cli/context/rules/04_tool_protocol.md`. Covers gaps #1 and #3:
trigger recognition and kind-selection table. Gap #2 (DEFERRED discovery) is already resolved —
`memory_create` is `visibility=ALWAYS`. Gap #4 (tagging) is void.

```markdown
## Memory

### Explicit saves

When the user explicitly asks to remember or save something — "remember I prefer X",
"always do Y", "we decided Z", "save this URL", "remember this note" — call `memory_create`
synchronously in the same turn. Do not defer to the dream cycle; dream handles implicit
patterns only.

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
```

#### ✓ DONE — TASK-3 — Append explicit-save guidance to `04_tool_protocol.md § Memory`

```
files:
  - co_cli/context/rules/04_tool_protocol.md

done_when:
  - grep -c "memory_create" co_cli/context/rules/04_tool_protocol.md returns >= 1
  - The ## Memory section contains the kind-selection table
  - uv run pytest tests/prompts/ -x passes (no regressions in static instructions)

success_signal:
  - "remember I prefer pytest" → memory_create(artifact_kind="preference") same turn, no dream wait
  - "save this URL" → memory_create(artifact_kind="reference") same turn
  - "we decided to use PostgreSQL" → memory_create(artifact_kind="decision") same turn
```

---

### ~~Phase 3 — Pinning / cap increase~~ — RETIRED (2026-04-30)

`load_personality_memories` was deleted by `fix-prompt-assembly-order`. The `[:5]` cap and
injection pipeline no longer exist. `personality-context` artifacts are reachable via
`memory_search` on demand — no cap applies to search results. Phase 3 is void.

---

## Key File Locations

| Component | File |
|---|---|
| Tool protocol rules | `co_cli/context/rules/04_tool_protocol.md` |
| `memory_create` / `memory_modify` | `co_cli/tools/memory/write.py` |
| `KnowledgeStore` | `co_cli/memory/knowledge_store.py` |
| Artifact write path (`save_artifact`) | `co_cli/memory/service.py` |
| Dream miner prompt (Phase 1 — done) | `co_cli/memory/prompts/dream_miner.md` |

## Delivery Summary — 2026-04-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-3 | memory_create in 04_tool_protocol.md (grep=1) + ## Memory section with kind-selection table + tests/prompts/ passes | ✓ pass |

**Tests:** scoped (`tests/prompts/`) — 8 passed, 0 failed
**Doc Sync:** fixed — `prompt-assembly.md` Files table: removed phantom `load_personality_memories()`, replaced with actual exports

**Overall: DELIVERED**
TASK-3 shipped: `## Memory` section appended to `co_cli/context/rules/04_tool_protocol.md` with explicit-save trigger and kind-selection table; no tagging guidance (tags= removed from memory_create).
