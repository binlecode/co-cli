# session-search-ripgrep

> **Status: DRAFT — pre-Gate-1.** Net-deletion simplification: session-transcript search moves from the
> hybrid (FTS5 + sqlite-vec) IndexStore to file-based ripgrep. Decision is settled
> (`feedback_session_search_ripgrep`); this plan is the implementation.

## Context

co indexes raw session transcripts into the shared hybrid `IndexStore` (FTS5 + sqlite-vec embeddings) —
the **same** machinery as curated memory + canon — and `session_search` runs BM25/vector recall over it.
This is the highest-volume, lowest-value-density corpus getting the most expensive indexing, and it is a
peer/principle outlier:

- **Peer survey** (`reference_peer_session_search`, source-verified): only co default-on vector-indexes raw
  transcripts. codex = pure ripgrep (`rollout/src/search.rs`), hermes = FTS5/BM25 keyword, openclaw = hybrid
  **shipped off** (cost warning in help text), opencode = none. Always-on transcript search in the field is
  lexical, never semantic.
- **co's own dichotomy** (`feedback_knowledge_search_dichotomy`): hybrid DB recall is for *curated* memory;
  *uncurated* content goes lexical/no-index. Raw transcripts are uncurated; the dream reviewer is the
  curation step that lifts durable value into the (still hybrid-indexed) memory tier. So transcripts belong
  on the lexical side — the current design violates the principle.

This plan moves session search to ripgrep-over-files and removes the session↔IndexStore coupling. Curated
memory + canon stay on the hybrid index, untouched. Compaction's in-place transcript rewrite stays
unchanged (`feedback_session_search_ripgrep`: append would add complexity to recover low-value residue).

### Verified code sites

- `co_cli/session/store.py` — `SessionStore`: the **only** `SESSION_SOURCE` holder. `index_session`
  (`:44`), `sync` (`:83`), `search` (`:115` → `index.search(sources=[SESSION_SOURCE])`), `count` (`:118`
  → `index.count_docs`). Constructed with `index=index_store` in `bootstrap/core.py:404`.
- `co_cli/session/chunker.py` — `chunk_session`; **only caller is `store.py:56`** (the index path). Imports
  `extract_messages` from `transcript.py`.
- `co_cli/session/transcript.py` — `extract_messages(path) → list[ExtractedMessage]` (line_index, role,
  content, timestamp, tool_name). **Also used by `tools/session/view.py:74`** — must stay.
- `co_cli/tools/session/recall.py` — `session_search` tool. `_search_sessions` (`:51`) consumes
  `store.search()` results via attributes `.path .snippet .start_line .end_line .created_at .source .score`
  and emits chunk-cited dicts `{session_id, when, source, chunk_text, start_line, end_line, score}`. Browse
  mode (`_browse_recent`) uses `session/browser.py:list_sessions` — index-independent, unaffected.
- `co_cli/tools/session/view.py` — verbatim line-range reads via `extract_messages`. Unaffected.
- `co_cli/bootstrap/core.py:492` `init_session_index` → `session_store.sync(...)`, called once at
  `co_cli/main.py:598`. `session_store.count()` consumed at `main.py:612` (welcome banner only).
- `co_cli/index/_retrieval.py:46` `SearchResult` — the shape `recall.py` currently depends on.
- `co_cli/tools/files/read.py:143-237` — existing `rg` invocation precedent (`_glob_ripgrep`,
  `_build_grep_shell_command`, `_parse_grep_content_output`) to mirror for subprocess + arg hygiene;
  `read.py:70,342,376` `_has_command("rg")` → `_grep_python` is the rg-absent fallback precedent.
- **Orphans this change creates (G1 sweep):**
  - `co_cli/config/memory.py:33-34,63-64` — `session_chunk_tokens` / `session_chunk_overlap` fields +
    `CO_MEMORY_SESSION_CHUNK_*` env map. Sole consumers are `store.py:41-42` (removed in the rewrite) →
    become dead; remove them (orphan the change creates, not pre-existing dead code).
  - `evals/eval_memory.py:192-193` — `deps.session_store.index_session(path)` seeds the staging-deploy-id
    recall scenario; `index_session` is removed → **AttributeError at eval runtime**. The JSONL is already
    written to `sessions_dir` at `:191` (all ripgrep needs), so the two indexing lines just get deleted.
  - `co_cli/tools/session/recall.py:1,58,141` — agent-facing docstrings say "FTS5/BM25 chunk-cited"; logic
    is unchanged but the prose misdescribes the lexical backend to the model (`/sync-doc` won't touch source).
  - `co_cli/session/store.py:1-10` — module docstring describes the chunk pipeline / hash-skip / append-only
    sync; all wrong post-rewrite.
- **Confirmed NOT dead (leave alone):** `IndexStore.needs_reindex` / `remove_stale` / `count_docs` are each
  still used by `memory/store.py` + canon (`bootstrap/core.py`); session removal does not orphan them.
- Tests: `tests/test_flow_session_search.py` (rewrite), `test_flow_session_view.py` (must stay green),
  `test_flow_memory_canon_recall.py` (hybrid untouched — must stay green).

### Current-state check

Source is internally consistent for this scope. `SESSION_SOURCE` is cleanly isolated to `store.py`;
removing it does not touch the `MEMORY_SOURCE` / canon paths in the shared `IndexStore`.

## Problem & Outcome

**Problem.** Session transcripts are indexed with the full hybrid FTS5+embedding pipeline by default —
expensive (embedding compute + vector storage) on the corpus least likely to repay it, redundant with the
dream-distilled memory tier, and contrary to co's curated-vs-uncurated search dichotomy.

**Scope-honesty note (PO-m-2):** "net deletion" is about removing the **coupling and pipeline** (chunker,
sync, reindex, transcript embeddings), not raw line count — a `_search.py` module (ripgrep + ranking +
`SessionHit` adapter + rg-absent fallback) is added. The win is architectural, not LOC.

**Compaction interaction (PO-m-1):** because in-place rewrite stays (out of scope) and ripgrep reads the
file, a compacted session is greppable only for its **post-compaction** content — pre-compaction verbatim
is gone from disk for both grep and the old index alike. Reasoned-acceptable (`feedback_session_search_ripgrep`:
the dream reviewer already distilled the durable value before compaction); stated here for the Gate-1 reader.

**Outcome.** `session_search` runs file-based ripgrep over `~/.co-cli/sessions/*.jsonl`, reusing
`extract_messages` to return readable, `session_id`+line-cited hits (same tool contract). `SessionStore`
drops its `IndexStore` dependency; the session chunker, content-hash reindex, `remove_stale`-for-sessions,
and per-transcript embeddings are removed, along with the startup `init_session_index` sync. Memory + canon
recall is unchanged.

**Failure cost:** none from *not* shipping — the current path works, it just over-spends. The cost is the
status quo: every transcript change re-chunks + re-embeds the largest corpus, startup pays a session-sync,
and the index carries vector rows for low-value text. The risk *of* shipping is a regression in
`session_search` result quality (semantic → lexical) — accepted per `feedback_session_search_ripgrep`, but
the eval/test must confirm lexical recall still surfaces a known phrase with usable citations.

## Scope

**In scope:** `co_cli/session/store.py` (rewrite to file-based, incl. its now-wrong module docstring), a new
private ripgrep search module under `co_cli/session/`, deletion of `co_cli/session/chunker.py`, removal of
`init_session_index` + its bootstrap wiring, removal of the orphaned `session_chunk_*` config fields
(`co_cli/config/memory.py`), the stale "FTS5/BM25" docstrings in `co_cli/tools/session/recall.py`, the dead
`index_session` seed call in `evals/eval_memory.py`, and the `session_search` recall test. The
`session_search`/`session_view` tool names and return shapes stay stable.

**Out of scope:**
- Compaction / in-place rewrite (unchanged — `feedback_session_search_ripgrep`).
- Memory + canon indexing (the hybrid `IndexStore` path stays exactly as-is).
- `docs/specs/` edits (sessions, memory, 01-system, bootstrap, config) — `/sync-doc` post-delivery (no
  `docs/specs/` in any `files:`); full stale-line list in Testing.
- The within-session compaction plan (`2026-06-02-210659-context-stability-sizing-control.md`).

## Behavioral Constraints

- **Curated-vs-uncurated dichotomy** (`feedback_knowledge_search_dichotomy`): this *applies* it — sessions
  lexical, memory/canon hybrid. Do not touch the memory/canon index path.
- **Zero backward-compat** (`feedback_zero_backward_compat`): remove the session-index path directly; no
  dual-mode/runtime-toggle shim between index and grep for sessions.
- **No migration code** (`feedback_no_migration_code`): session chunks already in the DB become orphaned;
  clear them with a one-off manual op (TASK-3), never a production lazy-migrate/cleanup-on-boot reader.
- **Tool contract stable**: `session_search`/`session_view` keep names, args, and result shape so the
  agent-facing surface and existing callers are unchanged.
- **Surgical**; Python 3.12; `__init__.py` docstring-only; `_prefix.py` visibility; no util modules.

## High-Level Design

**Decoupling shape: sessions leave `IndexStore` entirely.** Rejected alternative — a per-source backend
setting on `IndexStore` — keeps the coupling and the chunker alive for no benefit. Instead `SessionStore`
becomes a thin file-based store with **no `IndexStore` reference**; memory/canon keep using `IndexStore`
directly. The global `memory.search_backend` enum keeps governing memory/canon only; sessions ignore it.

**Search path.** A new private module `co_cli/session/_search.py` (leading-underscore, package-private):
1. `rg --fixed-strings --ignore-case` (mirroring `read.py`'s invocation + `--no-config` hygiene) over
   `sessions_dir/*.jsonl`, returning `(file, line_index)` matches; Python line-scan fallback if `rg` is
   absent (codex `scan_rollout_matches`). Matches the **raw** query as a case-insensitive substring of the
   JSON-encoded line. **Verified safe:** co writes transcripts via pydantic-core `dump_json`, which emits
   literal UTF-8 (no ASCII-escaping) — only `"` and `\` are escaped on disk, so unicode / accented / CJK
   queries match raw; only a query literally containing `"`/`\` could straddle an escape and miss (rare,
   accepted). (Codex's `json_escaped_search_term` was evaluated and rejected — it earns nothing here because
   co does not `\uXXXX`-escape; see Final.)
2. Group matches by session file; for each match, reuse `extract_messages(path)` and select the
   `ExtractedMessage` whose `line_index == matched_line` **and** whose `content` contains the *raw* query
   (case-insensitive) — its `content` becomes `chunk_text`, with `start_line = end_line = matched_line`.
   A JSONL line yields multiple parts sharing that `line_index`, so the query-content match disambiguates
   which part (if several match, take the **first** in `extract_messages` order — affects only displayed
   snippet text, not the citation line). **No content-match → skip the hit (CD-M-1, revised).** This is the
   co adaptation of codex's "conversation-text snippet, else `None`" (`search.rs:171-186,246`
   `conversation_text_from_item` extracts only message text and yields no snippet otherwise): if the rg line
   matched only on a structural JSON key (`part_kind`, `timestamp`, a `tool_name` value) or inside a
   non-extracted part (thinking / system-prompt), **no retained part's content contains the term → drop the
   match** rather than surfacing arbitrary tool-JSON as a snippet. **Divergence-by-design:** unlike codex
   (user/assistant text only), co keeps `tool-call`/`tool-return` parts eligible — `extract_messages`
   deliberately retains them, so a genuine match *inside* tool-return content still surfaces with a readable
   snippet. The skip rule kills structural-key noise without dropping co's intended tool-content recall.
3. Rank sessions by `(match_count desc, recency desc)`; `score` becomes the match count (synthetic).
   Cap at `_SESSIONS_CHANNEL_CAP` (current-session exclusion stays in `recall.py`). Return a small
   `SessionHit` dataclass exposing the attributes `recall.py` already reads — critically **`path` carries
   the uuid8** (parsed via `parse_session_filename`), NOT the filesystem path (CD-M-2): `recall.py:81,96`
   compares `r.path` to `current_uuid8` and emits it as `session_id`. Other fields: `snippet`
   (= chunk_text), `start_line`, `end_line`, `created_at` (ISO string from filename), `source` (= "session"),
   `score`. So `recall.py` needs no change.

**`SessionStore` after:** `search(query, limit)` delegates to `_search.py`; `count()` = number of
`*.jsonl` files in `sessions_dir`; `index_session`/`sync` removed; constructor takes `config` +
`sessions_dir`, no `index`.

**Bootstrap after:** drop `init_session_index` and its `main.py:598` call; construct `SessionStore`
without `index=`. `count()` still feeds the welcome banner.

**Deletion:** `chunker.py` (sole caller was the removed index path). `extract_messages`/`transcript.py`
stay (used by `view.py` + the new search).

## Tasks

### ✓ DONE TASK-1 — file-based session search module + `SessionStore` rewrite
**files:** `co_cli/session/_search.py` (NEW), `co_cli/session/store.py`,
`co_cli/tools/session/recall.py`, `tests/test_flow_session_search.py`
**Action:** Add `_search.py`: ripgrep the **raw** query (mirror `read.py` invocation + `--no-config`;
Python line-scan fallback when `rg` absent) over `sessions_dir/*.jsonl` → group by file → reuse
`extract_messages` for readable `chunk_text` + line citations (part-selection per High-Level Design
steps 1-2; **CD-M-1 = skip the hit when no retained part's content contains the query**, so
structural-JSON-key matches never surface) → rank `(match_count desc, recency desc)` → return `SessionHit`
objects whose **`path` is the uuid8**
(CD-M-2) plus `.snippet .start_line .end_line .created_at .source .score` (so `recall.py` **logic** is
unchanged). Rewrite `SessionStore`: `search()` delegates to `_search.py`; `count()` counts `*.jsonl`;
remove `index_session`, `sync`, `_sha256`, the `SearchResult`/`IndexStore` imports, and the now-wrong
module docstring (`store.py:1-10`); constructor drops `index=`. In `recall.py`, fix the agent-facing
docstrings (`:1` module, `:58` internal, `:141` `query` arg) from "FTS5/BM25 chunk-cited" to lexical
substring — **prose only, no logic change**.
**prerequisites:** none.
**done_when:** a unit/integration test writes two real session JSONL files (CO_HOME temp), calls
`SessionStore.search("<plain multi-word phrase in one>")` (exercises JSON-encoding passthrough, PO-m-4),
and asserts the hit's `path` is the correct **uuid8**, the `snippet` is readable (non-JSON-escaped) and
contains the phrase, and `start_line == end_line == matched_line`, with no `IndexStore` constructed.
**Refinement assertion:** a query that occurs only as a structural JSON key/value (e.g. `part_kind` or a
bare `tool_name`) returns **no hit** — proves the CD-M-1 skip rule kills structural-key noise.
`uv run pytest tests/test_flow_session_search.py -x` passes.
**success_signal:** `session_search "<phrase>"` in a real REPL returns the past session with a readable
cited snippet, no embedding/index involved.

### ✓ DONE TASK-2 — remove session-indexing wiring (chunker, bootstrap sync, orphaned config + eval seed)
**files:** `co_cli/session/chunker.py` (DELETE), `co_cli/bootstrap/core.py`, `co_cli/main.py`,
`co_cli/config/memory.py`, `evals/eval_memory.py`
**Action:** Delete `chunker.py`. Remove `init_session_index` (`bootstrap/core.py:492`) and its call
(`main.py:598`) and the `init_session_index` import (`main.py:26`). **CD-M-3:** the removed call consumed
`current_session_path = restore_session(...)` (`main.py:597`), which has no other reader — drop the
assignment to a bare `restore_session(...)` call (or remove if unused) so ruff does not flag an unused
binding. Construct `SessionStore` without `index=` (`bootstrap/core.py:404`). **CD-M-4 (orphaned config):**
remove `session_chunk_tokens` / `session_chunk_overlap` fields (`config/memory.py:63-64`) and their
`CO_MEMORY_SESSION_CHUNK_*` env-map entries (`:33-34`) — their only consumers were `store.py:41-42`, removed
in TASK-1. **CD-M-5 (dead eval seed):** in `evals/eval_memory.py` delete the `if deps.session_store is not
None: deps.session_store.index_session(path)` block (`:192-193`); the JSONL is already written to
`sessions_dir` at `:191`, which is all the file-based search needs. Confirm `SESSION_SOURCE`, `chunk_session`,
and `session_chunk_` have zero remaining references and the memory/canon `IndexStore` construction is untouched.
**prerequisites:** TASK-1.
**done_when:** `rg -n "SESSION_SOURCE|chunk_session|init_session_index|session_chunk_"` over `co_cli/` +
`evals/` returns nothing; `uv run co chat` boots without the session-sync step and the welcome banner still
shows a session count; `uv run pytest tests/test_flow_session_search.py tests/test_flow_session_view.py -x`
passes.
**success_signal:** startup no longer runs a session index sync; session count still displays.

### ✓ DONE TASK-3 — orphaned-chunk one-off cleanup + memory/canon regression guard
**files:** `tests/test_flow_memory_canon_recall.py` (verify only)
**Action:** Document (in the Delivery Summary, not production code) the one-off manual op to drop orphaned
`source='session'` rows from the existing `co-cli-search.db` (`feedback_no_migration_code` — no boot-time
cleanup reader). Verify memory + canon recall is fully unaffected by the session decoupling.
**prerequisites:** TASK-1, TASK-2.
**done_when:** `uv run pytest tests/test_flow_memory_canon_recall.py -x` passes unchanged (hybrid
memory/canon recall intact); the Delivery Summary records the manual orphan-cleanup command.
**success_signal:** N/A (regression guard).

### ✓ DONE TASK-4 — session_search recall test rewrite (functional contract)
**files:** `tests/test_flow_session_search.py`
**Action:** Rewrite the test to assert the ripgrep-backed contract end-to-end through the
`session_search` **tool** (not just the store): browse mode returns recent-session metadata; keyword mode
returns ≤3 unique sessions with readable cited snippets; current session excluded; no-match returns the
empty message. Drop any assertion tied to BM25/embedding internals. **CD-m:** remove the now-dead test
fixtures/helpers (`SessionStore(index=...)`, `index_session`, `IndexStore` imports) across the session
tests so they don't fail lint/import.
**prerequisites:** TASK-1, TASK-2.
**done_when:** `uv run pytest tests/test_flow_session_search.py -x` passes against the tool surface,
asserting a known phrase in a past session is recalled with `session_id` + line citation and the current
session is excluded.
**success_signal:** the agent-facing `session_search` behaves identically in shape to before, lexically.

## Testing
- Scoped: `tests/test_flow_session_search.py` (rewritten), `tests/test_flow_session_view.py` (unchanged,
  must stay green — `extract_messages` retained), `tests/test_flow_memory_canon_recall.py` (hybrid
  untouched, must stay green).
- `scripts/quality-gate.sh full` at ship.
- **Post-delivery (`/sync-doc`)** — five shipped specs carry stale session-index content (G1 sweep):
  - `docs/specs/sessions.md` — "FTS5/BM25 chunk-cited recall" → ripgrep lexical; chunk-pipeline rows
    (`:46,132-134`), chunk-config rows (`:47-48,83-84`), `init_session_index` rows (`:55,142,164`).
  - `docs/specs/memory.md` — session tier no longer on `IndexStore`.
  - `docs/specs/01-system.md:93,139` — boot-flow `→ init_session_index`; "chunked at write time / BM25".
  - `docs/specs/bootstrap.md:13,64,196,252,263` — `init_session_index` in the boot flow + "syncs into
    MemoryStore under source='session'".
  - `docs/specs/config.md:184-185` — the two `session_chunk_*` config rows (now-removed fields).

## Open Questions
- **Match semantics (v1 vs. tune).** v1: case-insensitive fixed-string substring of the full query. Open:
  multi-term as all-terms vs any-term, and a no-hit fallback to any-term. Recommend v1 substring; revisit
  if recall feels too narrow in use.
- **Ranking heuristic.** `(match_count desc, recency desc)` with `score=match_count`. Alternative:
  recency-only (codex sorts by metadata time). Recommend match_count+recency; cheap to change.
- **`rg` absence — RESOLVED.** `read.py` already gates `rg` via `_has_command` → `_grep_python` fallback
  (Core Dev verified). Mirror that precedent: keep the Python line-scan fallback. Not an open question.

## Final — Team Lead

Plan approved (PO `Blocking: none` at C1; Core Dev `Blocking: none` at C2 — all three CD blockers resolved
in-plan). This is a settled-decision simplification: session search → file ripgrep, memory/canon stay
hybrid, in-place compaction rewrite unchanged.

**G1 cleanup-completeness review (folded in):** a full reference sweep of every dropped symbol surfaced
five gaps now in scope — (1) **`evals/eval_memory.py:192-193`** would `AttributeError` on the removed
`index_session` (blocking; CD-M-5); (2) orphaned `session_chunk_*` config fields (CD-M-4); (3) stale
"FTS5/BM25" docstrings in `recall.py`; (4) the now-wrong `store.py` module docstring; (5) `/sync-doc`
under-scoped — three more specs (`01-system.md`, `bootstrap.md`, `config.md`) carry stale content beyond
sessions/memory. Confirmed NOT dead and correctly untouched: `IndexStore.needs_reindex/remove_stale/count_docs`
(shared with memory + canon). Source cleanup is now complete; `IndexStore` is not touched.

**Peer-impl alignment review (codex `rollout/src/search.rs`, source-read):** core mechanics already match
the lone rg peer — `--fixed-strings --ignore-case` over `*.jsonl`, two-phase locate→extract-readable-text,
native line-scan fallback when `rg` is absent, exit-code-1-as-no-match. Two candidate refinements were
evaluated; **one kept, one rejected after verification:**
- **KEPT — CD-M-1 revised to skip-on-no-content-match** — the co adaptation of codex's "conversation-text
  snippet else `None`" (`search.rs:171-186,246`), killing structural-JSON-key noise while *keeping*
  tool-content recall (divergence-by-design: co's `extract_messages` retains tool parts; codex is
  user/assistant only). It's a *simplification* of the original fallback (skip vs attach-arbitrary), so
  zero added cost.
- **REJECTED — JSON-escaping the query term** (codex `json_escaped_search_term`, `search.rs:228`).
  Verified that co persists transcripts via pydantic-core `dump_json`, which writes **literal UTF-8** (no
  `\uXXXX` ASCII-escaping) — so unicode / accented / CJK queries already match the raw line. The escape
  would only change matching for a query literally containing `"` or `\`, which session-recall queries
  never do. No true value here; the original "rare, accepted" note was correct.
co stays deliberately more precise than codex on line citations (codex returns file-level matches only).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev session-search-ripgrep`

## Delivery Summary — 2026-06-04

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | session test asserts uuid8 `path`, readable non-escaped snippet, `start==end==matched line`, structural-key skip, no IndexStore; `pytest test_flow_session_search.py` passes | ✓ pass |
| TASK-2 | `rg` sweep over `co_cli/`+`evals/` `.py` returns nothing; boot path imports clean + banner count works; `session_search`+`session_view` tests pass | ✓ pass |
| TASK-3 | `test_flow_memory_canon_recall.py` passes unchanged; manual orphan-cleanup command recorded (below) | ✓ pass |
| TASK-4 | tool-surface test: known phrase recalled with `session_id`+line citation, current session excluded, no-match empty, cap honoured | ✓ pass |

**Tests:** scoped — 14 passed, 0 failed (`test_flow_session_search.py` 7, `test_flow_session_view.py` 4, `test_flow_memory_canon_recall.py` 3).
**Doc Sync:** fixed (full scope) — `sessions.md`, `memory.md`, `01-system.md`, `bootstrap.md`, `config.md` rewritten off the file-based-search backend; all `init_session_index` / `session_chunk_*` / chunk-pipeline / BM25-chunk references removed. Flagged but **not** fixed (pre-existing, unrelated): `bootstrap.md` §5 Files lists stale paths `co_cli/memory/session.py`, `memory_store.py`, `indexer.py`.

**What shipped (delta vs plan):**
- New `co_cli/session/_search.py` — synchronous `search_sessions()` using `subprocess.run` (rg `--null --fixed-strings --ignore-case --no-config`, Python line-scan fallback) → `SessionHit` with `path`=uuid8, 1-indexed `start_line==end_line`, `score`=match count. **Sync, not async** (plan was backend-neutral): `recall._search_sessions` calls `store.search` synchronously, so keeping it sync left recall.py logic 100% unchanged per the tool-contract constraint.
- `SessionStore` rewritten file-based: `search()`→`_search.py`, `count()`=`*.jsonl` file count; no `IndexStore`, `index_session`, `sync`, `_sha256`.
- **Decoupling-by-design (delta):** `SessionStore` is now constructed **unconditionally** in `bootstrap/core.py` (moved out of the `if index_store is not None:` block) with `config + sessions_dir`. The plan pointed at line 404 inside that block; constructing it unconditionally is what realizes the plan's stated Outcome ("SessionStore drops its IndexStore dependency") — session search now works even when the hybrid index backend is unavailable.
- Removed: `chunker.py` (git rm), `init_session_index` + its `main.py` call + import (CD-M-3: `current_session_path` binding dropped to a bare `restore_session(...)` call), `session_chunk_*` config fields + env map (CD-M-4), eval `index_session` seed (CD-M-5).
- `recall.py` docstrings (module/internal/`query` arg) de-FTS5'd — prose only.

**Manual one-off orphan-cleanup op** (TASK-3, `feedback_no_migration_code` — NOT production code). Existing `~/.co-cli/co-cli-search.db` instances carry orphaned `source='session'` rows from the old pipeline. Drop them once, atomically (handles `docs`/`chunks`/`chunks_fts`/`chunks_vec`):
```bash
uv run python -c "
from co_cli.config.core import load_config, SEARCH_DB
from co_cli.index.store import IndexStore
idx = IndexStore(config=load_config(), db_path=SEARCH_DB)
print('removed', idx.remove_stale('session', set()), 'orphaned session docs')
idx.close()
"
```

**Overall: DELIVERED**
All four tasks passed `done_when`; lint clean; scoped tests green (14); doc sync fixed across the 5 specs. Ready for `/review-impl session-search-ripgrep`.

## Implementation Review — 2026-06-04

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | uuid8 `path`, readable non-escaped snippet, `start==end==matched`, structural-key skip, no IndexStore; `pytest test_flow_session_search.py` passes | ✓ pass | `_search.py:79-93` rg `--fixed-strings --ignore-case --no-config` + `--null`; Python fallback `:130-144` gated by `shutil.which("rg")` `:77`; part-select `needle in m.content.lower()` `:171-173`; skip-on-no-content `:175-176,182-183`; `path=uuid8` via `parse_session_filename` `:158-161,187`; rank `(score,created_at)` desc `:66`; SessionStore `search→_search` `store.py:29-31`, `count()` globs `*.jsonl` `:33-37`, no `index=`. 7 passed |
| TASK-2 | `rg "SESSION_SOURCE\|chunk_session\|init_session_index\|session_chunk_"` over `co_cli/`+`evals/` returns nothing; boot clean + banner count; session_search+view tests pass | ✓ pass | rg sweep returns zero (also `index_session`=0); `chunker.py` deleted (`D`); `main.py:596` bare `restore_session(...)` (no unused binding); SessionStore built unconditionally `core.py:399` w/o `index=`; eval JSONL write `eval_memory.py:191` kept, seed removed. 11 passed |
| TASK-3 | `test_flow_memory_canon_recall.py` passes unchanged; manual orphan-cleanup recorded | ✓ pass | test file UNCHANGED (no diff); memory `MemoryStore(index=index_store)` `core.py:405` + canon `_sync_canon_*` intact; `needs_reindex`/`remove_stale`/`count_docs` retain live memory+canon callers; no boot-time `remove_stale('session',…)` in runtime — only the documented one-off. 3 passed |
| TASK-4 | tool-surface test: known phrase recalled w/ `session_id`+line citation, current session excluded, no-match empty, cap honoured | ✓ pass | real `session_search` tool called `test:109-185`; browse `:130-135`; cap via `_SESSIONS_CHANNEL_CAP` `:162-172`; current excluded `:153-154`; no-match empty `:187`; no mocks/index/BM25/FTS5 leftovers; real `SessionStore`+JSONL. 7 passed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale package docstring: still claims transcripts "indexed under `source='session'` in the shared IndexStore" — same FTS5-prose class the G1 sweep fixed everywhere else (recall.py, store.py, 5 specs); `__init__.py` was missed | `co_cli/session/__init__.py:3-4` | minor | Rewritten to "searches them with file-based ripgrep (lexical, no index)"; stays docstring-only per the `__init__.py` rule |

### Tests
- Command: `uv run pytest -x`
- Result: 611 passed, 0 failed (1 pre-existing warning)
- Log: `.pytest-logs/20260509-173946-review-impl.log`

### Behavioral Verification
- No `co status` command exists; verified the changed surfaces via real bootstrap (`create_deps`) against live `~/.co-cli/sessions/`:
- Boot wiring: `create_deps` succeeds with no `init_session_index` step; `SessionStore` exposes no `index`/`index_session`/`sync` attribute (decoupled).
- `success_signal` TASK-2 verified: welcome-banner `count()` returns `12` (int) and displays; startup runs no session index sync.
- `success_signal` TASK-1/TASK-4 verified: `session_search` returns real past sessions with uuid8 `path` (`b8445d2b`), `start_line==end_line` citations, `source="session"`, synthetic `score`=match count, readable non-escaped snippets — no embedding/index involved.
- TASK-3 `success_signal` N/A (regression guard) — memory/canon recall green and untouched.

### Overall: PASS
All four tasks meet `done_when` with file:line evidence; full suite green (611); one minor stale-docstring fixed; behavioral smoke confirms boot, banner count, and lexical session_search on real data. Ready for Gate 2 / `/ship session-search-ripgrep`.
