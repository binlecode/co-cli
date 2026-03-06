# TODO: Retrieval Surfaces 2.5 Alignment

Task type: doc

## Context

Source: `docs/FIX-retrieval-surfaces-2.5-alignment.md` (review date 2026-03-05).

No prior REVIEW verdict for this scope. No existing TODO file found.

**Code Accuracy Verification — section 2.5 vs actual code (pre-plan scan):**

| Claim in `docs/DESIGN-knowledge.md` §2.5 | Actual source | Status |
|---|---|---|
| `search_knowledge(query, source?, kind?, tags?, created_after?, created_before?)` | `articles.py:169–179` — actual signature adds `limit: int = 10` and `tag_match_mode: Literal["any", "all"] = "any"` | **Inaccuracy** |
| Fallback: "Grep fallback only for `.co-cli/knowledge` files (`source='memory'` only)" | `articles.py:210` — guard is `if source is not None and source != "memory"`. `source=None` is also allowed and loads all kinds from `.co-cli/knowledge` | **Inaccuracy — condition is under-specified** |
| Fallback `source` payload field | `articles.py:230` — hardcodes `"source": "memory"` even when `m.kind == "article"`. This matches FTS indexing convention (`save_article` also calls `index(source="memory", ...)`) so it is consistent behavior, but the doc does not explain the convention. | **Doc omission — convention undocumented** |
| Retrieval mutation framed as "Important side effect" note | Correct behavior, but framing is a passive note. Contract language is absent. | **Framing gap** |
| `inject_opening_context → recall_memory` call chain | Confirmed: `_history.py:573` | Accurate |
| `_dedup_pulled` + `_touch_memory` called within `recall_memory` | Confirmed: `memory.py:257, 281, 511` | Accurate |

No previously-shipped sections — this is a net-new TODO.

## Problem & Outcome

**Problem:** Section 2.5 of `DESIGN-knowledge.md` contains three inaccuracies / framing gaps:
1. `search_knowledge` signature omits two parameters (`limit`, `tag_match_mode`).
2. Fallback semantics described as `source="memory"` only, but `source=None` also routes to fallback and loads any `kind`.
3. Retrieval mutation side effects (dedup-on-read, touch-on-read) are a passive note instead of explicit contract language.

The fallback result `source="memory"` for all entries is intentional (matches FTS indexing convention), but the doc does not explain why.

**Outcome:** After these fixes:
- Section 2.5 is exact and reviewer-friendly with no doc/code contradiction.
- Retrieval mutation is elevated to first-class contract language.
- Fallback `source="memory"` convention is documented with rationale.

## Scope

In scope:
- Doc fixes: `docs/DESIGN-knowledge.md` section 2.5 only.

Out of scope:
- Code changes — fallback `source="memory"` behavior is intentional and consistent with FTS path; no code fix needed.
- Fixing other known implementation issues listed in section 2.10 (pre-existing, tracked separately).

## High-Level Design

Two doc-only tasks covering three logical fixes:

**Fix A+C (doc, TASK-1):** Update `search_knowledge` signature line to add missing `limit` and `tag_match_mode` params; rewrite fallback subsection to accurately describe the fallback condition, `source=None` scope, and the `source="memory"` convention (which matches FTS indexing to maintain caller consistency).

**Fix B (doc, TASK-2):** Elevate the "Important side effect" note in `recall_memory` to explicit contract language. Name the exact mutation points in the runtime call chain.

Fixes A and C are tightly coupled (same signature line + same fallback subsection) — shipped together in TASK-1.
Fix B is standalone — TASK-2.

## Implementation Plan

### TASK-1: Fix search_knowledge signature and fallback semantics in §2.5 (doc)

files:
- `docs/DESIGN-knowledge.md`

Change 1 — Signature line (§2.5, agent-registered tools list):

Replace:
```
- `search_knowledge(query, source?, kind?, tags?, created_after?, created_before?)`
```
With:
```
- `search_knowledge(query, kind?, source?, limit?, tags?, tag_match_mode?, created_after?, created_before?)`
```

Change 2 — Fallback subsection (§2.5 `search_knowledge` behavior, "Without index" bullet):

Replace:
```
- Without index:
  - Grep fallback only for `.co-cli/knowledge` files (`source="memory"` only).
  - `obsidian` and `drive` source filters return empty in fallback mode.
```
With:
```
- Without index (`knowledge_index is None`):
  - Grep fallback searches `.co-cli/knowledge` files directly via `_load_memories`.
  - `source=None` (default) and `source="memory"` both route to fallback; `kind` filter
    is respected so both memory and article entries can be returned.
  - `source="obsidian"` or `source="drive"` return empty immediately — these sources
    require the FTS index.
  - Fallback result payload sets `source: "memory"` for all rows regardless of `kind`.
    This is intentional: locally-stored knowledge (memories and articles alike) uses
    `source="memory"` in the FTS index (see `save_article` → `index(source="memory", ...)`),
    so fallback mirrors the same convention for caller consistency.
```

done_when:
- `grep -n "limit" docs/DESIGN-knowledge.md` returns a match on the `search_knowledge` signature line in §2.5.
- `grep -n "tag_match_mode" docs/DESIGN-knowledge.md` returns a match on the same signature line.
- `grep -n "knowledge_index is None" docs/DESIGN-knowledge.md` returns a match in the fallback subsection.
- `grep -F 'fallback only for' docs/DESIGN-knowledge.md` returns no matches (unique fragment from old text, replaced by "Grep fallback searches").

### TASK-2: Elevate retrieval mutation to explicit contract language in §2.5 (doc)

files:
- `docs/DESIGN-knowledge.md`

The current "Important side effect" note at the end of the `recall_memory` behavior subsection:
```
Important side effect:
- Retrieval can mutate data (`updated` timestamps and dedup-on-read merges/deletes).
```

Replace with a named subsection and explicit contract language directly after the numbered
flow steps:
```
#### Retrieval mutation contract

Recall is read+maintenance, not read-only. The following mutations occur on every
`recall_memory` call:

- `_touch_memory()`: updates the `updated` frontmatter timestamp on each directly matched entry.
- `_dedup_pulled()`: may merge and delete older duplicate entries.

Runtime call chain:
  `inject_opening_context` → `recall_memory` → `_dedup_pulled` / `_touch_memory`

Both side effects are intentional lifecycle mechanics, not accidental write amplification.
`decay_protected` entries are exempt from dedup deletion but still receive touch updates.
```

done_when:
- `grep -F "read+maintenance" docs/DESIGN-knowledge.md` returns a match (literal `+` requires fixed-string mode).
- `grep -n "_touch_memory" docs/DESIGN-knowledge.md` returns a match in the new subsection.
- `grep -n "_dedup_pulled" docs/DESIGN-knowledge.md` returns a match in the new subsection.
- `grep -n "Important side effect" docs/DESIGN-knowledge.md` returns no match (old phrasing removed).

## Testing

- TASK-1 and TASK-2: Doc-only changes. Verified by grep cross-checks in each task's `done_when`.
- No code changes — fallback `source="memory"` behavior is preserved intentionally.

## Open Questions

None — all questions answerable from code inspection were resolved during pre-plan verification.

## Final — Team Lead

Plan approved. Two cycles completed — C1 had 3 blocking items (resolved), C2 approved with minor-only findings (both adopted).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev retrieval-surfaces-2.5-alignment`
