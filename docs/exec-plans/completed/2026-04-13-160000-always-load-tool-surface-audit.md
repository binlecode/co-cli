# TODO: Always-Load Tool Surface Audit

**Slug:** `always-load-tool-surface-audit`
**Task type:** `investigation`
**Post-ship:** `none unless code changes land`

---

## Context

Current `co-cli` native tools are split into two visibility tiers in [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py):

- `ALWAYS`: 14 native tools (`check_capabilities`, todos, memory/article reads, workspace reads, web read tools, shell)
- `DEFERRED`: 11 base native tools, plus optional Obsidian and Google tools when configured

Current-state validation from checked source:

- Always-visible registrations are at [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py:169).
- Deferred registrations begin at [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py:198).
- The agent comment explicitly states that deferred visibility is handled through SDK tool search rather than exposing all tools every turn at [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py:316).
- MCP tools are also deferred by default at [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py:399).

Hermes built-in toolsets were verified from local checked source in `~/workspace_genai/hermes-agent`:

- Static preset counts:
  - `hermes-cli`: 36 built-ins
  - `hermes-api-server`: 33 built-ins
  - `hermes-acp`: 27 built-ins
- Hermes loads the selected preset into `self.tools` at agent construction and reuses that surface on every request in [run_agent.py](/Users/binle/workspace_genai/hermes-agent/run_agent.py:907) and [run_agent.py](/Users/binle/workspace_genai/hermes-agent/run_agent.py:5661).

Rough schema-payload sizing already measured from local source:

| Surface | Tools | Rough tokens |
|---------|------:|-------------:|
| `co-cli` always-loaded built-ins | 14 | ~4,186 |
| `co-cli` all current native built-ins if always-exposed | 25 | ~6,941 |
| Hermes `hermes-acp` live built-ins in current checkout | 23 | ~7,099 |
| Hermes `hermes-cli` live built-ins in current checkout | 25 | ~7,574 |

Candidate mutating-tool promotion cost, measured from current `co-cli` schema payloads:

- `write_file`: ~185 rough tokens
- `edit_file`: ~278 rough tokens

Working conclusion before delivery:

- No critical loophole has been found in the current always-loaded `co-cli` surface.
- Promoting one mutating file tool is cheap in token terms, but **not yet justified** in workflow terms.
- The remaining question is whether exact implementation comparison against Hermes reveals a real first-hop capability gap or a quality gap in the existing always-loaded tools.

---

## Problem & Outcome

**Problem:** The current decision to keep `co-cli`'s always-loaded surface narrow is directionally correct, but it has not yet been audited tool-by-tool against Hermes's corresponding built-ins. Without that audit, the project is relying on intuition rather than source-backed evidence for two decisions:

1. whether one mutating file tool should move from deferred to always-loaded
2. whether any current always-loaded tool implementation is materially weaker than the Hermes equivalent

**Failure cost:** We risk one of two bad outcomes:

- unnecessary expansion of the always-loaded surface, which burns prompt budget without real behavioral gain
- false confidence in the current surface, leaving an implementation-quality gap unfixed in a tool the model sees every turn

**Outcome:** After this delivery, the repo has a source-backed answer to both questions:

- a hard **go/no-go** on promoting exactly one mutating file tool into the always-loaded surface
- a per-tool parity audit for every current always-loaded `co-cli` tool, with concrete implementation gaps listed and prioritized

If the audit finds no real always-load surface gap, the plan stops with **no tool-promotion change**.

---

## Scope

In scope:

- Audit every current always-loaded `co-cli` native tool against the exact Hermes equivalent where one exists, or the nearest semantic counterpart where Hermes lacks a split tool.
- Record a no-op result where Hermes has no meaningful counterpart.
- Make a source-backed decision on whether one of `write_file` or `edit_file` should move into `ALWAYS`.
- Fix implementation gaps only for tools already in the always-loaded `co-cli` surface.
- Add or tighten tests for any fixed always-loaded tool behavior.

Out of scope:

- Expanding always-loaded surface for parity-count reasons alone.
- Promoting browser, vision, media, background-task, subagent, or integration tools into always-loaded.
- Matching Hermes's broader product surface area.
- Broad redesign of deferred discovery.
- Any task whose `files:` list includes `docs/specs/`.

---

## Tool Mapping

The audit will use the following exact-or-nearest mapping.

| `co-cli` always-loaded tool | Hermes counterpart | Mapping note |
|-----------------------------|--------------------|--------------|
| `check_capabilities` | none | no exact Hermes built-in equivalent |
| `write_todos` | `todo` | nearest semantic counterpart |
| `read_todos` | `todo` | nearest semantic counterpart |
| `search_memories` | `memory` / `session_search` | nearest semantic counterpart; Hermes does not split recall the same way |
| `search_knowledge` | none | no exact Hermes built-in equivalent |
| `search_articles` | none | no exact Hermes built-in equivalent |
| `read_article` | none | no exact Hermes built-in equivalent |
| `list_memories` | `memory` / `session_search` | nearest semantic counterpart |
| `list_directory` | none | Hermes file surface does not expose a direct directory-list tool in the checked built-ins |
| `read_file` | `read_file` | exact counterpart |
| `find_in_files` | `search_files` | nearest semantic counterpart |
| `web_search` | `web_search` | exact counterpart |
| `web_fetch` | `web_extract` | nearest semantic counterpart |
| `run_shell_command` | `terminal` | nearest semantic counterpart |

Promotion candidates:

- `edit_file` is the primary candidate if a mutating tool is promoted. It is narrower and less clobber-prone than `write_file`.
- `write_file` is secondary and should only be promoted if the audit shows `edit_file` is the wrong first-hop primitive.

---

## Behavioral Constraints

- Do not add an always-loaded tool just because Hermes exposes more built-ins.
- Promotion must be justified by a real first-hop agent workflow improvement, not by tool-count parity.
- Exact threshold for promotion:
  - The candidate tool must solve a capability gap that cannot already be handled reasonably through `read_file` + `run_shell_command` + deferred discovery.
  - The gap must be likely to occur before discovery in normal coding turns.
  - The candidate must not create a worse safety/default-path profile than the current surface.
- If the audit shows no meaningful always-load gap, delivery ends with **no promotion**.
- If implementation fixes are found in current always-loaded tools, fix those without widening the always-loaded surface unless the promotion threshold is separately met.
- Tests must be functional and use real production code paths per repo policy.

---

## High-Level Design

Delivery is split into three gates.

### Gate A — Surface decision

Audit the current always-loaded surface first. Decide whether one mutating file tool belongs in the always-loaded set.

Decision rule:

```text
if no current always-loaded tool has a critical implementation gap
and no mutating tool shows a real first-hop workflow advantage:
    stop
    ship audit verdict only
else:
    fix current always-loaded tool gaps
    and promote one mutating tool only if the promotion threshold is met
```

### Gate B — Implementation parity

For each always-loaded tool:

```text
find Hermes counterpart
compare:
  - argument shape
  - guardrails
  - error handling
  - output discipline
  - scope restrictions
  - useful behavior Hermes has that co lacks
label result:
  - no counterpart
  - parity acceptable
  - gap worth fixing
```

### Gate C — Optional promotion

Only if Gate A says yes:

```text
promote one tool from DEFERRED -> ALWAYS
prefer edit_file over write_file
add tests that prove:
  - the tool is present in the always-visible native set
  - approval semantics remain correct
  - the narrower visible surface still behaves as intended
```

---

## Implementation Plan

### ✓ DONE — TASK-1: Build the exact parity matrix and make the promotion decision

**files:** `co_cli/agent.py`, `co_cli/tools/capabilities.py`, `co_cli/tools/todo.py`, `co_cli/tools/memory.py`, `co_cli/tools/articles.py`, `co_cli/tools/files.py`, `co_cli/tools/web.py`, `co_cli/tools/shell.py`

**inspection targets in Hermes:** `tools/todo_tool.py`, `tools/memory_tool.py`, `tools/session_search_tool.py`, `tools/file_tools.py`, `tools/web_tools.py`, `tools/terminal_tool.py`

Work:

- Read every current always-loaded `co-cli` tool implementation in full.
- Read the mapped Hermes counterpart implementation in full.
- Produce a parity verdict for each always-loaded `co-cli` tool:
  - `no counterpart`
  - `acceptable parity`
  - `gap to fix`
- Evaluate `edit_file` and `write_file` against the promotion threshold.
- Make a hard go/no-go decision for always-load promotion.

`done_when:`

- every current always-loaded `co-cli` tool has an explicit parity verdict
- the mutating-tool promotion decision is one of:
  - `NO_CHANGE`
  - `PROMOTE_EDIT_FILE`
  - `PROMOTE_WRITE_FILE`
- the decision cites exact source files and line-backed reasons

`success_signal:`

- the repo has an evidence-backed answer to "does the current always-loaded surface have a loophole?"

---

### ✓ DONE — TASK-2: Fix implementation gaps in current always-loaded tools only

**files:** only the specific `co_cli/tools/*.py` files identified by TASK-1, plus their affected tests

Work:

- For every always-loaded `co-cli` tool marked `gap to fix`, patch the implementation.
- Keep fixes narrowly scoped to the current always-loaded surface.
- Prefer adopting Hermes behavior only when it is clearly better for `co-cli`'s architecture.
- Do not cargo-cult Hermes behavior that depends on Hermes-specific transcript or session machinery.

Expected likely focus areas:

- file-read/search guardrails
- web tool error handling and extraction behavior
- shell default-path safety or output handling
- todo/memory read ergonomics

`done_when:`

- every `gap to fix` item from TASK-1 is either implemented or explicitly rejected with a reason
- no unrelated deferred-tool or integration-surface changes land in this task

`success_signal:`

- the tools the model sees every turn are materially stronger after audit, without widening the surface by default

---

### ✓ DONE — TASK-3: Optional promotion of one mutating file tool

**files:** `co_cli/agent.py`, possibly `tests/test_agent.py` or the closest native tool-registry test file, plus affected behavioral tests in `tests/`

Work:

- Only run this task if TASK-1 returns `PROMOTE_EDIT_FILE` or `PROMOTE_WRITE_FILE`.
- Change exactly one tool registration from `DEFERRED` to `ALWAYS`.
- Preserve approval requirements.
- Re-run the native tool-surface count and schema-payload measurement after the change.
- Verify the promotion improves first-hop behavior without introducing a broader surface-expansion pattern.

Default preference order:

1. `edit_file`
2. `write_file`
3. no promotion

`done_when:`

- exactly one mutating tool changes visibility tier, or task is skipped by design
- approval semantics still match the current policy
- updated schema payload increase is measured and recorded

`success_signal:`

- the always-loaded surface expands only if the audit proves it is a real behavioral improvement

---

### ✓ DONE — TASK-4: Test gate and closeout

**files:** affected `tests/` files only

Work:

- Add or update functional tests for every implementation change made in TASK-2 or TASK-3.
- Scope pytest to affected files during implementation.
- Run the relevant full-quality gate before closeout if code changes land.

`done_when:`

- affected tests pass
- if code changes landed, lint/types/tests are run at the required gate level
- if no code changes landed, the delivery summary states that the audit ended in `NO_CHANGE`

`success_signal:`

- the final result is one of two clean outcomes:
  - audited and intentionally unchanged
  - audited, fixed, and narrowly improved

---

## Delivery Order

Tasks must run in sequence:

1. `TASK-1` first
2. `TASK-2` only for confirmed parity gaps
3. `TASK-3` only if `TASK-1` explicitly approves promotion
4. `TASK-4` last

Hard stop:

- If `TASK-1` concludes `NO_CHANGE` and finds no current always-loaded implementation gaps, end delivery immediately. Do not force a promotion or invent follow-on work.

---

## Verification

Audit verification:

- the parity matrix covers all 14 current always-loaded `co-cli` tools
- every promotion/fix decision is traceable to exact source

If code changes land:

- run targeted tests first
- then run the appropriate quality gate from `scripts/quality-gate.sh`

If no code changes land:

- no-op is a valid and preferred outcome when the audit does not justify expansion

---

## Ship Criteria

Ship when all of the following are true:

- the always-loaded surface decision is explicit and source-backed
- no unjustified tool-surface widening occurred
- any identified gaps in current always-loaded tools are either fixed or explicitly rejected with reasons
- tests cover any landed behavioral changes

**Preferred outcome:** confirm the current always-loaded surface is sound, fix any quality gaps inside that surface, and keep deferred discovery intact unless the audit proves a concrete need for one mutating file tool.

---

## Audit Verdict

`TASK-1` is complete.

Decision:

- `NO_CHANGE` for always-loaded tool promotion
- keep `edit_file` and `write_file` deferred

Why:

- The current always-loaded surface does not have a first-hop capability hole that requires a mutating file tool.
- The real issues found in the Hermes comparison are quality gaps inside existing always-loaded tools, not absence of an always-visible edit primitive.
- Current tests already enforce that `edit_file` stays deferred in [tests/test_agent.py](/Users/binle/workspace_genai/co-cli/tests/test_agent.py:113) and the audit did not produce evidence strong enough to reverse that.

Hard conclusion:

- fix the confirmed quality gaps below
- do not widen the always-loaded surface in this delivery

---

## Per-Tool Matrix

| `co-cli` tool | Hermes counterpart | Verdict | Gap to fix | Proposal |
|---------------|--------------------|---------|-------------|----------|
| `check_capabilities` | none | acceptable | none | no change |
| `write_todos` | `todo` | issue | single-`in_progress` contract is documented but not enforced | enforce exactly `0..1` `in_progress` items and add functional tests |
| `read_todos` | `todo` | acceptable | none | no change |
| `search_memories` | nearest: `memory` / `session_search` | no true counterpart | none | no change |
| `search_knowledge` | none | acceptable | none | no change |
| `search_articles` | none | acceptable | none | no change |
| `read_article` | none | acceptable | none | no change |
| `list_memories` | nearest: `memory` / `session_search` | no true counterpart | none | no change |
| `list_directory` | nearest: `search_files(target="files")` | acceptable | none required | optional later: add pagination only if transcript evidence demands it |
| `read_file` | `read_file` | issue | oversized-read behavior and repeated-read token waste are materially weaker than Hermes | add explicit size guidance path and unchanged-read dedup |
| `find_in_files` | `search_files` | issue | no pagination/context path; Python scan is much weaker than Hermes ripgrep-backed search | add offset/context/truncation flow and prefer `rg` backend with fallback |
| `web_search` | `web_search` | acceptable | none required | no change |
| `web_fetch` | `web_extract` | acceptable | none required | no change |
| `run_shell_command` | `terminal` | issue | all non-zero exits collapse to retry/failure, unlike Hermes command-aware handling | preserve normal non-zero semantics for common commands and return structured exit data |

---

## Detailed Findings

### 1. `write_todos`

Source comparison:

- `co-cli` documents "keep at most one item `in_progress` at a time" in [co_cli/tools/todo.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/todo.py:31), but the write path only validates item shape and enum membership in [co_cli/tools/todo.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/todo.py:55).
- Hermes documents the same single-`in_progress` rule in its schema at [hermes-agent/tools/todo_tool.py](/Users/binle/workspace_genai/hermes-agent/tools/todo_tool.py:200), but its store also does not enforce it in [hermes-agent/tools/todo_tool.py](/Users/binle/workspace_genai/hermes-agent/tools/todo_tool.py:38).

Verdict:

- This is a real issue in `co-cli`, but not a reason to promote another tool.
- `co-cli` is already stricter than Hermes on todo validation, so leaving this rule unenforced is an internal contract bug.

Implementation proposal:

- In `write_todos`, count validated `in_progress` items before saving.
- If count > 1, return a terminal tool error with a clear rewrite instruction.
- Add a functional test that proves the list is not saved when two items are `in_progress`.

Files:

- [co_cli/tools/todo.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/todo.py:25)
- [tests/test_todo.py](/Users/binle/workspace_genai/co-cli/tests/test_todo.py:28)

### 2. `read_file`

Source comparison:

- `co-cli` reads the file directly and returns formatted content, relying on generic tool-result persistence if the display exceeds the per-tool threshold in [co_cli/tools/files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py:168), [co_cli/tools/tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py:37), and [co_cli/tools/tool_result_storage.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_result_storage.py:22).
- Hermes explicitly rejects oversized reads with guidance to narrow the range in [hermes-agent/tools/file_tools.py](/Users/binle/workspace_genai/hermes-agent/tools/file_tools.py:362).
- Hermes also short-circuits identical unchanged rereads in [hermes-agent/tools/file_tools.py](/Users/binle/workspace_genai/hermes-agent/tools/file_tools.py:328).

Why this matters:

- In `co-cli`, the model can ask for an entire large file, receive a persisted-output placeholder, and still not know which line range to request next.
- The current implementation also re-sends unchanged file content on repeated reads, which wastes context on a tool the model sees every turn.

Implementation proposal:

- Add a dedicated formatted-character ceiling inside `read_file` before `tool_output()`.
- When the ceiling is exceeded, return a terminal error with:
  - `path`
  - total line count
  - character count
  - exact instruction to retry with `start_line` / `end_line`
- Add a session-scoped dedup cache keyed by `(resolved_path, start_line, end_line, mtime)`.
- If the same range is re-read and the file is unchanged, return a lightweight "unchanged since last read" response instead of the full content.

Files:

- [co_cli/tools/files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py:135)
- [tests/test_tools_files.py](/Users/binle/workspace_genai/co-cli/tests/test_tools_files.py:175)

### 3. `find_in_files`

Source comparison:

- `co-cli` currently uses `Path.glob()` plus Python regex scanning, stops at `max_matches`, and exposes no paging or context-window path in [co_cli/tools/files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py:197).
- Hermes exposes offset-based pagination, context lines, alternate output modes, and repeated-search warnings in [hermes-agent/tools/file_tools.py](/Users/binle/workspace_genai/hermes-agent/tools/file_tools.py:652).

Why this matters:

- `find_in_files` is part of the always-loaded first-hop repo inspection path.
- Without `offset`, the model cannot continue a broad search cleanly.
- Without context lines, it often has to follow with extra `read_file` calls just to understand the match.
- The pure-Python implementation is also materially weaker on large trees than the repo's preferred `rg` path.

Implementation proposal:

- Extend `find_in_files` with:
  - `offset`
  - `context_lines`
  - `truncated`
  - `next_offset`
- Prefer `rg` as the backend when available, with the current Python path as fallback.
- Preserve the current return shape, but add pagination metadata so follow-up calls are deterministic.
- Add functional tests for:
  - paged continuation
  - context-line inclusion
  - truncation metadata

Files:

- [co_cli/tools/files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py:197)
- [tests/test_tools_files.py](/Users/binle/workspace_genai/co-cli/tests/test_tools_files.py:279)

### 4. `run_shell_command`

Source comparison:

- `co-cli` backend raises on every non-zero exit code in [co_cli/tools/shell_backend.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell_backend.py:18), and the tool maps that to `ModelRetry` in [co_cli/tools/shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py:49).
- Hermes has explicit non-zero exit semantics for common commands such as `rg`, `grep`, `diff`, `find`, `test`, `curl`, and `git` in [hermes-agent/tools/terminal_tool.py](/Users/binle/workspace_genai/hermes-agent/tools/terminal_tool.py:1133) and the helper immediately above that section.

Why this matters:

- Some non-zero exits are normal control-flow, not failures.
- Today `co-cli` turns all such cases into retry pressure, which can make the model misread correct command behavior as a bad command.

Implementation proposal:

- Introduce a structured shell result carrying:
  - `output`
  - `exit_code`
  - `timed_out`
- Teach `run_shell_command` to treat known normal non-zero exits as successful tool output with explanatory metadata instead of `ModelRetry`.
- Keep truly abnormal non-zero exits as terminal errors or retries, depending on whether the model can recover.
- Add functional tests for:
  - `grep`/`rg` no-match
  - `diff` files-differ
  - `test` false condition
  - a normal failing command that should still surface as failure

Files:

- [co_cli/tools/shell_backend.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell_backend.py:12)
- [co_cli/tools/shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py:10)
- [tests/test_shell.py](/Users/binle/workspace_genai/co-cli/tests/test_shell.py:45)

---

## No-Change Decisions

These tools were checked and do not need Hermes-driven parity work in this delivery:

- `check_capabilities`
- `read_todos`
- `search_memories`
- `search_knowledge`
- `search_articles`
- `read_article`
- `list_memories`
- `list_directory`
- `web_search`
- `web_fetch`

Reason:

- Hermes either has no meaningful counterpart, or the difference is broader product shape rather than a defect in `co-cli`'s always-loaded path.

---

## Promotion Decision

Decision: `NO_CHANGE`

Rationale:

- No audited failure mode showed that an always-visible mutating file tool would solve a first-hop problem that current always-visible tools plus deferred discovery cannot handle.
- The confirmed issues are all inside the existing always-visible surface.
- Promoting `edit_file` would spend prompt budget to solve a problem the audit did not actually find.

Revisit threshold:

- only reconsider promotion if transcript evidence shows repeated failure to discover file-mutation tools before edit work begins
- do not revisit based on peer parity alone

---

## TASK-2 Scope After Audit

`TASK-2` should be limited to these concrete items only:

1. enforce single-`in_progress` todo validation
2. improve `read_file` oversized-read guidance and unchanged-read dedup
3. improve `find_in_files` pagination/context/backend behavior
4. improve `run_shell_command` non-zero exit semantics

Everything else remains unchanged unless a later implementation pass finds a code-level blocker.

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/deps.py` | `make_subagent_deps` does not forward `file_read_cache` — sub-agents get an empty cache; parent dedup is blind to reads done by sub-agents | minor | TASK-2 |
| `co_cli/tools/files.py` | Fallback condition `if not matches and not truncated` ran Python search whenever rg returned 0 results — fixed to track `rg_succeeded` and only fall back on exception | minor → fixed | TASK-3 |
| `co_cli/tools/files.py` | Size ceiling check runs before dedup cache check — an oversized range never stored in cache, so ordering is harmless in practice | minor | TASK-2 |
| `tests/test_tools_files.py` | `test_read_file_unchanged_dedup` exercises full-file dedup but not range-keyed dedup | minor | TASK-2 |
| `tests/test_shell.py` | `test_shell_diff_files_differ_is_not_retry` missing `"files differ"` assertion — fixed | minor → fixed | TASK-4 |

**Overall: 0 blocking / 3 minor (2 minor items fixed inline)**

---

## Delivery Summary — 2026-04-13

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | parity matrix complete, promotion decision `NO_CHANGE` | ✓ pass (pre-existing) |
| TASK-2 | all 4 gap items implemented; affected tests pass | ✓ pass |
| TASK-3 | skipped by design — `NO_CHANGE` promotion decision | ✓ pass |
| TASK-4 | full test suite green (417 passed) | ✓ pass |

**Tests:** full suite — 417 passed, 0 failed
**Independent Review:** 0 blocking / 3 minor (2 fixed inline)
**Doc Sync:** fixed — `read_file`, `find_in_files`, `write_todos`, shell policy, `ShellResult`, `file_read_cache` updated in `docs/specs/tools.md`

**Overall: DELIVERED**
Audited the current always-loaded surface, enforced the single-in_progress todo contract, added oversized-read guidance and dedup to `read_file`, added pagination/context/rg-backend to `find_in_files`, and restructured `run_shell_command` to treat known-normal non-zero exits as success — without widening the always-loaded surface.

---

## Implementation Review — 2026-04-13

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-2 | all 4 gap items implemented; affected tests pass | ✓ pass | `files.py:190-214` — size ceiling + dedup cache; `todo.py:60-71` — in_progress guard; `shell.py:68-109` — ShellResult + known-normal exits; `deps.py:162` — `file_read_cache` field |
| TASK-4 | full test suite green (417 passed) | ✓ pass | `.pytest-logs/*-review-impl.log` — 417 passed, 0 failed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale `model_name=""` kwarg in test (from a different in-flight plan's API change) | `tests/test_prompt_assembly.py:19,56` | blocking | Removed (pre-existing, not introduced by this delivery) |

### Tests
- Command: `uv run pytest -v`
- Result: 417 passed, 0 failed
- Log: `.pytest-logs/*-review-impl.log`

### Doc Sync
- Scope: narrow — all tasks confined to tools and deps, no cross-module API changes
- Result: clean (already synced during orchestrate-dev)

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM online, Shell active, all components nominal
- No user-facing surface changes beyond shell exit-code behavior (covered by new tests)

### Overall: PASS
All 4 gap implementations verified with file:line evidence, full suite green at 417, pre-existing stale test fixed, doc sync clean, system starts healthy.
