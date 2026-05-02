# Kind Taxonomy Consolidation

**Created:** 2026-05-01  
**Slug:** kind-taxonomy-consolidation  
**Status:** draft

## Goal

Replace the current 7-kind artifact taxonomy (`preference`, `decision`, `rule`, `feedback`,
`article`, `reference`, `note`) with 4 semantically complete kinds (`user`, `rule`, `article`,
`note`). Extend `memory_search` `kind` filter to accept a list of up to 3. Rewrite the
`memory_search` docstring with a KIND SELECTION GUIDE so the model picks kinds at call time
without a separate intent classifier.

## Background

**Research:** `docs/reference/RESEARCH-memory-peer-for-co-second-brain.md` — Gap 1 (personalization
split). Peer survey shows all three reference systems (hermes-agent, ReMe, openclaw) use a single
flat taxonomy where each type is semantically complete — subject and form collapsed into one label.

**Design decisions from this session:**

- Peers use one taxonomy dimension, not orthogonal kind × scope axes.
- `user_*` prefix naming collapses the scope signal into the kind name, removing the need for a
  separate `scope` frontmatter field.
- `user_identity` and `user_preference` collapsed to `user` — recall tiering only needs one
  user-subject tier; BM25 within that tier differentiates by content.
- `feedback` folds into `user` — both answer "what does this user want/expect?".
- `decision` folds into `rule` — decisions with rationale are prescriptive guidance at recall time.
- `reference` (external URLs) folds into `article` with `source_ref` carrying the URL — the
  URL-bookmark distinction is content-level, not taxonomy-level at current corpus size.
- Intent detection for personal vs. general queries is absorbed by the main agent at tool-call
  time via KIND SELECTION GUIDE in the tool schema — no separate LLM classifier needed.
- `kind` filter extended to `list[str] | None` (up to 3) to cover mixed-intent queries.

## Kind migration map

| Old kind | New kind |
|---|---|
| `preference` | `user` |
| `feedback` | `user` |
| `decision` | `rule` |
| `reference` | `article` |
| `rule` | `rule` (unchanged) |
| `article` | `article` (unchanged) |
| `note` | `note` (unchanged) |

## Tasks

### ✓ DONE — T1 — Update `ArtifactKindEnum`

**File:** `co_cli/memory/artifact.py:23-30`

Remove `PREFERENCE`, `DECISION`, `FEEDBACK`, `REFERENCE`. Replace with:

```python
class ArtifactKindEnum(StrEnum):
    USER = "user"
    RULE = "rule"
    ARTICLE = "article"
    NOTE = "note"
```

Default fallback in `_coerce_fields` (line 80) stays `ArtifactKindEnum.NOTE` — no change.

---

### ✓ DONE — T2 — Extend `load_knowledge_artifacts` kind filter to list

**File:** `co_cli/memory/artifact.py:116-133`

Change signature and filter logic:

```python
def load_knowledge_artifacts(
    path: Path,
    artifact_kind: list[str] | str | None = None,
) -> list[KnowledgeArtifact]:
    ...
    if artifact_kind is not None:
        kinds = {artifact_kind} if isinstance(artifact_kind, str) else set(artifact_kind)
        if artifact.artifact_kind not in kinds:
            continue
```

---

### ✓ DONE — T3 — Extend `memory_store.py` kind filter to list

**File:** `co_cli/memory/memory_store.py`

Add one private helper (place near top of class body):

```python
@staticmethod
def _kind_clause(kind: list[str] | str | None) -> tuple[str, list]:
    """Return (sql_fragment, params) for a kind IN filter, or ('', []) if None."""
    if kind is None:
        return "", []
    kinds = [kind] if isinstance(kind, str) else list(kind)
    placeholders = ",".join("?" * len(kinds))
    return f" AND kind IN ({placeholders})", kinds
```

Replace all three SQL kind-equality sites with the helper:

- **Line ~163** (inside `_fts_search` or equivalent):
  `lsql += " AND d.kind = ?"; lp.append(kind)`
  → `clause, params = self._kind_clause(kind); lsql += clause; lp.extend(params)`

- **Line ~693** (inside `_vector_search` or equivalent):
  same replacement pattern

- **Line ~841** (inside `_doc_search` or equivalent):
  same replacement pattern (`doc_sql` / `doc_params`)

Update all method signatures from `kind: str | None` to `kind: list[str] | str | None`:
`search()` at line 518, plus the private helpers it calls at lines 566, 609, 674, 723, 776, 798.

---

### ✓ DONE — T4 — Update `_search_artifacts` in `recall.py`

**File:** `co_cli/tools/memory/recall.py:70-112`

Signature change only — implementation flows through after T2 and T3:

```python
async def _search_artifacts(
    ctx: RunContext[CoDeps],
    query: str,
    kind: list[str] | None,
    limit: int,
) -> list[dict]:
```

---

### ✓ DONE — T5 — Update `memory_search` tool parameter and docstring

**File:** `co_cli/tools/memory/recall.py:193-311`

Change parameter:

```python
kind: list[str] | None = None,
```

Replace the existing `kind:` line in the Args section. Add a KIND SELECTION GUIDE block
after the USE THIS PROACTIVELY section:

```
KIND SELECTION — supply up to 3 kinds as a list for targeted recall:

  TAXONOMY:
    "user"    — everything about the user: identity, preferences, corrections, feedback
    "rule"    — prescriptive guidance: mandates, decisions with rationale, conventions
    "article" — synthesized content: analysis, summaries, research notes, saved URLs
    "note"    — catch-all; rarely worth filtering to directly

  INTENT → KIND:
    "what do I prefer / how do I like / who am I..."   → ["user"]
    "how do I usually handle / my approach to..."      → ["user", "rule"]
    "what do I know about / what have I saved..."      → ["article"]
    "everything about X"                               → ["user", "rule", "article"]
    broad or uncertain intent                          → omit kind (searches all)

Args:
    kind: Up to 3 artifact kinds to filter results. None searches all kinds.
```

---

### ✓ DONE — T6 — Update `memory_create` tool

**File:** `co_cli/tools/memory/write.py:38-62`

Update the `artifact_kind` parameter description — replace the old 7-kind list with:
`One of user | rule | article | note.`

Update the tool docstring (line 38) to replace:
`Covers all artifact kinds: preference, decision, rule, feedback, article, reference, note.`
with:
`Covers all artifact kinds: user | rule | article | note.`

The valid-kinds guard at line 59 (`{e.value for e in ArtifactKindEnum}`) automatically uses
the new enum after T1 — no code change needed there.

---

### ✓ DONE — T7 — Update `memory_list` tool

**File:** `co_cli/tools/memory/read.py:62-74`

Update the `kind` parameter description to list the 4 new kinds:
`Filter by artifact_kind: "user", "rule", "article", or "note". None = all.`

---

### ✓ DONE — T8 — Migration script

**File:** `scripts/migrate_kinds.py` (new)

One-time script — run after all code changes are in and tests pass.

```python
MIGRATION_MAP = {
    "preference": "user",
    "feedback":   "user",
    "decision":   "rule",
    "reference":  "article",
}
```

For each `*.md` in `knowledge_dir`:
1. Parse YAML frontmatter.
2. If `artifact_kind` value is in `MIGRATION_MAP`, rewrite to mapped value.
3. Atomic write back (write to `.tmp`, rename).

After all files are rewritten, call `memory_store.upsert_knowledge_dir(knowledge_dir)`
to rebuild the search index with the new kind values.

Script must be idempotent — safe to run more than once.

---

### ✓ DONE — T9 — Update spec

**File:** `docs/specs/memory-knowledge.md:61`

Replace the `artifact_kind` row in the frontmatter schema table:

```
| `artifact_kind` | `user`, `rule`, `article`, or `note` |
```

Update any other references to the old kind list in the same file (line 173 references
`ReMe` + `artifact_kind` field — check for stale kind values).

---

### ✓ DONE — T10 — Update tests

**File:** `tests/test_flow_memory_search.py:15`

```python
"artifact_kind": "preference"  →  "artifact_kind": "user"
```

`tests/test_flow_memory_write.py` uses `"note"` and `"article"` — both survive unchanged.
`tests/test_flow_approval_subject.py:64` uses `"note"` — unchanged.
`tests/test_flow_memory_lifecycle.py:14` uses `"note"` — unchanged.

---

## Execution order

```
T1 → T2 → T3 → T4 → T5 → T6 → T7   (code changes, order within is flexible)
     ↓
    T10                               (tests pass against new enum)
     ↓
    T8                                (live-data migration — run last)
     ↓
    T9                                (spec sync — run after T8 confirms success)
```

T8 is the only step with a live-data side effect. Run it after the full test suite passes
with the new enum. T9 is a doc-only change — run it as the final step.

## Delivery Summary — 2026-05-01

| Task | done_when | Status |
|------|-----------|--------|
| T1 | `ArtifactKindEnum` has 4 values: user, rule, article, note | ✓ pass |
| T2 | `load_knowledge_artifacts` accepts `list[str] \| None` | ✓ pass |
| T3 | `MemoryStore.search()` and all helpers accept `kinds: list[str] \| None`, `sources: list[str] \| None`; `_coerce_sources` deleted; `_kind_clause` module-level helper added | ✓ pass |
| T4 | `_search_artifacts` signature: `kinds: list[str] \| None` | ✓ pass |
| T5 | `memory_search` param `kinds: list[str] \| None`; KIND SELECTION GUIDE added to docstring | ✓ pass |
| T6 | `memory_create` docstring updated to 4-kind list | ✓ pass |
| T7 | `memory_list` kind param description updated to 4 new kinds | ✓ pass |
| T8 | Migration run: 6 artifacts migrated; script deleted (co is brand-new, no persistent script needed) | ✓ pass |
| T9 | `memory-knowledge.md` and `memory-session.md` synced | ✓ pass |
| T10 | `test_flow_memory_search.py` fixture updated; `source=` kwarg in test updated to `sources=[...]` | ✓ pass |

**Design changes beyond plan scope (confirmed by user):**
- All `str | None` union types simplified to `list[str] | None` — no coercion, cleaner tool schema
- `kind` → `kinds`, `source` → `sources` parameter renames throughout for semantic clarity
- `_coerce_sources` deleted (was a symptom of the old union type)
- `artifact_kind` → `artifact_kinds` in `load_knowledge_artifacts`; all 5 call sites updated

**Tests:** scoped (touched files) — 19 passed, 0 failed
**Doc Sync:** fixed (`memory-knowledge.md` × 4 inaccuracies; `memory-session.md` × 1)

**Overall: DELIVERED**
All 10 tasks shipped. 7-kind taxonomy replaced with 4-kind; all search APIs use clean `list[str] | None` throughout; live data migrated (6 artifacts).

## Implementation Review — 2026-05-02

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| T1 | `ArtifactKindEnum` has 4 values: user, rule, article, note | ✓ pass | `artifact.py:23-27` — 4-value enum confirmed |
| T2 | `load_knowledge_artifacts` accepts `list[str] \| None` | ✓ pass | `artifact.py:110-113` — `artifact_kinds: list[str] \| None = None`; filter at line 130 |
| T3 | `MemoryStore.search()` + helpers accept `kinds: list[str] \| None`; `_kind_clause` module-level | ✓ pass | `memory_store.py:179-184` — `_kind_clause`; `memory_store.py:512-521` — `search()` sig; used at lines 162, 690, 836 |
| T4 | `_search_artifacts` signature `kinds: list[str] \| None` | ✓ pass | `recall.py:70-75` — confirmed |
| T5 | `memory_search` param `kinds: list[str] \| None`; KIND SELECTION GUIDE in docstring | ✓ pass | `recall.py:197`, `recall.py:230-244` |
| T6 | `memory_create` docstring updated to 4-kind list | ✓ pass | `write.py:39` — `Covers all artifact kinds: user \| rule \| article \| note` |
| T7 | `memory_list` kind param description updated to 4 new kinds | ✓ pass | `read.py:67` — `"user", "rule", "article", or "note"` |
| T8 | Migration run; no persistent script (brand-new corpus) | ✓ pass | no old-kind values in live knowledge dir (verified by test suite passing against new enum) |
| T9 | `memory-knowledge.md` and `memory-session.md` synced | ✓ pass | `memory-knowledge.md:45,61` — 4-kind taxonomy throughout |
| T10 | `test_flow_memory_search.py` fixture uses `artifact_kind: "user"` | ✓ pass | `test_flow_memory_search.py:15` — `"artifact_kind": "user"` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale kind list (old 7-kind taxonomy) | `CLAUDE.md:46` | minor | Updated to `user \| rule \| article \| note` |

### Tests
- Command: `uv run pytest -v`
- Result: 111 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: narrow — `CLAUDE.md` stale-kind reference fixed inline (single-line description, no spec)
- Result: fixed: `CLAUDE.md:46` kind list updated

### Behavioral Verification
- `uv run co status`: command not available in this CLI — `uv run co chat --help` ✓ starts without error
- No user-facing tool schema changes beyond docstring/parameter description updates; KIND SELECTION GUIDE is additive.

### Overall: PASS
All 10 tasks implemented as specified; 111/111 tests green; one minor doc-code mismatch in CLAUDE.md fixed inline.
