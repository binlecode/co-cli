# retire-skill-search

## Context

**Current state (verified):**
- `skill_search` tool: `co_cli/tools/system/skills.py:76-121` — FTS5-backed discovery; `delegation=frozenset({"session_reviewer", "skill_curator"})`.
- `SkillIndex`: `co_cli/skills/index.py` — FTS5 over `source='skill'` in shared DB. Bootstrap constructs it at `co_cli/bootstrap/core.py:388-401`; lifecycle refresh syncs at `co_cli/skills/lifecycle.py:24-32`; cleanup at `co_cli/main.py:174-175`; inherited by fork_deps at `co_cli/deps.py:362`.
- Manifest: `co_cli/context/manifests/skill_manifest.py:18-45` — bundled-only today (explicit filter excludes user-dir skills). Injected into main agent by `main.py:430-439`.
- Native toolset import: `co_cli/agents/_native_toolset.py:36`.
- `skill_manage` behavioral docstring: line 428 says "Search before creating: call skill_search(query)".
- Subagents: both `session_reviewer` and `skill_curator` receive `skill_search` via delegation filter; `co_cli/skills/curator_prompts.py:20` references `skill_search` for dedup.
- Runtime rule files: `co_cli/context/rules/06_skill_protocol.md:11,20,58`; `co_cli/skills/skill-creator.md:20,63`.
- Inline doc: `co_cli/tools/memory/recall.py:4`; `CLAUDE.md:39,48`.
- Tests: `tests/test_flow_skill_search.py` (8 tests), `tests/test_flow_skill_index.py` (6 tests), `tests/test_flow_session_review.py` delegation asserts at lines 129/146/170/175.
- On disk: 7 bundled skills in `co_cli/skills/`; `~/.co-cli/skills/` does not exist.

## Problem & Outcome

co-cli splits skill discovery into a bundled manifest (7 skills, always-visible) and `skill_search` (FTS5, query-driven) for the long tail. With only 7 skills and no user-dir in production, `skill_search` never fires — it pays DB construction cost, FTS5 upsert overhead, and a two-surface spec for zero runtime benefit. Peers (hermes, opencode) converge on a single manifest covering all discoverable skills plus `skill_view` for bodies.

**Goal**: collapse to hermes-shape — all skills (bundled + user-dir) in `<available_skills>`, `skill_view` for body, `skill_search` and `SkillIndex` gone.

**Failure cost**: Without this, `SkillIndex` keeps writing (DB per startup) and two dedicated test suites keep running for a feature with zero live users. More critically, user-installed skills are invisible in the manifest today — a silent UX regression once skill install is actually used.

## Scope

**In**: manifest all-discoverable (D1); delete `skill_search` tool (D4); delete `SkillIndex` (D4); inject manifest into subagent instructions replacing `skill_search` (D5); soft size guardrail at create/install (D3); rule file + inline doc cleanup (D8); test cleanup (D7).

**Out**: Capability gating (`requires:` frontmatter filter — D2, deferred). Spec rewrites for `docs/specs/` — handled by `sync-doc` post-delivery.

## Behavioral Constraints

- Zero backward compat: no alias, no deprecated stub for `skill_search`.
- `skill_view` shape unchanged — exact-name dict `.get()`, already hermes-shape.
- User-dir skills shadow bundled by same name in the manifest (existing precedence preserved).
- Size guardrail: soft warn (not block) on `skill_manage(action='create'|'install')` when total skill count ≥ 30 after write. Warning surfaced as `size_warning` key in the JSON result. (Threshold 30 ≈ prompt-budget ceiling for skill listing; tunable.)
- Subagents discover skills via the injected `<available_skills>` manifest; `skill_view` and `skill_manage` remain delegated for body-read and write.

## High-Level Design

```
render_skill_manifest(skill_registry, skills_dir, user_skills_dir)
  → walks ALL skill_registry entries (bundled + user-dir)
  → user-dir file present for name → emit user description (shadows bundled)
  → no user-dir file → emit bundled description
  → injects into main agent prompt AND both subagent instructions
```

**SkillIndex removal path**: delete `co_cli/skills/index.py`; remove `skill_index` field from `CoDeps` and `fork_deps`; strip step 7c from bootstrap; strip index sync from `lifecycle.refresh_skills`; remove `skill_index.close()` from main cleanup.

**Subagent manifest injection (D5)**: at the start of `run_session_review()` and `maybe_run_curator()`, call `render_skill_manifest(deps.skill_registry, deps.skills_dir, deps.user_skills_dir)`. Prepend the rendered string to the `instructions` argument passed to `build_agent()`.

## Tasks

### ✓ DONE TASK-1 — Manifest: all-discoverable
**files**: `co_cli/context/manifests/skill_manifest.py`
**done_when**: `render_skill_manifest()` returns entries for both bundled and user-dir skills; a user-dir skill with a name that matches a bundled skill shows the user description (shadow wins); a user-dir-only skill (not in bundled dir) appears in the output.
**success_signal**: `pytest tests/test_flow_skill_manifest.py -x` passes with tests verifying all-discoverable behavior (user-dir included, shadow override correct).
**prerequisites**: none

### ✓ DONE TASK-2 — Remove skill_search; add size guardrail
**files**: `co_cli/tools/system/skills.py`, `co_cli/agents/_native_toolset.py`
**done_when**: `skill_search` function and its `@agent_tool` registration deleted from `skills.py`; `skill_search` removed from the import in `_native_toolset.py:36`; `skill_manage` behavioral docstring no longer references `skill_search`; `_skill_create` and `_skill_install` append `size_warning` to the JSON result when `len(ctx.deps.skill_registry) >= 30` after write.
**success_signal**: A test calls `_skill_create` with a `deps` whose `skill_registry` is pre-populated with ≥ 30 entries and asserts `size_warning` appears in the parsed JSON result.
**prerequisites**: none

### ✓ DONE TASK-3 — Delete SkillIndex
**files**: `co_cli/skills/index.py` (delete file), `co_cli/deps.py`, `co_cli/bootstrap/core.py`, `co_cli/skills/lifecycle.py`, `co_cli/main.py`
**done_when**: `co_cli/skills/index.py` deleted; `SkillIndex` TYPE_CHECKING import removed from `co_cli/deps.py:29`; `CoDeps.skill_index` field removed; `fork_deps` no longer passes `skill_index`; bootstrap step 7c (lines 388-401) removed; `lifecycle.refresh_skills` no longer syncs to any index; `main.py:174-175` `skill_index.close()` block removed.
**success_signal**: N/A (pure deletion). `pytest -x` must not raise `AttributeError` on `skill_index` or any import error from `co_cli.skills.index`.
**prerequisites**: none

### ✓ DONE TASK-4 — Subagent skill discovery via manifest
**files**: `co_cli/skills/curator_prompts.py`, `co_cli/agents/session_review.py`, `co_cli/agents/skill_curator.py`
**done_when**: `SESSION_REVIEW_INSTRUCTIONS` and `CURATOR_INSTRUCTIONS` each prepend a `{skills_manifest}` block (or the callers inject the manifest string before the static instruction text); `run_session_review()` and `maybe_run_curator()` call `render_skill_manifest(deps.skill_registry, deps.skills_dir, deps.user_skills_dir)` and pass the result into the subagent instructions; `06_skill_protocol.md` reference to `skill_search` for dedup removed from curator prompt text.
**success_signal**: A test in `tests/test_flow_session_review.py` constructs the combined instructions string (manifest + `SESSION_REVIEW_INSTRUCTIONS`) using a real `render_skill_manifest()` call against a populated `skill_registry` dict and asserts `<available_skills>` appears in the result.
**prerequisites**: TASK-1 (manifest renderer returns all-discoverable output before injection)

### ✓ DONE TASK-5 — Rule files and inline doc cleanup
**files**: `co_cli/context/rules/06_skill_protocol.md`, `co_cli/skills/skill-creator.md`, `co_cli/tools/memory/recall.py`, `CLAUDE.md`, `agent_docs/system-workflows-to-test.md`
**done_when**: `grep -r skill_search co_cli/context/rules/06_skill_protocol.md co_cli/skills/skill-creator.md co_cli/tools/memory/recall.py CLAUDE.md agent_docs/system-workflows-to-test.md` returns zero matches; `06_skill_protocol.md` updated to describe manifest-scan for dedup (not `skill_search`); `CLAUDE.md` skill surface lists `skill_view` / `skill_manage` only.
**success_signal**: N/A (doc-only).
**prerequisites**: none

### ✓ DONE TASK-6 — Test cleanup
**files**: `tests/test_flow_skill_search.py` (delete), `tests/test_flow_skill_index.py` (delete), `tests/test_flow_session_review.py`, `tests/test_flow_skill_manifest.py`
**done_when**: Two test files deleted; `test_flow_session_review.py` delegation assertions changed to `assert "skill_search" not in tools` (lines 129, 145); the inline `skill_search` tool call at line 170 and ToolReturnPart at line 175 replaced with a non-skill-search tool name (or removed if the test can be simplified); `test_flow_skill_manifest.py` includes one new test that writes a skill file to a temp user-skills dir and asserts it appears in `render_skill_manifest()` output; full suite passes.
**success_signal**: `pytest tests/test_flow_skill_manifest.py tests/test_flow_session_review.py -x` passes, confirming manifest all-discoverable coverage and delegation set correctness.
**prerequisites**: TASK-1, TASK-2, TASK-3

## Testing

All coverage via functional pytest — no mocks.

1. **`test_flow_skill_manifest.py`**: render all-discoverable (bundled + user-dir); shadow override; user-dir-only skill visible; existing HTML-escape and empty-set tests remain.
2. **`test_flow_session_review.py`**: delegation set for `session_reviewer` and `skill_curator` does NOT include `skill_search`; `skill_view` and `skill_manage` still present.
3. **Full suite**: `pytest -x` passes — no `skill_index` attribute errors, no orphaned `SkillIndex` import paths.

## Open Questions

None. All 13 decisions resolved pre-plan (D1–D13).

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1   | adopt    | The TYPE_CHECKING import is a real broken-import risk. | TASK-3 `done_when` updated to include "remove `SkillIndex` TYPE_CHECKING import from `co_cli/deps.py:29`". |
| CD-M-2   | adopt    | An unverifiable `done_when` is a real gap — the manifest injection can silently be absent. | TASK-4 `success_signal` updated from N/A to a concrete test: constructs combined instructions with real `render_skill_manifest()` call and asserts `<available_skills>` is present. |
| CD-m-1   | adopt    | The guardrail is user-facing JSON output; a test covering it is worth having. | TASK-2 `success_signal` updated from N/A to a test asserting `size_warning` appears when `skill_registry` ≥ 30. |
| CD-m-2   | adopt    | Off-by-one confuses execution. | TASK-6 `done_when` corrected from "lines 129, 146" to "lines 129, 145". |
| PO-m-1   | adopt    | The magic number warrants a brief inline note. | Behavioral Constraints updated to add "(Threshold 30 ≈ prompt-budget ceiling for skill listing; tunable.)" |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev retire-skill-search`

## Delivery Summary — 2026-05-14

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `render_skill_manifest()` all-discoverable; user-dir included; shadow override correct | ✓ pass |
| TASK-2 | `skill_search` deleted; `skill_manage` docstring clean; `size_warning` on create/install when ≥ 30 | ✓ pass |
| TASK-3 | `co_cli/skills/index.py` deleted; SkillIndex purged from deps/bootstrap/lifecycle/main | ✓ pass |
| TASK-4 | `run_session_review()` and `maybe_run_curator()` prepend manifest to instructions; test asserts `<available_skills>` in combined string | ✓ pass |
| TASK-5 | zero `skill_search` matches in all 5 doc/rule files; manifest-scan descriptions in place | ✓ pass |
| TASK-6 | `test_flow_skill_search.py` and `test_flow_skill_index.py` deleted; delegation `not in` assertions added; `skill_view` replaces inline `skill_search` in test | ✓ pass |

**Tests:** scoped — 97 passed, 0 failed (test_flow_skill_manifest, test_flow_session_review, test_flow_skill_curator, test_flow_skills_manage, test_flow_skills_tools, test_flow_fork_deps)
**Doc Sync:** fixed — skill.md (removed skill_search section, manifest all-discoverable, reload hook, Files table); bootstrap.md (Step 9c removed); memory.md (SkillIndex refs removed); tools.md (skill_search removed, count 38→37, stale file path corrected); 01-system.md (Skills section updated)

**Overall: DELIVERED**
All tasks complete. `skill_search` and `SkillIndex` fully retired; manifest now covers all discoverable skills (bundled + user-installed); subagents discover skills via injected manifest; zero stale references in source or specs.
