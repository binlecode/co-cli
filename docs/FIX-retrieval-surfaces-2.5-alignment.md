# FIX: Retrieval Surfaces 2.5 Alignment Review (co-cli)

## Scope
Review date: 2026-03-05

This document captures findings from reviewing `docs/DESIGN-knowledge.md` section 2.5
("Retrieval surfaces") against current code behavior and proposes concrete fixes.

Primary references:
- `docs/DESIGN-knowledge.md`
- `co_cli/agent.py`
- `co_cli/_history.py`
- `co_cli/tools/articles.py`
- `co_cli/tools/memory.py`

## Findings (Ranked)

### 1. `search_knowledge` fallback semantics are under-specified in section 2.5 (Medium)

Files:
- `docs/DESIGN-knowledge.md:123`
- `docs/DESIGN-knowledge.md:130`
- `co_cli/tools/articles.py:71`
- `co_cli/tools/articles.py:76`
- `co_cli/tools/articles.py:93`
- `co_cli/tools/memory.py:62`

Problem:
- Section 2.5 describes grep fallback as effectively `source="memory"` only.
- Actual behavior allows fallback when `source is None` and loads from
  `.co-cli/knowledge` by `kind`, which can include both memories and articles.
- Fallback result payload currently hardcodes `"source": "memory"` even when
  a returned item is `kind="article"`, which can blur source semantics.

Impact:
- Design doc readers can form an inaccurate mental model of fallback behavior.
- Result metadata can be ambiguous for downstream consumers and prompt logic.

### 2. Retrieval path mutates knowledge on normal recall flow (Medium)

Files:
- `docs/DESIGN-knowledge.md:153`
- `co_cli/_history.py:541`
- `co_cli/_history.py:573`
- `co_cli/tools/memory.py:971`
- `co_cli/tools/memory.py:996`
- `co_cli/tools/memory.py:302`

Problem:
- `inject_opening_context()` calls `recall_memory()` on each new user turn.
- `recall_memory()` runs dedup-on-read and touch-on-read:
  - `_dedup_pulled()` can merge and delete files.
  - `_touch_memory()` updates `updated` in file frontmatter.
- Section 2.5 mentions this side effect, but the doc currently frames it as a note,
  not as a major retrieval contract tradeoff.

Impact:
- Read operations have write/delete side effects in steady-state conversation.
- Behavior is intentional but high leverage; it warrants explicit contract framing.

### 3. Section 2.5 tool signature summary is not exact (Low)

Files:
- `docs/DESIGN-knowledge.md:114`
- `co_cli/tools/articles.py:32`

Problem:
- The section lists `search_knowledge(query, source?, kind?, tags?, created_after?, created_before?)`.
- Actual tool signature also includes `limit` and `tag_match_mode`.

Impact:
- Minor doc drift; small but avoidable confusion when debugging tool-call behavior.

## Fix Logic

## Fix A: Tighten section 2.5 fallback semantics and metadata expectations

Target file:
- `docs/DESIGN-knowledge.md`

Implementation logic:
1. Update `search_knowledge` subsection text to reflect real fallback behavior:
   - Fallback applies when `knowledge_index is None`.
   - `source=None` searches `.co-cli/knowledge` entries by `kind`.
   - Explicit `source in {"obsidian", "drive"}` returns empty in fallback mode.
2. Clarify that fallback currently uses a memory-backed local file loader
   (`_load_memories`) for both memory/article kinds.
3. Add a short note on current payload caveat:
   - Fallback currently sets `source: "memory"` for returned rows.

Acceptance criteria:
- Section 2.5 fallback description matches `co_cli/tools/articles.py` behavior exactly.
- No contradiction remains between lines describing fallback scope and source filter behavior.

## Fix B: Promote retrieval mutation side effects to explicit contract language

Target file:
- `docs/DESIGN-knowledge.md`

Implementation logic:
1. Keep the existing side-effect note but elevate it into explicit contract language:
   - "Recall is read+maintenance, not read-only."
2. Specify where mutation happens in the runtime path:
   - `inject_opening_context -> recall_memory -> _dedup_pulled/_touch_memory`.
3. Add guardrail note:
   - dedup-on-read may delete older duplicates.
   - this is intentional lifecycle behavior, not accidental write amplification.

Acceptance criteria:
- Section 2.5 clearly states retrieval mutation as a first-class design tradeoff.
- A reader can identify mutation points without reading code.

## Fix C: Make tool signature list exact and future-proof

Target file:
- `docs/DESIGN-knowledge.md`

Implementation logic:
1. Update `search_knowledge(...)` signature line in section 2.5 to include:
   - `limit`
   - `tag_match_mode`
2. Keep optional marker style consistent with the rest of the section.

Acceptance criteria:
- Signature line for `search_knowledge` matches the function definition in
  `co_cli/tools/articles.py`.

## Optional Code Follow-up (Not Required for Doc Sync)

### Code Follow-up D: Correct fallback `source` metadata in `search_knowledge`

Target file:
- `co_cli/tools/articles.py`

Implementation logic:
1. In fallback path result serialization, set `source` based on entry kind/source intent
   instead of always `"memory"`.
2. Preserve existing return schema (`display`, `count`, `results`) for compatibility.

Acceptance criteria:
- Fallback results for `kind="article"` are no longer mislabeled as `source="memory"`.

## Suggested Change Sequence

1. Apply Fix A/C together (quick doc/code alignment).
2. Apply Fix B to make the lifecycle tradeoff explicit.
3. Optionally implement Code Follow-up D if source metadata accuracy matters for callers.

## Validation Checklist

- Manual doc/code cross-check:
  - `docs/DESIGN-knowledge.md` section 2.5 vs `co_cli/tools/articles.py` and `co_cli/tools/memory.py`
- Optional code check for Follow-up D:
  - `uv run pytest tests/test_tools.py -q`
  - add/adjust functional assertions for `search_knowledge` fallback metadata if tests exist

## Expected Outcome

After these fixes:
- Section 2.5 is exact and reviewer-friendly.
- Retrieval side effects are clearly framed as intentional lifecycle mechanics.
- Optional code follow-up can remove fallback source-label ambiguity without changing tool shape.
