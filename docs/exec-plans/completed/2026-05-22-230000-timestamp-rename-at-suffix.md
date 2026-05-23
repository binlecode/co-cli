# timestamp-rename-at-suffix

## Problem

Co's timestamp field naming is inconsistent across stores:

| Surface | Current name | Style |
|---|---|---|
| `MemoryItem` dataclass (`co_cli/memory/item.py:67-74`) | `created`, `updated`, `last_recalled` | **bare** |
| Memory YAML frontmatter (writer `frontmatter.py:64,69,73`; reader `item.py:87,94`) | `created:`, `updated:`, `last_recalled:` | **bare** |
| Session YAML frontmatter (`session/store.py:78-79` via IndexStore upsert) | `created:`, `updated:` | **bare** |
| `IndexStore` SQLite columns + Python kwargs (`index/store.py:84,85,99,100,226,227,...`) | `created`, `updated` | **bare** |
| Session Python locals + dataclass (`session/store.py:54`, `session/browser.py:29`, `session/filename.py:19`) | `created_at: datetime` | **`_at`** |
| Skill usage sidecar JSON (`skills/usage.py:21,80`) | `"created_at"` | **`_at`** |

The newer JSON sidecars and Python timestamp variables adopted the `_at` suffix. The older memory and session YAML schemas — and the IndexStore that backs them — kept the bare form. Result: `item.created` exists alongside `record["created_at"]` in the same codebase.

This blocks any plan that operates on memory timestamps from being self-explanatory: `2026-05-20-010811-plan2a-dream-housekeeping.md` reads `item.created` / `item.last_recalled` / `decay_after_days` from `item.created` and forces every reader to remember that memory's schema is the odd one out.

## Dependencies

None. **This plan must ship before:**
- `2026-05-20-010811-plan2a-dream-housekeeping.md` — uses `item.created`, `item.last_recalled` in decay logic.
- `2026-05-22-104835-plan2b-skill-lifecycle-absorption.md` — likely touches same memory fields.

Both should be updated to use the renamed fields once this lands.

## Status

Delivered. Review-impl: PASS. Awaiting Gate 2 (`/ship`).

## Goals

1. **Normalize all persisted-timestamp identifiers on `_at` suffix.** No aliases, no readers for the old form (zero-backward-compat).
2. Rename across these surfaces atomically in one PR:
   - `MemoryItem` Python attrs
   - Memory YAML frontmatter keys
   - Session YAML frontmatter keys
   - `IndexStore` SQLite columns AND its Python kwargs/signatures
   - All call-sites: `co_cli/memory/`, `co_cli/session/`, `co_cli/index/`, `co_cli/tools/`, `co_cli/commands/`, `co_cli/bootstrap/`, `co_cli/observability/`
3. Update specs (`docs/specs/memory.md`, `docs/specs/sessions.md`) to reflect renamed keys.
4. **One-time manual data migration ops** (documented in plan, NOT shipped as production code per `feedback_no_migration_code`):
   - `~/.co-cli/memory/*.md` — sed YAML frontmatter keys
   - `~/.co-cli/sessions/*.md` — sed YAML frontmatter keys
   - `~/.co-cli/co-cli-search.db` — drop & rebuild via existing reindex path (IndexStore schema bump)

## Non-goals

- **No backwards-compat reader.** Per `feedback_zero_backward_compat`: old key is gone, code that sees it errors out. User runs the documented one-off migration to upgrade their store.
- **No rename of Python local variables that already used `_at`** (e.g. `created_at = _parse_iso8601(item.created)` in `decay.py` — local var becomes `created_at = _parse_iso8601(item.created_at)` naturally, no extra work).
- **No rename of `recall_count`, `recall_days`, `decay_protected`, `decay_after_days`.** Those aren't timestamp fields. Out of scope.
- **No rename of session filename timestamps** (`session_filename`'s `created_at: datetime` is already correct).
- **No new index store schema migration framework.** The DB rebuild is the manual op; existing reindex code stays.

## Design

### Rename table

| Old | New | Surface |
|---|---|---|
| `MemoryItem.created` | `MemoryItem.created_at` | Python attr |
| `MemoryItem.updated` | `MemoryItem.updated_at` | Python attr |
| `MemoryItem.last_recalled` | `MemoryItem.last_recalled_at` | Python attr |
| YAML key `created:` | `created_at:` | memory + session frontmatter |
| YAML key `updated:` | `updated_at:` | memory + session frontmatter |
| YAML key `last_recalled:` | `last_recalled_at:` | memory frontmatter |
| SQLite column `documents.created` | `documents.created_at` | IndexStore schema |
| SQLite column `documents.updated` | `documents.updated_at` | IndexStore schema |
| Kwarg `created=...` in `IndexStore.upsert/upsert_skill/upsert_canon` | `created_at=...` | IndexStore Python |
| Kwarg `updated=...` (same callers) | `updated_at=...` | IndexStore Python |
| Param `created_after`, `created_before` in IndexStore search | unchanged | already `_at`-ish; leave alone |

### IndexStore schema bump

The SQLite column rename means existing user DBs are incompatible. Options:

- **Schema version bump + drop-and-rebuild on mismatch.** IndexStore likely already has a schema_version (verify in T2). On version mismatch, drop the DB file and the existing reindex path repopulates it on first use. No migration code.
- **Pure manual op.** User runs `rm ~/.co-cli/co-cli-search.db && co … (something that triggers reindex)`. Simpler, no version-check code.

Pick at T2 implementation time. Prefer the manual-op route if no schema_version field exists; don't add one for this rename.

### Data migration ops (one-off, documented, not shipped as code)

```bash
# Memory frontmatter
find ~/.co-cli/memory -name '*.md' -exec sed -i '' \
  -e 's/^created: /created_at: /' \
  -e 's/^updated: /updated_at: /' \
  -e 's/^last_recalled: /last_recalled_at: /' {} \;

# Session frontmatter
find ~/.co-cli/sessions -name '*.md' -exec sed -i '' \
  -e 's/^created: /created_at: /' \
  -e 's/^updated: /updated_at: /' {} \;

# Search index — drop & let next run rebuild
rm ~/.co-cli/co-cli-search.db
```

This block goes into the plan's delivery notes and the CHANGELOG entry — NOT into any Python module.

## Tasks

- [x] ✓ DONE **T1.** `MemoryItem` rename (`co_cli/memory/item.py`):
  - Rename dataclass attrs `created` → `created_at`, `updated` → `updated_at`, `last_recalled` → `last_recalled_at`
  - Update `from_frontmatter` reader keys (`frontmatter["created"]` → `frontmatter["created_at"]`, etc.)
  - Update `format_memory_summary` (line 158) — `m.created[:10]` → `m.created_at[:10]`
  - Update required-field check at line 111 — `"created" not in frontmatter` → `"created_at" not in frontmatter`
  - `files:` `co_cli/memory/item.py`
  - `done_when:` `grep -n "\bcreated\b\|\bupdated\b\|\blast_recalled\b" co_cli/memory/item.py` returns only renamed identifiers (no bare form)

- [x] ✓ DONE **T2.** Memory frontmatter writer (`co_cli/memory/frontmatter.py`):
  - Rename YAML keys in `memory_item_to_frontmatter` (lines 64, 69, 73)
  - `files:` `co_cli/memory/frontmatter.py`
  - `done_when:` `grep -E '"(created|updated|last_recalled)"' co_cli/memory/frontmatter.py` returns nothing

- [x] ✓ DONE **T3.** Memory decay logic (`co_cli/memory/decay.py`):
  - Update all `item.created` → `item.created_at`, `item.last_recalled` → `item.last_recalled_at`
  - Keep local var name `created_at` as-is (now matches attr; one less translation)
  - `files:` `co_cli/memory/decay.py`
  - `done_when:` `grep -n "item\.created\b\|item\.last_recalled\b" co_cli/memory/decay.py` returns nothing

- [x] ✓ DONE **T4.** Memory store + service (`co_cli/memory/store.py`, `co_cli/memory/service.py`):
  - Update any `.created` / `.updated` / `.last_recalled` attr access and any frontmatter dict reads/writes
  - `files:` `co_cli/memory/store.py`, `co_cli/memory/service.py`
  - `done_when:` grep returns nothing in those files

- [x] ✓ DONE **T5.** IndexStore schema + Python API (`co_cli/index/store.py`, `co_cli/index/_retrieval.py`):
  - Rename SQLite columns `documents.created` → `documents.created_at`, `documents.updated` → `documents.updated_at` in CREATE TABLE
  - Rename SQLite column `embedding_cache.created` → `embedding_cache.created_at` in CREATE TABLE
  - Update ON CONFLICT clauses and SELECT lists (lines 235, 240–253, 460, 466)
  - Rename `upsert` / `upsert_skill` / `upsert_canon` kwargs `created=` → `created_at=`, `updated=` → `updated_at=`
  - Update internal `inventory` query ORDER BY (`ORDER BY d.created DESC` → `ORDER BY d.created_at DESC`)
  - Rename `SearchResult` dataclass fields `created` → `created_at`, `updated` → `updated_at` (lines 55–56 in `_retrieval.py`); update all SQL row unpackings and consumers in the same file
  - **Schema invalidation:** if no version field exists, document `rm ~/.co-cli/co-cli-search.db` as the manual op (covered in delivery notes); if a version field exists, bump it
  - `files:` `co_cli/index/store.py`, `co_cli/index/_retrieval.py`
  - `done_when:` `grep -E "\bcreated\b|\bupdated\b" co_cli/index/store.py co_cli/index/_retrieval.py | grep -v "created_at\|updated_at\|created_after\|created_before\|updated_after\|updated_before"` returns nothing

- [x] ✓ DONE **T6.** Session store call-site (`co_cli/session/store.py`):
  - Update kwargs at lines 78–79: `created=` → `created_at=`, `updated=` → `updated_at=`
  - `files:` `co_cli/session/store.py`
  - `done_when:` grep against bare forms returns nothing in that file

- [x] ✓ DONE **T7.** Tools + commands + bootstrap + observability:
  - `co_cli/tools/memory/recall.py`, `co_cli/tools/session/recall.py`, `co_cli/commands/memory.py`, `co_cli/bootstrap/core.py`, `co_cli/observability/file_logging.py`
  - Update all `.created` / `.updated` / `.last_recalled` attribute reads referring to memory/session items
  - `co_cli/tools/session/recall.py:93` (`r.created[:10]`) — `r` is a `SearchResult`; this rename follows T5's `SearchResult` field rename, so T5 must land first
  - **`co_cli/observability/file_logging.py:66` — no change needed.** `record.created` is Python stdlib `logging.LogRecord.created`; not a project field.
  - `files:` (5 files above)
  - `done_when:` `grep -En "\.created\b|\.updated\b|\.last_recalled\b" co_cli/tools/memory/recall.py co_cli/tools/session/recall.py co_cli/commands/memory.py co_cli/bootstrap/core.py | grep -v "_at\b"` returns nothing

- [x] ✓ DONE **T8.** Tests + evals updated:
  - `tests/test_flow_memory_write.py`, `tests/tools/memory/test_recall_metrics.py`
  - Plus any test that constructs `MemoryItem(...)` with kwargs or writes frontmatter inline
  - **Evals:** `evals/eval_memory.py:143`, `evals/eval_daily_chat.py:141,184,649`, `evals/eval_trust_visibility.py:212` — all use bare `"created"` dict key; rename to `"created_at"`
  - `files:` the two known test files + any flagged by grep + the three eval files above
  - `done_when:` `uv run pytest tests/memory tests/tools/memory tests/test_flow_memory_write.py -x` green; `grep -rn '"created"\|"updated"\|"last_recalled"' evals/` returns nothing

- [x] ✓ DONE **T9.** Spec sync:
  - `docs/specs/memory.md` — frontmatter schema section
  - `docs/specs/sessions.md` — frontmatter schema section
  - `docs/specs/dream.md:203` — spec table documents `last_recalled: str | None`; rename to `last_recalled_at`
  - `files:` three spec files above
  - `done_when:` grep against `created:` / `updated:` / `last_recalled:` in those docs returns only `created_at:` / `updated_at:` / `last_recalled_at:`

- [x] ✓ DONE **T10.** Plan-2a + plan-2b update (defer to those plans' own delivery, NOT this one):
  - Add a one-line note to each plan's Dependencies section: "Depends on `2026-05-22-230000-timestamp-rename-at-suffix.md` (renames `item.created` → `item.created_at` etc.)."
  - Leave their task bodies untouched; they'll naturally use the new names when they ship.
  - `files:` `docs/exec-plans/active/2026-05-20-010811-plan2a-dream-housekeeping.md`, `docs/exec-plans/active/2026-05-22-104835-plan2b-skill-lifecycle-absorption.md`
  - `done_when:` both files contain the dependency note

- [x] ✓ DONE **T11.** `/sync-doc` — run after all code tasks complete to catch any remaining doc-only inaccuracies across specs.

- [x] ✓ DONE **T12.** CHANGELOG entry + delivery notes:
  - Add the manual migration block (sed commands + `rm ~/.co-cli/co-cli-search.db`) to CHANGELOG for this version
  - `files:` `CHANGELOG.md`
  - `done_when:` CHANGELOG entry exists with the three sed lines + the rm line

## Test plan

| Test | Scope | Type |
|---|---|---|
| `MemoryItem` round-trip | Write item → read back; new keys preserved, no bare forms emitted | Unit |
| Frontmatter required-field check | Missing `created_at:` raises (old `created:` no longer accepted) | Unit |
| Decay candidacy | `find_decay_candidates` honors `created_at` + `last_recalled_at` correctly | Unit |
| IndexStore upsert + query | `upsert(created_at=..., updated_at=...)` writes; inventory ORDER BY `created_at DESC` works | Unit |
| Session store integration | New session writes `created_at:` / `updated_at:` to frontmatter | Integration |
| Specs grep | No bare `created:` / `updated:` / `last_recalled:` in memory/session specs | Static check |
| Full test suite | `scripts/quality-gate.sh full` green | Suite |

## Risks

- **Existing user data on disk breaks silently if migration skipped.** Mitigation: CHANGELOG migration block; required-field check at frontmatter read raises a clear "missing required field 'created_at'" pointing to migration.
- **IndexStore schema mismatch crashes on first query.** Mitigation: documented `rm` op; alternatively, T5 implementer can wrap the schema bootstrap in a try/except that detects the old column and prints "drop and rerun" — but per `feedback_no_migration_code`, prefer crash + clear error + CHANGELOG over auto-repair.
- **Tests outside the known set may reference bare frontmatter strings inline.** Mitigation: after T1–T8, run full `uv run pytest -x` once; fix any straggling tests (incremental cost in T8).
- **Concurrent in-flight plans (2a, 2b) reference old field names.** Mitigation: T10 adds the dependency note to both plans; they ship with renamed identifiers naturally since neither has started implementation.
- **Search DB drop loses session-search index until next reindex.** Acceptable — reindex is fast and CHANGELOG warns the user.

## Implementation Footprint Summary

**Modified:**
- `co_cli/memory/item.py` — dataclass attrs + frontmatter reader
- `co_cli/memory/frontmatter.py` — writer keys
- `co_cli/memory/decay.py` — attr access
- `co_cli/memory/store.py`, `co_cli/memory/service.py` — attr access
- `co_cli/index/store.py` — SQLite columns (`documents` + `embedding_cache`) + Python kwargs
- `co_cli/index/_retrieval.py` — `SearchResult` dataclass fields + query references
- `co_cli/session/store.py` — IndexStore upsert kwargs
- `co_cli/tools/memory/recall.py`, `co_cli/tools/session/recall.py` — attr access
- `co_cli/commands/memory.py` — display
- `co_cli/bootstrap/core.py`, `co_cli/observability/file_logging.py` — attr access
- `docs/specs/memory.md`, `docs/specs/sessions.md`, `docs/specs/dream.md` — schema sections
- `CHANGELOG.md` — migration block

**Added:** Nothing (pure rename).

**Deleted:** Nothing in code. **Manual ops (not code):** documented sed commands + `rm co-cli-search.db`.

**Plans to update with new dependency note:** `2026-05-20-010811-plan2a-dream-housekeeping.md`, `2026-05-22-104835-plan2b-skill-lifecycle-absorption.md`.

## Delivery Summary — 2026-05-22

| Task | done_when | Status |
|------|-----------|--------|
| T1 | grep bare forms in item.py returns nothing | ✓ pass |
| T2 | grep bare keys in frontmatter.py returns nothing | ✓ pass |
| T3 | grep item.created/item.last_recalled in decay.py returns nothing | ✓ pass |
| T4 | grep bare forms in store.py/service.py returns nothing | ✓ pass |
| T5 | grep bare created/updated in index/store.py + _retrieval.py returns nothing | ✓ pass |
| T6 | grep bare forms in session/store.py returns nothing | ✓ pass |
| T7 | grep .created/.updated/.last_recalled in T7 files returns nothing | ✓ pass |
| T8 | scoped pytest green; grep "created" in evals returns nothing | ✓ pass |
| T9 | grep bare timestamp keys in 3 spec files returns nothing | ✓ pass |
| T10 | both plans contain dependency note | ✓ pass |
| T11 | sync-doc run — dream.md + memory.md + 01-system.md fixed | ✓ pass |
| T12 | CHANGELOG entry with migration block present | ✓ pass |

**Extra file touched:** `co_cli/index/_embedding.py` — embedding cache `INSERT` SQL used `created` column name; renamed to `created_at` to match the schema DDL. Not listed in T5's `files:` but required by T5's schema change.

**Tests:** scoped — 63 passed, 0 failed

**Doc Sync:** fixed (`dream.md` — 4 timestamp refs; `memory.md` — 2 stale file paths; `01-system.md` — 2 missing component entries)

**Overall: DELIVERED**
All 12 tasks passed done_when. Lint clean. Scoped tests green. Schema renamed atomically across docs + embedding_cache + retrieval + session + memory. Migration block in CHANGELOG.

## Implementation Review — 2026-05-22

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| T1 | `MemoryItem` dataclass: all three fields renamed | ✓ pass | `item.py:67-74` — `created_at`, `updated_at`, `last_recalled_at` present; call path through `_coerce_fields`, `load_memory_item`, `filter_memory_items`, `format_memory_item_row` confirmed |
| T2 | `memory_item_to_frontmatter` writes renamed keys | ✓ pass | `frontmatter.py:64,69,73` — all three `_at` keys written |
| T3 | `find_decay_candidates` uses renamed attrs | ✓ pass | `decay.py:42,46,53` — `item.created_at`, `item.last_recalled_at`, `art.created_at` |
| T4 | `MemoryStore` and `service.py` use renamed fields | ✓ pass | `store.py:101-102,140`, `service.py` — all `created_at=`, `updated_at=` kwargs |
| T5 | Schema DDL + all SQL paths renamed | ✓ pass | `schema.py:9,10,23` — DDL columns confirmed; `store.py` INSERT/ON CONFLICT; `_retrieval.py` FTS/LIKE/vec paths; `_embedding.py:73` INSERT SQL |
| T6 | `session/store.py` upsert renamed | ✓ pass | `session/store.py:78-79` — `created_at=`, `updated_at=` |
| T7 | Tool + command surface renamed | ✓ pass | `tools/memory/recall.py`, `tools/session/recall.py`, `commands/memory.py`, `bootstrap/core.py` — all `_at` attrs; `file_logging.py` stdlib `record.created` correctly untouched |
| T8 | Tests and evals updated | ✓ pass | `test_flow_memory_write.py:252,278`, `test_flow_memory_store.py:110`, `test_recall_metrics.py` (×5), all three eval files |
| T9 | `dream.md` spec updated | ✓ pass | `dream.md:197,206,373,600` — four `last_recalled_at` occurrences |
| T10 | Dependency notes in sibling plans | ✓ pass | Both plan files updated |
| T11 | `memory.md` file paths corrected; `01-system.md` component table | ✓ pass | `memory.md:140-141`, `01-system.md:61-62` |
| T12 | CHANGELOG migration block | ✓ pass | `[0.8.240]` section with sed commands + `rm co-cli-search.db` |
| GLOBAL | Zero bare refs remaining in `co_cli/` | ✓ pass | grep for `"created":`, `"updated":`, `"last_recalled":`, `.last_recalled`, bare `.created`/`.updated` — all zero |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale prose: "created (required identity)" | `frontmatter.py:59` | minor | Updated to "created_at (required identity)" |

### Tests

- Command: `uv run pytest -v`
- Result: 558 passed, 0 failed
- Log: `.pytest-logs/` (timestamped review-impl log)

### Behavioral Verification

No user-facing CLI surface changed (no commands added/removed, no output format changed). System integrity verified: all renamed modules import cleanly, `MemoryItem` field annotations and `SCHEMA_SQL` DDL confirmed correct via import-time assertions.

### Overall: PASS

All 12 tasks confirmed at file:line. Single minor prose fix auto-applied. Full suite 558 passed, lint clean, zero bare timestamp refs.

---

## Future scope (out of scope here)

- **Normalize `recall_days` / `decay_after_days` / `recall_protection_days`** if a similar inconsistency surfaces (they're durations, not timestamps — different naming axis).
- **Migration framework.** If a third rename like this comes up, consider a tiny `IndexStore` schema-version field + first-run rebuild check. Don't build it for this single rename.
