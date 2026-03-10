# REVIEW: all — Co-System Health Check
_Date: 2026-03-10_

## What Was Reviewed

**DESIGN docs (28):**
DESIGN-core.md, DESIGN-core-loop.md, DESIGN-context-engineering.md, DESIGN-prompt-design.md,
DESIGN-flow-bootstrap.md, DESIGN-system-bootstrap.md, DESIGN-system.md, DESIGN-index.md,
DESIGN-knowledge.md, DESIGN-flow-knowledge-lifecycle.md, DESIGN-memory.md,
DESIGN-flow-memory-lifecycle.md, DESIGN-skills.md, DESIGN-flow-skills-lifecycle.md,
DESIGN-tools.md, DESIGN-tools-integrations.md, DESIGN-tools-execution.md,
DESIGN-tools-delegation.md, DESIGN-flow-tools-lifecycle.md, DESIGN-core-loop.md,
DESIGN-flow-approval.md, DESIGN-flow-context-governance.md, DESIGN-llm-models.md,
DESIGN-mcp-client.md, DESIGN-doctor.md, DESIGN-logging-and-tracking.md,
DESIGN-personality.md, DESIGN-eval-llm-judge.md

**TODO docs (3):**
TODO-capabilities-surface.md, TODO-chunking-rrf.md, TODO-fix-hi-he-orch.md

**DELIVERY docs:** none

---

## Auditor — TODO Health

| TODO doc | Task | Verdict | Key finding |
|----------|------|---------|-------------|
| TODO-capabilities-surface.md | TASK-1: `_capabilities.py` module | not_shipped | `co_cli/_capabilities.py` does not exist; `_doctor.py` is still the canonical health module |
| TODO-capabilities-surface.md | TASK-2: runtime capability snapshot in `CoRuntimeState` | not_shipped | `CoRuntimeState` has no `capabilities` field; the dataclass has 4 unrelated fields only |
| TODO-capabilities-surface.md | TASK-3: populate tool surface state in main loop | not_shipped | No tool surface population code in `main.py`; local `tool_names` list remains sole source of truth |
| TODO-capabilities-surface.md | TASK-4: skill surface in capabilities engine | not_shipped | No skill surface representation in any shared module |
| TODO-capabilities-surface.md | TASK-5: fold model/provider diagnostics into capabilities | not_shipped | `_model_check.py` is unchanged; `_capabilities.py` does not exist to absorb probe logic |
| TODO-capabilities-surface.md | TASK-6: replace `_doctor.py` with shared engine | not_shipped | `tools/capabilities.py` still imports `from co_cli._doctor import run_doctor` directly |
| TODO-capabilities-surface.md | TASK-7: `/doctor` as thin UX relay | partially_shipped | `co_cli/skills/doctor.md` already delegates to `check_capabilities` tool; no logic inside skill — but the backing tool still routes through `_doctor.py`, not `_capabilities.py` |
| TODO-capabilities-surface.md | TASK-8: expand functional coverage | not_shipped | No `tests/test_capabilities.py` or `tests/test_capabilities_engine.py` exists |
| TODO-capabilities-surface.md | TASK-9: rewrite docs to capabilities vocabulary | not_shipped | `DESIGN-doctor.md` still names `_doctor.py` as the canonical abstraction |
| TODO-chunking-rrf.md | TASK-1: `_chunker.py` new module | not_shipped | `co_cli/_chunker.py` does not exist |
| TODO-chunking-rrf.md | TASK-2: schema + index_chunks + search routing in `knowledge_index.py` | partially_shipped | `knowledge_index.py` is named `_knowledge_index.py`; chunking is implemented inline via character-position slicing in `index()` using `self._chunk_size`/`self._chunk_overlap` — but the dedicated `chunks`/`chunks_fts`/`chunks_vec` tables, `index_chunks()`, `remove_chunks()`, and chunk-aware search routing described in the TODO are NOT present |
| TODO-chunking-rrf.md | TASK-3: `sync_dir` — emit chunks on file sync | not_shipped | `sync_dir` does not call `index_chunks`; chunking is embedded in `index()` using the `docs` table only |
| TODO-chunking-rrf.md | TASK-4: `save_article` calls `index_chunks` | not_shipped | `tools/articles.py` has no `index_chunks` call |
| TODO-chunking-rrf.md | TASK-5: two new settings (`knowledge_chunk_size`, `knowledge_chunk_overlap`) + deps + main threading | shipped | `config.py`, `deps.py`, and `main.py` all have `knowledge_chunk_size`/`knowledge_chunk_overlap`; env vars use `CO_CLI_` prefix (minor mismatch vs `CO_` in TODO — not blocking) |
| TODO-chunking-rrf.md | TASK-6: RRF hybrid merge | not_shipped | `_hybrid_merge()` still uses weighted score merge (`vector_weight * vec_score + text_weight * fts_score`); no RRF |
| TODO-fix-hi-he-orch.md | Gap 1 — Sandboxed Execution | future_planning | Doc explicitly marks these as future-phase notes; no implementation tasks with `done_when`. Not an actionable TODO in the current planning sense |
| TODO-fix-hi-he-orch.md | Gap 2 — Dynamic Re-planning | future_planning | Same — design notes only, no `done_when` criteria |
| TODO-fix-hi-he-orch.md | Gap 3 — Codebase Impact Analysis | future_planning | Same |
| TODO-fix-hi-he-orch.md | Gap 4 — Eval Harness Integration | future_planning | Same |

### Well-formedness check

**TODO-capabilities-surface.md:**
- All 9 tasks have non-empty `files:` and `done_when:` fields. Done-when criteria are grep/file-exists verifiable. Scopes are atomic (1–5 files). Well-formed.

**TODO-chunking-rrf.md:**
- Has a file-action table and per-task descriptions but uses inline spec prose rather than the standard `files:` / `done_when:` block format. TASK-1 through TASK-6 can be extracted but are not in machine-verifiable task block form. The file also references `co_cli/knowledge_index.py` (no leading underscore) but the actual file is `co_cli/_knowledge_index.py` — stale filename assumption.
- TASK-5 partially shipped: settings and deps fields exist, but env var prefix is `CO_CLI_` in code vs `CO_` in the TODO (`CO_KNOWLEDGE_CHUNK_SIZE`). Minor inconsistency, not blocking.

**TODO-fix-hi-he-orch.md:**
- The doc header declares `Task type: doc` and explicitly frames content as future-phase planning notes, not an implementation plan. There are no `done_when:` blocks and no `files:` lists. This doc does not conform to the standard TODO task block format and is not actionable as a planning input without a dedicated `/orchestrate-plan` cycle per gap. Its presence in the TODO directory is intentional (parking lot for future cycles).

**Overall verdict for `TODO-capabilities-surface.md`: `ready_for_plan`**
All 9 tasks are well-formed with verifiable `done_when` criteria. Nothing has shipped — the refactor is greenfield. No stale module references (all files named exist: `_doctor.py`, `tools/capabilities.py`, `_model_check.py`, `deps.py`, `main.py`). The task sequence and prerequisites are internally consistent.

**Overall verdict for `TODO-chunking-rrf.md`: `needs_cleanup`**
TASK-5 is partially shipped (settings and deps fields). The file module name assumption is wrong (`knowledge_index.py` should be `_knowledge_index.py`). Task blocks lack the standard `files:` / `done_when:` format — they must be reformatted before planning. The shipped inline chunking in `index()` (character-position slicing into the `docs` table) is a partial prior implementation that diverges from the TODO's design (separate `chunks`/`chunks_fts`/`chunks_vec` tables). Cleanup should: (1) fix the filename, (2) mark TASK-5 as shipped, (3) add standard task block headers with `done_when:`, (4) clarify the relationship between existing inline chunking in `docs` and the new separate-table design.

**Overall verdict for `TODO-fix-hi-he-orch.md`: `needs_cleanup`**
Not an implementation-ready TODO — it is a future-phase research parking lot with no `done_when` or `files:` blocks. Either rename to `docs/reference/RESEARCH-orch-hile-gaps.md` (matching doc placement conventions) or convert each gap into a properly structured TODO with `done_when:` before it enters a planning cycle. As-is it cannot be handed to `/orchestrate-plan` directly.

---

## Auditor — Delivery Artifact Lifecycle

No DELIVERY docs in scope — all delivered features cleaned up after Gate 3.

**Overall delivery lifecycle: clean**

---

## Verdict — PENDING Code Dev results

The final overall verdict (HEALTHY / NEEDS_ATTENTION / ACTION_REQUIRED) requires Code Dev findings. The Auditor findings below are complete and can be incorporated once Code Dev appends their results.

**Auditor-side summary:**

| Priority | Action | Source |
|----------|--------|--------|
| P1 | Rename `docs/TODO-fix-hi-he-orch.md` to `docs/reference/RESEARCH-orch-hile-gaps.md` or convert gaps to proper task-block format before any `/orchestrate-plan` cycle | TODO-fix-hi-he-orch.md |
| P2 | Fix stale filename in TODO-chunking-rrf.md: `knowledge_index.py` → `_knowledge_index.py` | TODO-chunking-rrf.md |
| P2 | Add standard `files:` / `done_when:` task block format to TODO-chunking-rrf.md tasks | TODO-chunking-rrf.md |
| P2 | Mark TASK-5 of TODO-chunking-rrf.md as shipped; clarify relationship between existing inline `docs`-table chunking and the new separate-table design | TODO-chunking-rrf.md |
| P3 | No action needed on TODO-capabilities-surface.md — ready for planning | TODO-capabilities-surface.md |

**Recommended next step:** Run `/orchestrate-plan capabilities-surface` (the most complete and ready TODO), and separately clean up `TODO-chunking-rrf.md` task format before planning that cycle.
