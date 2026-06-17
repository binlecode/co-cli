# Surface Silent Recall Degradation — make hybrid→FTS fallback and reranker-breaker-open visible to the model in the tool result, not just in spans

Task type: core-loop correctness fix (recall observability → model-visible signal). Independent of the other two refocus plans; shippable standalone.

## Context

co's recall is a hybrid pipeline: FTS5 BM25 + sqlite-vec vector search + RRF fusion + TEI
cross-encoder rerank, with relevance floors (`co_cli/index/_retrieval.py`). The quality-gating floor
that actually drops weak hits is the **post-rerank floor** (`rerank_score_floor`, default 0.2,
`_retrieval.py:500`); the pre-fusion vector floor (0.02) is near-permissive by design.

Two degradation paths return results that look normal but are materially weaker, **with zero signal
to the model**:

1. **Hybrid → FTS fallback (`_retrieval.py:242-263`).** If the embedder errors mid-query
   (`self._embedding.embed` raises, or returns `None`), the `except` logs a warning, emits an
   `index.hybrid_degraded_to_fts` span event, and returns FTS-only results. The semantic-recall half
   is gone and the model is never told.
2. **Reranker circuit-breaker open (`co_cli/index/_circuit.py`, consulted in `_rerank`,
   `_retrieval.py:~485-505`).** After 3 consecutive TEI failures the breaker opens for 5–10s; during
   that window `_rerank` skips the cross-encoder and returns the unranked RRF candidate list. The
   **0.2 quality floor never runs**, so weak candidates that would have been dropped leak through —
   again with no model-visible signal (a debug log only).

The spans go to `co tail` / `co trace` (`co_cli/observability/`), which the *operator* sees, not the
*model*. So the agent answers from lexical-only or unfloored results believing it did a full hybrid
recall — a likely contributor to the reported "struggling with effective recall."

### Code-state verification (claims checked against HEAD)
- `_hybrid_search` returns `self._rerank(fts_query, _dedup_by_path(fts_chunks), limit)` on the
  fallback branch after emitting `index.hybrid_degraded_to_fts` (`_retrieval.py:259-263`). Confirmed.
- `IndexStore.search` (`co_cli/index/store.py:508-549`) emits the `index.search` span with
  `co.index.hits` but returns a bare `list[SearchResult]` — no degradation field on the result.
- `SearchResult` is the carrier returned up through `MemoryStore.search_memory_items`
  (`co_cli/memory/store.py:166-207`) to the `memory_search` tool
  (`co_cli/tools/memory/recall.py:172-218`), which formats the model-facing text/`ToolReturn`.
- `session_search` is lexical ripgrep and has NO hybrid/rerank path — this plan does not touch it
  (`feedback_session_search_ripgrep`).

## Problem & Outcome

**Problem.** When the embedder or reranker is unavailable, recall silently degrades to a weaker mode
and the model presents the degraded results as authoritative — it cannot adjust its confidence,
widen its query, or tell the user the recall was partial.

**Outcome.** When a `memory_search` (or other hybrid recall) result was produced in a degraded mode,
the tool result carries a concise, model-readable annotation (e.g. "note: semantic recall
unavailable — lexical-only results" / "note: reranking unavailable — results not relevance-filtered")
so the model can react. Spans remain for the operator; this adds the **model-facing** channel that is
missing. No change to the floors, the fusion, or the breaker thresholds.

## Scope

### In scope
- Propagate a degradation signal from `RetrievalService` through `IndexStore.search` →
  `MemoryStore` → the `memory_search` tool's model-facing return.
- A one-line annotation in the tool result text when degradation occurred.

### Out of scope
- Changing relevance floors, RRF, breaker trip/probe thresholds, or fallback behavior itself (the
  fallback is correct — only its invisibility is the bug).
- `session_search` (no hybrid path).
- Auto-recall / last-user-message recall nudging (separate idea, not this plan).

## Behavioral Constraints
- The degradation signal must be a structured field on the result path, not a magic string parsed
  downstream (`feedback_naming_no_abbreviations` — no magic labels; use an enum/named constant).
- Set the degradation flag only on the branch that actually degraded
  (`feedback_set_state_flags_after_success` discipline — accurate signal, no optimistic tagging).
- Underscore/visibility contract holds if any helper crosses a package boundary
  (`feedback_underscore_visibility_contract`).

## Tasks

✓ DONE **TASK-1 — Carry a degradation signal out of retrieval (always)**
- files: `co_cli/index/_retrieval.py`, `co_cli/index/store.py`, `tests/test_retrieval_degradation.py` (new)
- done_when: `IndexStore.search` returns the result set together with a degradation indicator (a named
  enum/flag distinguishing none / `semantic_unavailable` / `rerank_unavailable`), set on exactly the
  `_hybrid_search` fallback branch (`semantic_unavailable`) and BOTH unranked-return branches of `_rerank`
  — breaker-open AND the `except` fallback after a TEI failure (`rerank_unavailable`). The
  `provider == "none"` / no-candidates branch is NOT degradation (reranking intentionally off — tagging it
  is a false signal). A test drives a real `IndexStore` configured to force each branch (no embedder
  available → `semantic_unavailable`; reranker unreachable → `rerank_unavailable`) and asserts the
  indicator is set, and that a fully healthy hybrid query reports none.
- success_signal: retrieval reports *how* it answered, not just *what* it found.
- prerequisites: none

✓ DONE **TASK-2 — Surface the signal in the model-facing tool result (always)**
- files: `co_cli/memory/store.py`, `co_cli/tools/memory/recall.py`, `tests/test_memory_search_tool.py` (extend)
- done_when: `MemoryStore.search_memory_items` propagates the indicator, and `memory_search` appends a
  concise model-readable note to its `ToolReturn` text when degradation occurred (none → no note); a
  test calls `memory_search` through a real degraded `IndexStore` and asserts the returned text contains
  the degradation note, and that a healthy search returns no note.
- success_signal: in a live session, a `memory_search` run while the embedder/reranker is down tells the
  model the recall was lexical-only / unfiltered.
- prerequisites: TASK-1

## Testing
- Functional assertions only: the note is present iff recall degraded, absent when healthy
  (`feedback_functional_tests_only`). Drive real degradation by configuring the `IndexStore` (no
  embedder / unreachable reranker), not by mocking internals.
- Fail-fast `-x`; pipe pytest to `.pytest-logs/`; tail the log.

## Open Questions
1. Exact note wording — must be terse and actionable without inflating every recall result. Draft in
   TASK-2 and keep it one line per degradation mode.
2. Whether other hybrid consumers exist beyond `memory_search` that should also surface the note (grep
   `IndexStore.search` callers during TASK-1; canon indexing is bootstrap-only and has no model surface).

## Delivery Summary — 2026-06-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `IndexStore.search` returns results + degradation set, set on the hybrid-FTS fallback AND both `_rerank` unranked branches (breaker-open + except), `none` branch untagged; tests force each branch + healthy | ✓ pass |
| TASK-2 | `MemoryStore.search_memory_items` propagates the set; `memory_search` appends an imperative note per mode iff degraded; tests assert note present/absent | ✓ pass |

**Design decisions (vs draft):**
- Carrier is `frozenset[RecallDegradation]` (empty = healthy), not a single enum — semantic + rerank degradation **co-occur** on one query (embedder down → FTS fallback → reranker also fails). Surfaced query-level (`search` returns `(results, degraded)`), never as a per-`SearchResult` field.
- G1 fix applied: tagged BOTH `_rerank` unranked-return branches (breaker-open `_retrieval.py:494` + `except` fallback `:504`); left `provider=="none"` (`:490`) untagged (intentional-off, not degradation).
- Notes phrased as imperative `RECALL WARNING` directives, not soft advisory text, for the small local model (per maintainer call). Advisory-only — no auto-retry/flow-control (scoped out).
- Only production caller of `IndexStore.search` is `MemoryStore.search_memory_items` (Open Q #2 resolved); canon indexing is bootstrap-only, no model surface.

**Extra files (beyond plan `files:`):** test callers updated for the tuple return — `tests/index/test_recall_floors.py`, `tests/index/test_rerank_truncation.py`, `tests/test_flow_memory_store.py`, `tests/test_flow_memory_items_waterfall_cap.py`. Plan named `tests/test_memory_search_tool.py` (does not exist); used the real files `tests/test_flow_memory_search.py` (tool) + new `tests/test_retrieval_degradation.py` (retrieval).

**Tests:** scoped — 17 passed, 0 failed (`test_flow_memory_search`, `test_flow_memory_items_waterfall_cap`, `test_retrieval_degradation`, `index/test_recall_floors`, `index/test_rerank_truncation`).
**Doc Sync:** fixed — `docs/specs/memory.md` (degradation now two-channel: operator span + model note) and `docs/specs/observability.md` (`co.index.degraded` span attribute).

**Overall: DELIVERED**
Recall degradation now reaches the model in-turn as an imperative warning, not just operator spans. Side discovery: TEI was healthy all along (8282=reranker, 8283=embedder per config) — an earlier mis-port-mapped test fixture, now fixed.

## Status
REVIEWED — PASS. Ready for Gate 2 (TL reads verdict) → `/ship`.

## Implementation Review — 2026-06-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `IndexStore.search` returns results + degradation set, tagged on exactly the right branches; healthy = none | ✓ pass | `_retrieval.py:279` SEMANTIC on FTS-fallback; `:518` RERANK breaker-open; `:529` RERANK TEI-except; `:514` provider=="none" untagged; tuple on all paths (:215/:228/:240); `store.py:551` `co.index.degraded` span attr + tuple return :554 |
| TASK-2 | `search_memory_items` propagates set; `memory_search` appends note iff degraded | ✓ pass | `store.py:196,210` union over both passes; `recall.py:124` consume, `:238-247` note on both branches, `:122` grep→frozenset(); call path memory_search→_search_memory_items→search_memory_items→index.search confirmed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Missed `IndexStore.search` caller — tuple-return breaks it | tests/test_flow_bootstrap_canon.py:31,36,38 | blocking | Unpacked `results, _ =` (3 sites) |
| Missed caller (delete regression test) | tests/test_flow_memory_item_manage.py:131,140 | blocking | Unpacked `hits, _ =` |
| Missed caller (write index test) | tests/test_flow_memory_write.py:65 | blocking | Unpacked `hits, _ =` |
| Non-blocking: fts5-path `_rerank` dead at IndexStore wiring (cross_encoder_url hybrid-gated) | store.py:189-191 | minor | Pre-existing, out of scope — noted only |

_Root cause of all three blockers: dev-phase grep for `.search(` callers was over-filtered and hid these three test files. An unfiltered sweep (`grep -rn "\.search(" co_cli tests evals`) now confirms every `IndexStore.search` caller unpacks the tuple; `co_cli/tools/session/recall.py:69` is `SessionStore.search` (has `is_regex`), correctly untouched._

### Tests
- Command: `uv run pytest` (full suite)
- Result: 775 passed, 0 failed
- Log: `.pytest-logs/20260617-183056-review-impl3.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads, commands listed)
- `success_signal` (both tasks) verified via direct-call repro `test_memory_search_degraded_recall_appends_note` — a real degraded `IndexStore` (hybrid + dead embed/rerank URLs) routed through `memory_search` emits the `RECALL WARNING` note; healthy `memory_search` emits none. The model's adaptation to the note is LLM-mediated, marked non-gating.

### Overall: PASS
Both tasks meet done_when with file:line evidence; three missed test callers (all the same tuple-return root cause) fixed; full suite green; boot smoke + repro confirm the model-facing note fires iff recall degraded.

### Post-review tweak — 2026-06-17
Peer check (openclaw, hermes, codex, opencode) found the only architecturally-comparable peer (openclaw: hybrid on by default) DOES surface degradation to the model — but as a compact structured field, not prose. Swapped co's multi-line imperative `RECALL WARNING` blocks for a single terse `recall: …` provenance line (`co_cli/tools/memory/recall.py`), peer-aligned. Behavior unchanged (note fires iff degraded); tests + memory.md updated. Lint clean, affected tests green.
