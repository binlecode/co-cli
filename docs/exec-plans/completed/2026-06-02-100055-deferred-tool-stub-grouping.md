# deferred-tool-stub-grouping

> Small-model Google Workspace surface: group the DEFERRED tool stubs by integration
> family in the per-turn awareness prompt, and land the Google docstring-correctness +
> sibling-steer items moved out of `tool-surface-small-model-audit` (Task 4/5).

## Context

co-cli's small-model surfacing for external services is already ahead of the peers surveyed
(`docs/reference/RESEARCH-tools-peers-tiers.md` Part 6): Google tools are `DEFERRED` +
`requires_config="google_credentials_path"` + `check_fn=_google_available` (double-gated), so a
weak local model sees only a one-line stub per Google tool — and nothing when Google is
unconfigured. Both hermes and openclaw gate external services to binary present/absent with no
deferred tier; co's deferred tier is the differentiator.

The remaining small-model weakness is **presentation**: `build_deferred_tool_awareness_prompt`
(`co_cli/tools/deferred_prompt.py`) emits a single flat list of stubs under one header. The function
already sorts by name (`sorted(tool_index)`, `:45`), so the 7 Google tools (calendar ×2, gmail ×3,
drive ×2) are *already adjacent* via the shared `google_` prefix — sitting in a block alongside
`capabilities_check`, `task_list`, `web_research`, and any MCP tools. So the delta this plan buys is
**not adjacency** (largely free today) but an **explicit cluster label** ("Google Workspace") that
gives a small model a named-capability signal beyond mere ordering. The marginal cost is the
sub-header lines only; the marginal benefit is the label — a clean, cheap trade (PO-m-1).

This plan is also the dedicated home for the Google docstring-correctness + sibling-steer items
moved out of `tool-surface-small-model-audit` (Task 4/5) on 2026-06-02 — they touch the same Google
surface this plan reworks, so doing them in two plans would clobber.

### Decisions already made (do NOT relitigate — see RESEARCH Part 6)

- Keep Google at `DEFERRED` visibility + the `requires_config` + `check_fn` double-gate. No tiering change.
- Keep **dedicated monomorphic per-operation** Google tools. Do **not** collapse into a uniform
  `google(action=…)` surface — that reintroduces the action-dispatch hazard removed in Tasks 3a/3b
  and violates `feedback_tool_split_small_model`.
- The soft opt-in layer (hermes `_DEFAULT_OFF_TOOLSETS` style) is **out of scope** — `DEFERRED`
  already reduces the uncredentialed cost to a single stub line.

### Current-state verification (TL, source read 2026-06-02)

- `integration` values are **distinct per service**: `google_calendar` (`calendar.py:86,158`),
  `google_gmail` (`gmail.py:54,95,147`), `google_drive` (`drive.py:66,133`). One "Google Workspace"
  header therefore requires a **family resolver**, not raw `integration` grouping.
- `ToolInfo.integration: str | None` (`deps.py:107`) — native non-integration tools (`web_research`,
  `task_list`, `capabilities_check`) carry `None`; MCP tools carry the server name (e.g. `context7`).
- Caller: `agent/_instructions.py:22-31` (per-turn slot, post-static — mid-session toggles reflected
  next turn). Returns `""` when no deferred tools exist (slot contract relied on at
  `test_empty_set_returns_empty_string`).
- Existing tests (`tests/test_flow_deferred_tool_stubs.py`) parse stub names via
  `^- \`([^\`]+)\`` (`:48`) and assert `set(stub_names) == deferred` + `len == len`
  (`:74-85`). Sub-header lines (not matching `- \`name\``) are additive and do **not** break these —
  but the grouping must preserve the exact `- \`name\`: one-liner` stub-line format.
- Google docstring items confirmed against source in the prior audit Gate-1 (see Tasks below for
  exact file:line); all are wording-only, no signature change.

## Problem & Outcome

**Problem:** (1) DEFERRED stubs are a flat list — a small model gets no signal that the 7 Google
tools are one capability cluster. (2) The Google docstrings carry the defects catalogued in the audit
plan (calendar `days_back` "today onward" misnomer at 3 sites, divergent `days_ahead` defaults
undocumented, missing recurring-events caveat parity, drive `page` stateful-contract vague, gmail
`draft.to` single-recipient, missing gmail/calendar sibling steers).

**Outcome:** the awareness prompt groups deferred stubs under integration-family sub-headers (Google
tools under one "Google Workspace (load before use)" header); the Google docstrings are corrected and
carry reciprocal sibling steers.

**Failure cost:** without (1), a small local model is likelier to mis-select among 7 interleaved
Google stubs or fail to recognize the cluster, raising wrong-tool-call and load-then-abandon rates;
without (2), the misleading `days_back` wording and absent steers actively misdirect tool selection
on the deferred Google surface (where the one-liner stub + docstring are the model's only signal).

## Scope

**In:** `deferred_prompt.py` grouping; the Google docstring + sibling-steer edits; a behavioral test
asserting the Google cluster grouping.

**Out:** tiering changes; any `google(action=…)` consolidation; the soft opt-in layer;
`google_drive_search.max_results` parity (stays deferred, doc-only per audit decision #3); MCP
grouping behavior beyond what falls out of the family resolver; touching ALWAYS tools.

## Behavioral Constraints

- **Complete-by-construction:** grouping derives entirely from the live `tool_index`; no hardcoded
  tool allowlist. A new `google_*` integration auto-joins the Google Workspace cluster.
- **Stub-line format preserved:** each tool still emits exactly one `- \`name\`: one-liner` line
  (or `- \`name\`` when description is empty), single-line, length-capped at `_ONE_LINER_MAX_CHARS`.
- **Empty contract preserved:** no deferred tools → `""`.
- **Prefill budget:** sub-headers add a handful of short lines only; coordinate with the
  `prefill-trim` family. The awareness prompt is a per-turn slot (not the cached static prefix), so
  added lines cost per-turn tokens — keep sub-headers terse.
- **Deterministic ordering:** group order and within-group order are stable (no dict-iteration
  nondeterminism) so the per-turn slot doesn't churn.

## High-Level Design

### TASK-1 — integration-family grouping in `build_deferred_tool_awareness_prompt`

Group emitted stubs by an **integration family** derived from `ToolInfo.integration`:

1. **Family key:** for a **native** tool (`ToolInfo.source != MCP`) with `integration` set, the family
   is the segment before the first `_` (`google_calendar` → `google`, `google_gmail` → `google`,
   `google_drive` → `google`). Tools with `integration is None` fall into a sentinel "general" family.
   This makes all `google_*` integrations collapse into one family **by construction** — adding a new
   Google integration needs no code change. **MCP-sourced tools must NOT be prefix-split:** their
   `integration` is the user-configured server prefix (`co_cli/agent/mcp.py:150`, `prefix or None`),
   an arbitrary string that can itself contain `_` (e.g. a `data_api` server would mis-key to `data`,
   and a `google_*`-prefixed server would be wrongly absorbed into the Google Workspace cluster).
   So for `source == ToolSourceEnum.MCP`, use the **whole `integration` string** as the family key
   (no split). `context7` → family `context7` either way; `data_api` → family `data_api` (intact).
2. **Family label:** a small presentation map `_FAMILY_LABELS = {"google": "Google Workspace", …}`
   resolves the friendly header; unmapped families fall back to a title-cased family key
   (`context7` → "Context7"). This map is **presentation-only** — an unmapped family still groups and
   renders correctly, so it is not a completeness-bearing allowlist (the original docstring's
   no-allowlist contract is about tool *membership*, which stays index-derived).
3. **Render:** one section per family. The "general" family (native non-integration tools) renders
   first under the existing top-line directive (no sub-header, preserving today's look for
   `web_research`/`task_list`/`capabilities_check`); each integration family renders under a sub-header
   line, e.g. `Google Workspace (load before use):`. Within each family, stubs sorted by name; families
   ordered with "general" first then families alphabetically by label (deterministic).
4. **Stub line unchanged:** `_stub_one_liner` and the `- \`name\`: …` / `- \`name\`` format are
   reused verbatim — only the surrounding section structure changes.

Single header directive stays at top (`search_tools` loader instruction) so
`test_search_tools_directive_present` holds. Sub-headers are plain lines, so `_stub_names` regex and
the set/count assertions still pass.

### TASK-2 — Google docstring-correctness + sibling steers (folded from audit Task 4/5)

Pure wording edits, no signature changes (all verified against source in the audit Gate-1):

- **calendar** (`co_cli/tools/google/calendar.py`): document the `days_ahead` rationale in each Args
  line (list = today/this-week overview, default 1; search = forward month hunt, default 30 — do NOT
  align); reword `days_back` away from "today onward" at all **three** sites — list Args `:113`,
  search Args `:188`, search **Caveats** bullet `:184` — to `days_back: How many past days to include
  before today (default 0 = start from today 00:00).`; add the "Recurring events are expanded into
  individual occurrences" caveat to `google_calendar_search` for parity with list (`:110`);
  cross-reference the differing default windows in both list↔search steers (the calendar pair is
  already reciprocal at `:100-101`/`:174-175`).
- **drive** (`co_cli/tools/google/drive.py`): the sequential-fetch contract is already stated in
  *prose* (`:76-79`); the `page` **Args** line `:95` is terse ("Use 1 for first page, 2 for next") —
  reword it to carry the contract inline (`page: 1-based page number (default 1). Must be requested
  sequentially — page N requires page N-1 fetched earlier this session; you cannot jump ahead.`) and
  state the fixed page size of 10 (currently only in prose `:72`). `max_results` parity stays deferred
  (doc-only).
- **gmail** (`co_cli/tools/google/gmail.py`): add the reciprocal sibling steer to
  `google_gmail_search` (`:100`): "For a plain most-recent-inbox overview with no filters, use
  `google_gmail_list`." (list already points to search at `:62`); state comma-separated
  multiple-recipient support on `google_gmail_draft.to` (`:162`).

## Tasks

### ✓ DONE TASK-1 — group deferred stubs by integration family
- **files:** `co_cli/tools/deferred_prompt.py`, `tests/test_flow_deferred_tool_stubs.py`
- **prerequisites:** none
- **done_when:** a behavioral test builds a `tool_index` containing the three distinct Google
  integrations plus a `None`-integration native tool plus one MCP tool (e.g. `context7`), calls
  `build_deferred_tool_awareness_prompt`, and asserts (a) a single `Google Workspace` sub-header
  precedes all google_*-integration stubs, (b) all google stub lines fall under it, (c) the
  `None`-integration tool renders under the general (no-sub-header) section, (d) the MCP tool renders
  under a non-empty fallback family sub-header (title-cased family key — CD-m-1), (d2) an MCP tool
  whose integration prefix contains `_` (e.g. `data_api`) keeps its **whole prefix** as the family —
  it is NOT split to `data` and NOT merged into any native family (TL-g1: source-aware guard),
  (e) output is **deterministic** — calling twice yields byte-identical strings (CD-m-1), and (f) the full existing
  suite in `tests/test_flow_deferred_tool_stubs.py` still passes (completeness, no-ALWAYS-leak,
  set==deferred, one-line cap, empty contract, search_tools directive).
- **success_signal:** a small model sees the deferred Google tools presented as one labeled
  "Google Workspace" cluster instead of 7 interleaved loose lines.

### ✓ DONE TASK-2 — Google docstring-correctness + sibling steers
- **files:** `co_cli/tools/google/calendar.py`, `co_cli/tools/google/drive.py`,
  `co_cli/tools/google/gmail.py`
- **prerequisites:** none (independent of TASK-1; both edit the Google surface but different files —
  TASK-1 touches `deferred_prompt.py`, TASK-2 the tool modules)
- **done_when:** the existing Google tool behavioral tests pass and lint is clean after the edits,
  and these grep-checkable signals all hold (CD-m-3): no "today onward" string remains in
  `calendar.py`; `google_calendar_search` carries the recurring-events caveat; the `drive.py` `page`
  Args line names the sequential-page contract and the fixed page size of 10; `google_gmail_search`
  docstring names `google_gmail_list`; `google_gmail_draft.to` Args names comma-separated recipients.
- **success_signal:** N/A (docstring/wording only — no behavior change).

## Testing

- TASK-1: new behavioral test in `tests/test_flow_deferred_tool_stubs.py` (real `ToolInfo` instances,
  no mocks) for the grouping; the existing 9 tests are the regression guard. Run the file, then a
  scoped run of any prompt-assembly tests.
- TASK-2: existing Google tool tests + lint. No eval (no signature/behavior change).
- No `docs/specs/` in task `files:` — `sync-doc` handles specs post-delivery (the deferred-prompt /
  prompt-assembly spec and the google tool-surface prose).
- Coordinate with the `prefill-trim` family before editing shared docstrings.

## Resolved Decisions (Gate-1 C1)

1. **Family-key derivation → prefix-before-first-`_` for native tools + whole-string for MCP + label
   map (no schema change).** Complete-by-construction (new `google_*` auto-joins). The prefix-split is
   a mild heuristic for *native* integrations (a hypothetical native `home_assistant` would key as
   `home`), acceptable because co has only `google_*` native integrations today; revisit with an
   explicit `integration_family` field on `@agent_tool`/`ToolInfo` only if a non-`google` multi-segment
   native integration is added. **MCP integrations are exempt from the split** (TL Gate-1 amendment
   2026-06-02): MCP `integration` is a user-configured prefix (`mcp.py:150`) that can contain `_`, so
   splitting it would fragment one server (`data_api` → `data`) or wrongly absorb a `google_*`-prefixed
   server into the Google cluster. Gate the split on `ToolInfo.source != ToolSourceEnum.MCP`; MCP tools
   use their whole `integration` string as the family key.
2. **General (no-integration) section → render first with no sub-header.** Preserves today's
   appearance for `web_research`/`task_list`/`capabilities_check` and is terser (fewer per-turn
   tokens) than an explicit "Built-in" header.

---

## Final — Team Lead

Plan approved. Converged at Cycle C1 — Core Dev and PO both returned `Blocking: none`. All 4 adopted
minor refinements applied: Context reworded to credit existing adjacency and frame the benefit as the
explicit cluster label (PO-m-1); TASK-1 `done_when` extended with deterministic-output and MCP
fallback-family assertions (CD-m-1); TASK-2 `done_when` extended with grep-checkable signals for the
drive `page` and gmail `draft.to` edits and the drive framing softened (CD-m-3); calendar steer cite
corrected to `:100-101` (CD-m-2). Open Questions resolved (prefix-split family key + label map;
general section renders first with no sub-header).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev deferred-tool-stub-grouping`

---

## Delivery Summary — 2026-06-02

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | grouping test asserts Google cluster (a), all google stubs under it (b), general section (c), MCP fallback family (d), multi-segment MCP prefix not split (d2), deterministic output (e), full existing suite passes (f) | ✓ pass |
| TASK-2 | google tool tests + lint pass; no "today onward" remains; search recurring-events caveat; drive `page` Args names sequential contract + page-size 10; gmail search names `google_gmail_list`; `draft.to` names comma-separated recipients | ✓ pass |

**Implementation notes:**
- TASK-1 (TL): `build_deferred_tool_awareness_prompt` now buckets deferred stubs by integration family. Added `_family_key` (Gate-1 source-aware guard: native integrations split on first `_` so all `google_*` cluster; MCP integrations keep their whole prefix so `data_api` is never fragmented to `data` and a `google_*`-prefixed MCP server is never absorbed into the native Google family), `_family_label` (`_FAMILY_LABELS` map + title-cased fallback), and `_stub_line`. General (no-integration) tools render first under the top directive with no sub-header; families render under `<label> (load before use):`, ordered alphabetically by label, names sorted within — fully deterministic. Stub-line format and empty-string contract unchanged.
- TASK-2 (Dev-1): docstring/wording only, no signatures touched. calendar `days_back` reworded at all 3 sites away from "today onward"; `days_ahead` rationale documented (list near-term default 1, search forward-month default 30, deliberately unaligned); recurring-events caveat added to search for parity; list↔search steers cross-reference the differing windows. drive `page` Args now carries the session-scoped sequential contract + fixed page-size 10. gmail search gained the reciprocal `google_gmail_list` steer; `draft.to` documents comma-separated recipients.

**Tests:** scoped — 14 passed, 0 failed (`test_flow_deferred_tool_stubs.py` 10 incl. new grouping test; `test_agent_build_task_agent.py` 4). No Google-specific behavioral tests exist (docstring-only edits); lint + grep signals are TASK-2's full done_when and all pass.
**Doc Sync:** fixed — `prompt-assembly.md` (3 edits) and `tools.md` (2 edits) updated to document family grouping; other specs clean.

**Overall: DELIVERED**
Both tasks pass `done_when`, lint clean, scoped tests green, docs synced. The Gate-1 source-aware MCP guard is implemented and covered by assertion (d2).

---

## Implementation Review — 2026-06-02

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | grouping test asserts (a)–(f) incl. d2 source-aware MCP guard + deterministic output | ✓ pass | `deferred_prompt.py:64-66` `_family_key` gates `if info.source == ToolSourceEnum.MCP: return integration` before the native `split("_",1)[0]` — MCP `data_api` never split, `google_*`-prefixed MCP never absorbed; general first no-sub-header `:115-116`; empty contract `:109`; deterministic `sorted(tool_index):99` + `sorted(families,key=_family_label):117`. Caller chain `_instructions.py:22-31` → `orchestrator.py:62`. 10/10 tests pass |
| TASK-2 | grep signals + no signature change | ✓ pass | no "today onward" in `calendar.py` (grep empty); `days_back` docstring matches code `.replace(hour=0,…):126-129,207-208`; recurring-events caveat parity `calendar.py:189`; drive `page` sequential+size-10 matches `_resolve_page_token:20-29` + `pageSize:107`; gmail search names `google_gmail_list:104`; `draft.to` comma-separated valid via RFC-5322 To header `:176`. All 7 signatures unchanged; diff is docstring-text-only |

### Issues Found & Fixed
No issues found. Per-task evidence subagents and a cold adversarial re-review (13 claims, 0 downgraded) confirmed every PASS against source. The one latent edge surfaced — an MCP server named *exactly* `google` would merge into the Google cluster — is benign (deterministic; requires exact-name, not prefix; docstring correctly scopes the guard to prefixed integrations) and not a defect.

### Tests
- Command: `uv run pytest -q`
- Result: 659 passed, 0 failed (1 warning), 318.57s
- Log: `.pytest-logs/*-review-impl.log`

### Behavioral Verification
- `co status`: N/A — no such command in this CLI.
- Real prompt-assembly path (`build_native_toolset(SETTINGS)` → `build_deferred_tool_awareness_prompt`): ✓ general section renders 5 native deferred tools with no sub-header (today's look preserved); deterministic (byte-identical 2nd call).
- `success_signal` verified (TASK-1): with the Google config gate set, all 7 `google_*` tools register and render as one labeled `Google Workspace (load before use):` cluster beneath the general section — a small model sees one capability cluster, not 7 loose lines. TASK-2 `success_signal` N/A (docstring-only).

### Overall: PASS
Both tasks fully implemented and verified against source; full suite green; lint clean; the Gate-1 source-aware MCP guard confirmed by adversarial review and the success_signal verified on the real production path. Ready for Gate 2 → `/ship`.
