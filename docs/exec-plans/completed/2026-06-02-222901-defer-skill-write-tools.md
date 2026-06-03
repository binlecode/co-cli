# Defer rarely-first-turn skill-write tools off the ALWAYS surface

- **Slug:** `defer-skill-write-tools`
- **Created:** 2026-06-02 22:29:01
- **Status:** Gate 1 PASS (2026-06-02) — **Scope A locked**. Ready for `/orchestrate-dev`.
- **Scope:** Move `skill_create` and `skill_delete` from `ALWAYS` to `DEFERRED` visibility; re-pin the ALWAYS schema-budget ceiling. No behavior change to skill writing itself.

### Gate 1 decision (2026-06-02)

Approved at Gate 1 with **Scope A** (defer `skill_create` + `skill_delete` only). Scope B (defer all four write tools) explicitly **not taken** — `skill_patch`/`skill_edit` stay `ALWAYS` to keep the drift-fix path immediate. Scope is now locked; §3 tasks are the implementation contract.

---

## 1. Context & motivation

co-cli targets small local models (default `ollama` / `qwen3.6:35b-a3b-agentic`, `co_cli/config/llm.py`). Production frontier guidance for small/limited-context agents converges on **progressive tool disclosure**: keep a small always-loaded core and defer the rest behind `search_tools`. Anthropic's measured result on their own MCP eval suite is an ~85% surface-token cut **and** a selection-accuracy lift (Opus 4.5: 79.5% → 88.1%) — the accuracy gain matters most for small models, which mis-route when many near-duplicate tool names co-occur.

Measured current ALWAYS surface (cl100k approx, name+description+params):

| tool | ~tokens | role |
|---|---|---|
| `skill_view` | 110 | read skill body — **common, first-turn** |
| `skill_patch` | 244 | surgical drift-fix — **"patch immediately" path** |
| `skill_edit` | 171 | structural overhaul — drift-fix path |
| `skill_create` | 247 | promote procedure to new skill — **deliberate, rare** |
| `skill_delete` | 101 | remove obsolete skill — **deliberate, rare** |

The `skill_*` cluster is 5 of 24 ALWAYS tools — the largest single near-duplicate name family on the hot surface. Trimming the two genuinely-rare writers reduces both surface tokens and name-collision pressure at selection time.

## 2. Issue analysis

### 2.1 What's wrong today
All five `skill_*` tools are `VisibilityPolicyEnum.ALWAYS` (`co_cli/tools/system/skills.py:34, 299, 331, 359, 392`). Two of them — `skill_create` and `skill_delete` — are only ever invoked on **deliberate, user-initiated, non-first-turn** actions:

- `skill_create`: the "offer-to-save after iterative work" flow (`docs/specs/skills.md:236`, rule `06_skill_protocol.md:49,66`) — the agent offers, the user accepts, *then* it writes. A one-turn discovery cost here is invisible.
- `skill_delete`: removing an obsolete skill — rare and deliberate.

They occupy hot-surface budget every turn for capability needed in a vanishing fraction of turns. This is exactly the profile progressive disclosure is designed to defer.

### 2.2 Why NOT defer the whole cluster
- `skill_view` is the **primary skill path** (`docs/specs/skills.md:49`) — agent reads the `<available_skills>` manifest then calls `skill_view`. Must stay `ALWAYS`.
- `skill_patch` / `skill_edit` are the **drift-fix path**, which the protocol rule explicitly frames as *immediate*: "patch immediately via `skill_patch` for surgical fixes" (`06_skill_protocol.md:39`, `docs/specs/skills.md:234`). Deferring these injects a `search_tools` round-trip into a path designed to be latency-free — a real cost on a small model. Keep `ALWAYS`.

So the surgical, defensible cut is **`skill_create` + `skill_delete` only**.

### 2.3 Blast-radius checks (all clear)
- **Dream daemon — UNAFFECTED.** `build_task_agent` (`co_cli/agent/build.py:64-138`) resolves tools by explicit name from `spec.tool_names` and registers each with `agent.tool(fn, requires_approval=False)`. It never consults `VisibilityPolicyEnum`. `SKILL_REVIEW_SPEC` (`co_cli/daemons/dream/_reviewer.py:67-71`) names `skill_create`/`skill_edit`/`skill_patch` directly, so the daemon keeps full access regardless of visibility. Visibility policy only governs the **orchestrator's** `ToolSearchToolset` path.
- **Model awareness — preserved.** `deferred_tool_awareness_prompt` (`co_cli/agent/_instructions.py:22`, `co_cli/tools/deferred_prompt.py`) injects a per-turn name+one-liner stub for every DEFERRED tool. The agent still knows `skill_create`/`skill_delete` exist and what they do; it issues one `search_tools` call to surface them when the user accepts a save/delete.
- **`capabilities_check` — auto-correct.** It enumerates `always_visible_tools` / `deferred_tools` dynamically from `tool_index` (`co_cli/tools/system/capabilities.py:166-175`). The two tools move buckets automatically; no edit needed.
- **Approval-resume — intact.** Both tools are `approval=True`. The resume filter (`co_cli/agent/toolset.py:55-69`) admits a tool when its name is in `resume_tool_names` OR it is `ALWAYS`. A deferred tool that was discovered and approved is in `resume_tool_names`, so the approval-resume turn restores it correctly.
- **Tests — direct-call, no visibility coupling.** `tests/test_flow_skills_manage.py` calls the functions directly and asserts behavior, not visibility. No change required there.

### 2.4 The one guard that WILL react
`tests/test_orchestrator_schema_budget.py` pins the ALWAYS schema-budget bucket:
- `ALWAYS_BUCKET_CEILING = 21_400` chars (current measured total: 20,988).
- `PER_ALWAYS_TOOL_CEILING = 2_300` chars (current max: `file_search` 2,111 — **unaffected**, since `file_search` stays ALWAYS).

Deferring two tools *shrinks* the bucket, so the `<=` assertion still passes — but the test's own header says to "update them whenever an ALWAYS tool's surface intentionally changes." We re-pin the bucket ceiling lower to lock the win and keep the guard meaningful.

## 3. Proposed refactor

Move exactly two decorators from `ALWAYS` → `DEFERRED`, then re-pin the ceiling.

### Task 1 — Defer `skill_create` ✓ DONE
`co_cli/tools/system/skills.py:299`
```python
@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,   # was ALWAYS
    approval=True,
    ...
)
async def skill_create(...):
```
- [ ] Change visibility to `DEFERRED`. Leave `approval=True`, `approval_subject_fn`, docstring untouched.

### Task 2 — Defer `skill_delete` ✓ DONE
`co_cli/tools/system/skills.py:392`
```python
@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,   # was ALWAYS
    approval=True,
    ...
)
async def skill_delete(...):
```
- [ ] Change visibility to `DEFERRED`.

### Task 3 — Re-pin the schema-budget ceiling ✓ DONE
`tests/test_orchestrator_schema_budget.py:32-33`
- [ ] Run the budget test once to read the new measured ALWAYS total (it prints the bucket size on failure; or add a temporary print). Recompute `ALWAYS_BUCKET_CEILING` as `new_total + ~400` headroom (mirror the existing `+~400` convention).
- [ ] Update the `# ALWAYS bucket = ...` comment with the new measured value and note the cause ("skill_create + skill_delete moved to DEFERRED, <slug>").
- [ ] Leave `PER_ALWAYS_TOOL_CEILING` unchanged (max ALWAYS tool is `file_search`, still present).

### Task 4 — Doc sync (sync-doc scope, not a code change) ✓ DONE
- [ ] `docs/specs/skills.md:30` — the row describing the four write tools is visibility-agnostic; verify it still reads correctly. If the spec anywhere implies all skill tools are always-present, add one sentence: `skill_create`/`skill_delete` are deferred (discovered via `search_tools`); `skill_view`/`skill_patch`/`skill_edit` stay always-loaded. (Grep first — do not invent a visibility table.)
- [ ] `.agent_docs/tools.md` — no change; the ALWAYS/DEFERRED definition (line 57) is generic and still accurate.

No change to `06_skill_protocol.md`: it references `skill_patch`/`skill_edit` (kept ALWAYS) for the immediate drift-fix path, and `skill_create` only in the deliberate offer-to-save path where a discovery round-trip is acceptable.

## 4. Verification

- [ ] `scripts/quality-gate.sh lint`
- [ ] `uv run pytest tests/test_orchestrator_schema_budget.py tests/test_flow_skills_manage.py tests/test_flow_deferred_tool_stubs.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-defer-skill.log` — budget guard re-pinned and passing; skill behavior unchanged; deferred-stub formatting still valid (now includes 2 more stubs).
- [ ] Behavioral smoke (manual or eval): in `co chat`, confirm (a) `skill_create`/`skill_delete` are absent from the default tool manifest, (b) their one-liner stubs appear via the deferred-awareness prompt, (c) asking the agent to save a new skill triggers a `search_tools` → `skill_create` → approval sequence that completes.
- [ ] Full gate before ship: `scripts/quality-gate.sh full`.

## 5. Expected outcome

- ALWAYS surface: 24 → **22 tools**; `skill_*` family on the hot surface: 5 → **3 names**.
- Surface savings: ~350 tokens/turn (cl100k approx), cached — modest in tokens, the real win is fewer near-duplicate `skill_*` names competing at small-model selection.
- DEFERRED surface: 11 → 13 tools, surfaced on demand.
- Zero behavior change to skill writing, the dream daemon, or approval.

## 6. Alternatives considered

- **Option B — defer all four write tools** (`create`/`edit`/`patch`/`delete`, keep only `skill_view` ALWAYS). Closer to Anthropic's "keep 3–5 most-used always" guidance and saves ~760 tokens. **Rejected for now:** it puts a `search_tools` round-trip on the drift-fix path that the skill protocol explicitly designs to be immediate. Revisit only if an eval shows the drift-fix path is rare in practice.
- **Aggressive 3–5 ALWAYS target** (per Anthropic's literal recommendation). **Rejected:** that figure is calibrated for 55K-token, hundreds-of-tools MCP sprawl. co-cli runs a curated 38-tool / ~8.5K set; forcing core tools (`file_read`, `shell_exec`, `memory_search`) through discovery would hurt small-model latency with no context-rot problem to solve.

## 7. Out of scope (separate work)

- **Tool-use examples on heavy schemas** (`shell_exec`, `file_patch`, `file_search`). Anthropic reports 72%→90% parameter-accuracy gains — but via the API-level `input_examples` field, which the **Ollama** backend does not support. For co-cli this would be docstring-text only, adds tokens, and its value on small models is unverified. Requires a dedicated eval before any change; do not bundle here. (Tracks with the eval-real-world-data and no-eval-driven-API conventions.)
- **`web_search`/`web_fetch` call-time failure gap** (no `requires_config` gate) — pre-existing, noted in `.agent_docs/tools.md:17`; not in scope.

## Implementation Review — 2026-06-02

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `skill_create` → DEFERRED | ✓ pass | `co_cli/tools/system/skills.py:299` — `visibility=VisibilityPolicyEnum.DEFERRED`; `approval=True` + `approval_subject_fn` preserved |
| TASK-2 | `skill_delete` → DEFERRED | ✓ pass | `co_cli/tools/system/skills.py:392` — `visibility=VisibilityPolicyEnum.DEFERRED` |
| (unchanged) | `skill_view`/`edit`/`patch` stay ALWAYS | ✓ pass | `skills.py:34, 359, 331` — all `VisibilityPolicyEnum.ALWAYS` |
| TASK-3 | budget ceiling re-pinned, guard green | ✓ pass | `tests/test_orchestrator_schema_budget.py:33` — `ALWAYS_BUCKET_CEILING = 20_200` (measured 19,800); `PER_ALWAYS_TOOL_CEILING` unchanged (max ALWAYS = `file_search`, still present) |
| TASK-4 | spec reflects visibility split | ✓ pass | `docs/specs/skills.md:52` — Path 3 sentence: edit/patch always-loaded; create/delete deferred via `search_tools` + per-turn stub |

### Behavioral evidence (live runtime surface, real bootstrap)
- `build_native_toolset(Settings())`: ALWAYS 24→**22**, DEFERRED 11→**13**.
- `skill_create`/`skill_delete` absent from ALWAYS, present as discovery stubs in `build_deferred_tool_awareness_prompt` (model stays aware, loads via `search_tools`).
- Dream daemon unaffected — `build_task_agent` registers by explicit name (visibility-agnostic); `SKILL_REVIEW_SPEC` names the tools directly.

### Issues Found & Fixed
No issues found. Auto-fix loop empty.

Scope note: `tests/test_flow_deferred_tool_stubs.py` appears in the diff but is **not** a task in this plan — it is the separately-requested clean-tests pass (8→1 test, structural removals). Two other dirty plan files (`vision-input`, `prefill-trim-4`) are pre-existing/coworker edits, left untouched.

### Tests
- Command: `uv run pytest -x -q`
- Result: **649 passed, 0 failed** (1 warning) in 293s
- Log: `.pytest-logs/20260602-225835-review-impl.log`
- Lint: `scripts/quality-gate.sh lint` ✓ clean

### Behavioral Verification
- No `co status` command in this CLI; user-facing surface is the assembled tool manifest, verified via real-bootstrap probe (above). `success_signal` verified: model sees a 22-tool always surface with `skill_create`/`skill_delete` discoverable on demand.
- **End-to-end LLM validation (real `qwen3.6:35b-a3b-agentic`, ollama):** drove two live turns asking the agent to create a skill.
  - Hinted prompt → tool-call sequence `search_tools → skill_create` (discovered + called).
  - Unprompted prompt → `skill_create → search_tools → skill_create → skill_edit`: the model attempts the deferred tool directly (rejected — not loaded), recovers via `search_tools`, then calls it successfully.
  - Confirms the deferral mechanism works end-to-end. Empirically observed cost: the small model may burn one round-trip attempting a deferred tool before searching — direct evidence that keeping `skill_edit`/`skill_patch` ALWAYS (the immediate drift-fix path) was correct.

### Overall: PASS
Scope A implemented exactly as specified; full suite green, lint clean, runtime surface confirms the deferral with discoverability intact and zero behavior change to skill writing or the dream daemon.
