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

1. **Family key:** for a tool with `integration` set, the family is the segment before the first
   `_` (`google_calendar` → `google`, `google_gmail` → `google`, `google_drive` → `google`;
   `context7` → `context7`). Tools with `integration is None` fall into a sentinel "general" family.
   This makes all `google_*` integrations collapse into one family **by construction** — adding a new
   Google integration needs no code change.
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

### TASK-1 — group deferred stubs by integration family
- **files:** `co_cli/tools/deferred_prompt.py`, `tests/test_flow_deferred_tool_stubs.py`
- **prerequisites:** none
- **done_when:** a behavioral test builds a `tool_index` containing the three distinct Google
  integrations plus a `None`-integration native tool plus one MCP tool (e.g. `context7`), calls
  `build_deferred_tool_awareness_prompt`, and asserts (a) a single `Google Workspace` sub-header
  precedes all google_*-integration stubs, (b) all google stub lines fall under it, (c) the
  `None`-integration tool renders under the general (no-sub-header) section, (d) the MCP tool renders
  under a non-empty fallback family sub-header (title-cased family key — CD-m-1), (e) output is
  **deterministic** — calling twice yields byte-identical strings (CD-m-1), and (f) the full existing
  suite in `tests/test_flow_deferred_tool_stubs.py` still passes (completeness, no-ALWAYS-leak,
  set==deferred, one-line cap, empty contract, search_tools directive).
- **success_signal:** a small model sees the deferred Google tools presented as one labeled
  "Google Workspace" cluster instead of 7 interleaved loose lines.

### TASK-2 — Google docstring-correctness + sibling steers
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

1. **Family-key derivation → prefix-before-first-`_` + label map (no schema change).** Complete-by-
   construction (new `google_*` auto-joins). It is a mild heuristic (a hypothetical `home_assistant`
   would key as `home`), but co has only `google_*` + MCP integrations today, so it is safe now;
   revisit with an explicit `integration_family` field on `@agent_tool`/`ToolInfo` only if a
   non-`google` multi-segment native integration is added.
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
