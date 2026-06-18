# Close Remaining context/ Leaf-Boundary Edges (R4 round 2)

**Slug:** `close-context-leaf-edges` · **Created:** 2026-06-18 16:18:49

## Context

Promoted from the `rules-conformance-cleanup` deferred backlog. Round 1 of that
plan relocates the turn-loop (`orchestrate.py`) out of the `context/` leaf,
eliminating the loop's `context → tools` edges. This plan closes the **remaining**
leaf back-edges the audit found, honoring the `01-system.md` leaf-package invariant
(corrected this session): leaves don't import sideways into `tools`/`session`;
foundational constants live in `config/`, lateral domain access goes through
`CoDeps`.

`config/tuning.py` already exists as the sanctioned foundational constants home
("imports nothing, so every other package can depend on it freely") — the natural
target for the bare tool constants.

**Re-classified as NOT a violation (excluded):** `context/summarization.py → llm.call`
(`llm` is foundational infra below `context` — a downward dependency, allowed).

**Sequencing:** best run after `rules-conformance-cleanup` round 1 (both touch
`context/`), but technically independent.

## Tasks

### ✓ DONE TASK-1 — Hoist bare tool constants to `config/tuning.py`
Move `MAX_TOOL_CALLS_PER_MODEL_REQUEST` + `TOOL_CAP_HARD_STOP_CONSECUTIVE`
(`tools/tool_call_limit.py`) and the `PERSISTED_OUTPUT_TAG` / `PERSISTED_OUTPUT_CLOSING_TAG`
pair (`tools/tool_io.py:47-48`) to `config/tuning.py`. Move both tags together — never
split the open/close pair across modules. Repoint **every** importer, not just `context/`:
the cap constants are also consumed by `agent/orchestrate.py:86-87`, `agent/toolset.py:58`,
and `bootstrap/core.py` (three lazy imports). `tool_call_limit.py` legitimately retains
`MaxToolCallsExceededPayload` + `make_exceeded_payload` and re-imports the constant from
`config/tuning.py` (a downward `tools → config` edge, allowed). Zero-backward-compat: no
alias left behind.
- **files:** `co_cli/config/tuning.py`, `co_cli/tools/tool_call_limit.py`, `co_cli/tools/tool_io.py`, `co_cli/context/compaction.py`, `co_cli/context/history_processors.py`, `co_cli/agent/orchestrate.py`, `co_cli/agent/toolset.py`, `co_cli/bootstrap/core.py`, plus any other importer surfaced by grep
- **done_when:** rebuilt edge map shows no `context → tools` edge for these constants (`compaction.py:617`, `history_processors.py:63-64`); full suite green; no behavior change

### ✓ DONE TASK-2 — Relocate `spill_if_oversized` to a foundational module
`spill_if_oversized` (`tools/tool_io.py:76`) is tool-result spill logic consumed by
**both** `context/history_processors.py:352` **and** `tools/tool_io.py:152`
(`spill_with_span` → `tool_output`). Because `tools` is also a consumer, moving it into
`context/` would create a new `tools → context` back-edge — the inverse of the edge we
are closing. The only correct home is a **foundational module below both** `tools` and
`context`. The function already depends only on foundational infra (`fileio.atomic`,
`observability.tracing`), and its `SPILL_*` constants already live in `config/tuning.py`,
so a foundational spill home is clean. Relocate `spill_if_oversized` (and the
`spill_with_span` wrapper if it travels with it) to a new foundational module
(e.g. `co_cli/fileio/spill.py`); repoint `tools/tool_io.py` and
`context/history_processors.py` to import downward from it.
- **files:** `co_cli/tools/tool_io.py`, `co_cli/context/history_processors.py`, new `co_cli/fileio/spill.py` (or chosen foundational home)
- **done_when:** rebuilt edge map shows no `context → tools` edge for `spill_if_oversized` AND no new `tools → context` edge introduced; both consumers import downward into the foundational module; full suite green

### ✓ DONE TASK-3 — Relocate the misplaced dream-queue producers out of `session/`
`context/compaction.py:65-66` imports `session.persistence.append_messages` and
`session.review_kick.write_review_kick`. The framing matters: **these are not session
writes.** At `compaction.py:323-324` the call writes a *dream snapshot* to
`DREAM_SNAPSHOTS_DIR` and drops a *memory review KICK* to `DREAM_QUEUE_DIR` — both are
dream-subsystem producer concerns that merely live under `session/` today.
`write_review_kick` already disclaims (in its own docstring) any `context/` coupling and
imports only `config` + `fileio.atomic`; it is shared by `main.py:57` and compaction
alike. `append_messages` is a generic JSONL append used here only for the snapshot, not a
transcript.

Do **not** route through `deps.session_store` — `SessionStore` exposes only
`search`/`count` (transcript search), so attaching snapshot-append + dream-kick to it is a
semantic mismatch that pollutes the session domain. The correct fix mirrors round 1's
treatment of `orchestrate.py`: **relocate to the owning layer**. Move `write_review_kick`
(and the dream-snapshot append it pairs with) into a foundational dream-producer module
that imports only `config` + `fileio.atomic` (+ pydantic messages) — e.g. a
`daemons/dream/` producer submodule or `co_cli/dream_queue.py`. Repoint both callers:
`context/compaction.py` and `main.py:57`.
- **files:** `co_cli/session/review_kick.py` (relocate), `co_cli/session/persistence.py` (`append_messages` — relocate or leave if still a genuine transcript helper), `co_cli/context/compaction.py`, `co_cli/main.py`, new dream-producer module
- **done_when:** rebuilt edge map shows no `context → session` MODULE edge; no new leaf back-edge introduced; the relocated producer imports only `config`/`fileio`; full suite green; no behavior change

## Verification
`scripts/quality-gate.sh full`; rebuild the audit import-edge map and confirm the
`context → tools` and `context → session` edges enumerated above are gone.

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | no `context → tools` edge for the cap/persisted-output constants; suite green | ✓ pass |
| TASK-2 | no `context → tools` edge for `spill_if_oversized`; no new `tools → context` edge; both consumers import downward | ✓ pass |
| TASK-3 | no `context → session` module edge; relocated producer imports only `config`/`fileio`; suite green | ✓ pass |

**Edge verification:** `grep` for `co_cli.tools` / `co_cli.session` imports under `co_cli/context/` → both empty. `grep` for `co_cli.context` imports under `co_cli/tools/` → empty (no new back-edge). `fileio/spill.py` imports `config` + `fileio` + `observability`; `dream_queue.py` imports `config` + `fileio` only.

**What moved:**
- `MAX_TOOL_CALLS_PER_MODEL_REQUEST`, `TOOL_CAP_HARD_STOP_CONSECUTIVE`, `PERSISTED_OUTPUT_TAG`/`PERSISTED_OUTPUT_CLOSING_TAG` → `co_cli/config/tuning.py`. `tool_call_limit.py` keeps `MaxToolCallsExceededPayload` + `make_exceeded_payload`, re-importing the cap from tuning.
- `spill_if_oversized` + `spill_with_span` + `_generate_preview` → new `co_cli/fileio/spill.py` (foundational, below both `tools` and `context`).
- `write_review_kick` (from `session/review_kick.py`, now deleted) + new `write_dream_snapshot` (replacing the inline `session.persistence.append_messages` snapshot write) → new `co_cli/dream_queue.py`. `append_messages` stays in `session/persistence.py` — still the genuine transcript helper for `persist_session_history`. Compaction shed its `datetime`/`uuid4`/`DREAM_SNAPSHOTS_DIR` imports via full snapshot encapsulation.

**Extra files touched** (beyond plan `files:`, all required for the relocation): `co_cli/agent/orchestrate.py`, `co_cli/agent/toolset.py` (cap + spill import repoints); 7 test files + `evals/eval_daily_chat.py` (moved-symbol import repoints; snapshot monkeypatch target retargeted to `co_cli.dream_queue.*`).

**Tests:** scoped — 109 passed, 0 failed (spill, tool-call cap, model-request cap, compaction processor chain/proactive/spill-largest, files_read, review-snapshot, post-turn hook, exit-cleanup, multi-repl kick, review-kick e2e, override-snapshot).
**Doc Sync:** fixed — compaction.md, dream.md, tools.md, pydantic-ai-integration.md (stale module locations for all relocated symbols).

**Overall: DELIVERED**
All three leaf back-edges closed; both directions verified clean; scoped tests green; specs synced.

## Implementation Review — 2026-06-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | no `context → tools` edge for cap/persisted-output constants; suite green | ✓ pass | Constants now in `config/tuning.py:95-107`; `tools/tool_call_limit.py:9` re-imports cap downward; `context/history_processors.py:42-45` imports `PERSISTED_OUTPUT_TAG` from `config.tuning`; `agent/orchestrate.py:71-73`, `agent/toolset.py:12`, `bootstrap/core.py:329,373` all repointed to `config.tuning`. No alias left in donor modules. |
| TASK-2 | no `context → tools` edge for `spill_if_oversized`; no new `tools → context` edge; both consumers import downward | ✓ pass | `spill_if_oversized`/`spill_with_span`/`_generate_preview` in new `fileio/spill.py` (imports only `config`+`fileio`+`observability`); `tools/tool_io.py:29` and `context/history_processors.py:63` both import downward. |
| TASK-3 | no `context → session` module edge; relocated producer imports only `config`/`fileio`; suite green | ✓ pass | `write_review_kick`+`write_dream_snapshot` in new `dream_queue.py` (imports only `config`+`fileio`+pydantic); `context/compaction.py:61` and `main.py:54` repointed; `session/review_kick.py` deleted. |

**Edge-map verification (re-run cold):** `grep co_cli.tools|co_cli.session` under `co_cli/context/` → only a docstring mention in `_dedup_tool_results.py:35`, no import. `grep co_cli.context` under `co_cli/tools/` → empty (no new back-edge). No stragglers for `session.review_kick` / `context.orchestrate` / `session.persistence import append_messages` anywhere in `co_cli/`.

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Extra files in working tree | `config/memory.py`, `evals/eval_memory.py` | n/a — out of scope | Belong to a separate in-flight plan (memory `review_enabled` / dream-reviewer eval), not this slug. Do NOT stage them under this plan's `/ship`. |

_No blocking issues found in any task's declared `files:`._

### Tests
- Command: `uv run pytest -v`
- Result: 782 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads cleanly after the constant/spill/dream-queue relocations) — the gating smoke for a pure import-edge refactor.
- No LLM-mediated behavior changed (refactor is import-graph only); chat non-gating.

### Overall: PASS
All three leaf back-edges closed and verified clean in both directions; full suite green; lint clean; boot smoke passes. Note for ship: `config/memory.py` and `evals/eval_memory.py` in the working tree are unrelated to this slug — exclude from the staged set.
