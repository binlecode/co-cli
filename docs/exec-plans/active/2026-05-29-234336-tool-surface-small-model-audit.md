# tool-surface-small-model-audit

> **Status: draft plan (not yet Gate-1 approved).** Captures the full output of the
> cross-tool small-model surface audit — **63 findings as originally audited** (5 critical,
> 27 high, 31 low), with a concrete fix per finding. `file_search` is already fixed and is the
> reference design — excluded here. Live total is now **61** (5 critical · 26 high · 30 low):
> `knowledge_analyze` was deleted, voiding its Task 2 LOW + one Task 4 HIGH. The original count
> also predates the obsidian-surface removal (Task 1), so 61 is an upper bound.

## Context

After reshaping `file_search` into a presence-based, monomorphic surface (one meaning per
param, defaults stated inline, no overloaded/dead/conditional params), we audited every other
native tool against the same rubric. The audit fanned out one reviewer per tool group and
synthesized a prioritized gap report. This plan is the durable record of that audit and the
execution backlog.

**Relationship to the `prefill-trim` family:** prefill-trim children 2/3 trim docstring/schema
*size* (token budget); this plan fixes surface *correctness* for small models. They touch the
same files (tool docstrings) but optimize different axes — coordinate edits to avoid clobbering.
Wording fixes here should be written tight so they don't re-inflate the prefill budget.

### Governing principles (the rubric — derived from the `file_search` redesign)

1. **Monomorphic split** — no model-visible `action`/`mode`/`target` discriminator with disjoint
   param sets. Split into per-operation tools. (co's documented small-model doctrine:
   `feedback_tool_split_small_model`.)
2. **No overloaded params** — a parameter must not change meaning based on another arg's value.
3. **No dead/conditional params on the surface** — a param that is silently ignored, or errors,
   depending on another arg. Remove it (split) or, if unsplit, state the conditionality inline.
4. **State every optional default inline** — value *and* meaning of the default, in the param's
   own Args line (Hermes practice), not only in prose above.
5. **No dead/parity params** — remove inert args (Hermes-parity stubs); never expose them.
6. **Cross-family naming/semantic alignment** — one name + one contract per concept.
7. **Sibling disambiguation steers** — every near-duplicate pair carries reciprocal
   when-to-use / when-NOT-to-use lines.

### Verdict

Leaf read/view tools (`file_read`, `memory_view`, `session_view`, `obsidian_read`,
`google_drive_read`, `task_status`, `task_cancel`, `todo_read`, `capabilities_check`) are clean
monomorphic primitives. Damage concentrates in three `action`/`mode`-discriminated write tools
(`memory_manage`, `skill_manage`, `file_patch`) plus systemic doc gaps across the
`google_*`/`obsidian_*` families.

Counts (as originally audited): **5 critical · 27 high · 31 low = 63.** Live after the
`knowledge_analyze` deletion: **5 critical · 26 high · 30 low = 61** (upper bound — also predates
the obsidian-surface removal in Task 1).

---

## Sequencing (ROI / risk)

- **Task 1 — Pattern 5 quick wins (bug-grade, do FIRST): ✓ DONE.** broken cross-references and
  dead params. Tiny, high-certainty, no behavior risk. (`skill_view.file_path` removal,
  `skill_view.name` plugin note, V4A Move error. The `obsidian_list` item was resolved by removing
  the obsidian tool surface entirely.)
- **Task 2 — Pattern 2 sweep (defaults-inline): ✓ DONE.** 9 docstring-only Args-line edits across
  6 tools. Low risk, no signature changes.
- **Task 3 — the three monomorphic splits (CRITICAL):** `memory_manage`, `skill_manage`,
  `file_patch`. Largest correctness win; mirrors the shipped `file_search` split. Signature +
  registry + tests + spec. Internal `_handle_*`/`_skill_*` helpers already exist → thin wrappers.
- **Task 4 — cross-family naming/semantic alignment:** working-dir name+contract, calendar
  defaults, `max_requests` sentinel wording.
- **Task 5 — sibling disambiguation steers:** reciprocal when-to-use lines for the three
  near-duplicate pairs.

Each task is independently shippable. Task 3 is the only one touching the tool registry and the
`docs/specs/tools.md` count.

---

## Task 1 — Pattern 5: dead params & broken references (do first)

### `skill_view.file_path` — HIGH — dead parity param — ✓ DONE
- **File:** `co_cli/tools/system/skills.py:41, 63-64`
- **Issue:** any non-None `file_path` returns a tool_error; `linked_files` is always `{}`. Pure
  Hermes-parity stub. A small model sees a real optional param and will populate it, then error.
- **Fix:** remove the `file_path` parameter → `skill_view(ctx, name: str)`. Drop the
  `file_path` Args line and the `linked_files` paragraphs. Replace docstring body with: "Load a
  skill's full SKILL.md content. Call before `skill_manage(action='edit'|'patch')` to read
  current content — don't edit blind."
- **Delivered:** removed `file_path` param, the dead guard, and the `linked_files={}` return stub;
  reworded the module + tool docstrings. Updated `docs/specs/skills.md` (signature heading + prose).
  Removed `test_skill_view_file_path_unsupported` and the stale `linked_files` assertion. Also
  corrected a pre-existing spec bug: three verification rows pointed at the nonexistent
  `tests/test_flow_skills_tools.py` → `tests/test_flow_skills_manage.py`.

### `skill_view.name` — LOW — dead `plugin:skill` note — ✓ DONE
- **File:** `co_cli/tools/system/skills.py:53-54` (code `:57`)
- **Issue:** the `plugin:skill` qualified-name note is dead in co (no plugin namespace; prefix is
  just stripped). Implies plugins exist.
- **Fix:** Args line → `name: Skill name, e.g. "deep-research" (the filename_stem from the skill
  manifest).` Drop the `plugin:skill` sentence.
- **Delivered:** full removal (not doc-only) — reworded the Args line AND deleted the inert
  `name.split(":")` prefix-strip and the now-pointless `lookup` alias (inlined to `name`).
  Plugin-prefixed names now return a clean unknown-skill error instead of silently stripping.
  Removed `test_skill_view_plugin_qualified_name` and the spec's "resolves plugin-qualified names"
  verification row; updated the spec prose to address-by-`filename_stem`.

### `file_patch` (V4A) — LOW — silent Move no-op — ✓ DONE (re-diagnosed)
- **File:** `co_cli/tools/files/_v4a.py` (parser); `co_cli/tools/files/write.py:416-417`
- **Original (incorrect) diagnosis:** "`_apply_v4a_patch` silently `continue`s on MOVE ops." This
  was wrong: `OperationType` has no `MOVE` member and `parse_v4a_patch` only ever produces
  UPDATE/ADD/DELETE, so the `else: continue` branch was **unreachable dead code**, not a runtime
  no-op.
- **Actual issue:** a `*** Move File:` directive matched no directive regex in the parser and was
  silently absorbed as a hunk *context line* (`_v4a.py:106-117`) — the real silent partial-apply.
- **Fix (delivered):** parser now rejects any unrecognized `*** Xxx File:` directive (catches Move
  and typo'd directives) with an explicit parse error → the model gets feedback. The dead
  `else: continue` in `write.py` was converted to an explicit error return (defensive; no longer a
  silent skip). Added `test_file_patch_v4a_mode_rejects_unsupported_move_directive`.

---

## Task 2 — Pattern 2: state every optional default inline (docstring-only sweep) — ✓ DONE

All edits are reword-the-Args-line only; no signature changes.

> **Delivered:** 9 Args-line rewrites across 6 files — `web_search.domains`, `web_research.domains`,
> `shell_exec.workdir`, `memory_search.query`/`kinds`, `session_search.query`/`limit`,
> `google_drive_search.page`, and `google_gmail_list`/`google_gmail_search.max_results`. For
> `memory_search`, also trimmed the now-redundant body paragraph (kinds-filter + FTS5-syntax prose)
> since the rewritten Args lines carry it — budget-neutral. The `knowledge_analyze.inputs` row was
> dropped (tool deleted; see note below). Lint clean; 111 scoped behavioral tests pass, no
> docstring-asserting test broke.

> Note: the `knowledge_analyze.inputs` finding was dropped — `knowledge_analyze` was deleted
> wholesale (no behavioral consumer: the dream daemon synthesizes via its own `memory_reviewer`,
> and the foreground DEFERRED tool was effectively unused). Its `max_requests` item in Task 4 is
> likewise void.

| Tool | Param | File:line | Fix (new Args line) |
|---|---|---|---|
| `web_search` | `domains` | `web/search.py:311` | `domains: Restrict results to these domains, e.g. ["github.com"] (default None = search the entire web).` |
| `web_research` | `domains` | `agents/delegation.py:82` | `domains: Restrict the agent's web searches to these domains (default None = search the entire web).` |
| `shell_exec` | `workdir` | `shell/execute.py:50` | append `Default None = the workspace root.` (see also Task 4 rename) |
| `memory_search` | `query` | `memory/recall.py:190` | `query: FTS5 keyword query. Default "" lists the most recent `limit` artifacts (browse mode); non-empty runs BM25 search. Syntax: OR, NOT, "phrase", prefix*.` |
| `memory_search` | `kinds` | `memory/recall.py:191` | `kinds: Filter to these artifact kinds — any of user, rule, article, note. Default None = all kinds.` Drop the unenforced "Up to 3". |
| `session_search` | `query` | `session/recall.py:142` | `query: FTS5 keyword query. Default "" browses recent `limit` sessions; non-empty runs BM25 chunk-cited search.` |

Related caps to surface in the same sweep (LOW):
- `session_search.limit` (`session/recall.py:143`): note that **keyword search always returns at
  most 3 sessions regardless of `limit`** (hard cap `_SESSIONS_CHANNEL_CAP=3`); `limit` only
  governs browse mode.
- `google_drive_search` (`google/drive.py:71`): page size is fixed at 10 — state it (or expose
  `max_results` for family parity; see Task 4).
- `google_gmail_list` / `google_gmail_search` `max_results` (`gmail.py:71, 119`): replace the
  vague "max ~100" with `max_results: Number of emails to return (default 5; values above 100
  may be slow).`

---

## Task 3 — CRITICAL: the three monomorphic splits

Each split mirrors the shipped `file_search`/memory-recall pattern: surface becomes
per-operation tools with only their real params; internal handlers already exist. Each split
touches: the tool module, `agent/toolset.py` import, `docs/specs/tools.md` (Tool Groups table,
native-tool count, Files table), result markers / display maps if the tool name appears there,
and tests.

### 3a. `memory_manage(action=…)` → split (worst offender)
- **File:** `co_cli/tools/memory/manage.py:41`
- **Critical issues:**
  - `action` discriminator bundles create/append/replace/delete; 5 of 7 params conditional.
  - `name` **overloaded**: new-artifact *title* for create (`save_memory_item(title=…)`, `:122`)
    vs existing *filename_stem* for append/replace/delete (`:78/81/84`). Create→append round-trip
    reuses the title as a stem → FileNotFound trap.
- **Dead/conditional params (HIGH each):** `kind` (create-only `:73-76`), `content` (overloaded
  body/append-text/replacement; ignored for delete; `:101,175`), `section` (replace-only `:81`;
  internal name mismatch `target` `:173`), `source_type` (create-only `:123`; typed free `str`
  but real domain is `SourceTypeEnum`), `source_url` (create-only `:124`).
- **Fix (split):**
  - `memory_create(name_title, content, kind, source_type='manual', source_url=None)`
  - `memory_append(filename_stem, content)`
  - `memory_replace(filename_stem, section, content)`
  - `memory_delete(filename_stem)`
  - Append/replace/delete already dispatch to separate `_handle_*` fns (`_handle_mutate` covers
    both append and replace) — only the surface is conflated. Type `source_type` as
    `SourceTypeEnum` so invalid values are schema-rejected.
- **Integration touch-points (mandatory — zero-backward-compat removes `memory_manage`):**
  - **Dream `memory_reviewer` (behavioral):** `daemons/dream/_reviewer.py:52-55` grants
    `tool_names=("memory_search", "memory_manage")`; update to the new write tool(s) it actually
    needs (`memory_create`). Reword its prompt `daemons/dream/prompts/memory_review.md:10,12`
    (`create one with memory_manage` → `memory_create`; `on every memory_manage call set
    source_type='session_review'`). Without this the dream daemon can no longer write memory.
  - **Approval-subject fns (behavioral):** `_memory_manage_approval_subject` reads `args["action"]`
    to build `tool:memory_manage:<action>:<name>`. The split has no `action` arg — give each new
    tool its own subject fn (`tool:memory_create:<name_title>`, `tool:memory_append:<stem>`, etc.).
  - **Display map:** `tools/display.py:23` `"memory_manage": "name"` → per-tool entries
    (`memory_create`→`name_title`, append/replace/delete→`filename_stem`).
  - **Spec `docs/specs/memory.md` (heavier than `tools.md`):** signature table row (`:105`),
    architecture diagram (`:24,31`), article-accumulation prose (`:170,178,243,244`), and the
    `co_cli/tools/memory/` surface line (`:39`).
  - **`deps.py:157` comment** mentions `memory_manage(create|append|replace)` — update names.
- **Fallback if unsplit:** every conditional Args line must state `REQUIRED for action=X; ignored
  otherwise. Default None.` and add an Examples block (create/append/replace/delete one-liners).
- **Delivered (✓ DONE):** split into `memory_create(name_title, content, kind,
  source_type=SourceTypeEnum.MANUAL, source_url=None)`, `memory_append(filename_stem, content)`,
  `memory_replace(filename_stem, section, content)`, `memory_delete(filename_stem)`. `source_type`
  typed as `SourceTypeEnum` (schema-rejects invalid values); per-tool monomorphic params, no
  discriminator, `name` overload resolved (`name_title` vs `filename_stem`). Internal
  `_handle_create`/`_handle_mutate`/`_handle_delete` retained as thin-wrapper targets; trace spans
  renamed `co.memory.{memory_create,memory_mutate,memory_delete}`. Approval subjects now
  per-tool via `_subject_fn(tool_name, arg_key)` (`tool:memory_create:<name_title>`, etc.).
  Integration touch-points all updated: `agent/toolset.py` import, `tools/display.py` (4 entries),
  dream `_reviewer.py` `MEMORY_REVIEW_SPEC.tool_names` + `prompts/memory_review.md`,
  `deps.py` comment, rule prompt `context/rules/07_memory_protocol.md`, `skills/triage.md`.
  Specs synced: `memory.md` (write table → 4 rows, diagram, tier table, surface line, growth-pipeline
  prose, source_type table), `tools.md` (group, count 30→33, Files table), `dream.md` (×3),
  `01-system.md` (×2), `skills.md`, `observability.md`. Evals reworded (`eval_memory.py`,
  `eval_trust_visibility.py`, `eval_domain_review.py`, `_timeouts.py`). Tests rewritten across 6
  files. Lint clean; full suite 654 passed.

### 3b. `skill_manage(action=…)` → split — ✓ DONE
- **File:** `co_cli/tools/system/skills.py:287-350` (decorator `:282-286`; signature `:287-295`;
  docstring `:296-332`; dispatch `:333-350`).
- **Critical issue:** `action` (`:289`, `Literal["create","edit","patch","delete"]`) bundles four
  operations; of 6 params, validity is per-action. create/edit use `content`; patch uses
  `old_string`/`new_string`/`replace_all`; delete uses only `name`.
- **Dead/conditional params (HIGH each):** `content` (create/edit-only — Args `:328`, consumed in
  `_skill_create:123`/`_skill_edit:156`), `old_string` (patch-only — Args `:329`, `_skill_patch:192`),
  `new_string` (patch-only — Args `:330`, `_skill_patch:194`), `replace_all` (patch-only — Args `:331`,
  `_skill_patch:210`; default `False` not named inline).
- **Required-as-optional (HIGH):** `name` defaults to `""` (`:290`) but every action requires a
  valid name (`_NAME_RE` guard `:333`) — a required field presented as optional.
- **Fix (split):**
  - `skill_create(name, content)`
  - `skill_edit(name, content)`
  - `skill_patch(name, old_string, new_string, replace_all=False)`
  - `skill_delete(name)`
  - Internal `_skill_create`/`_skill_edit`/`_skill_patch`/`_skill_delete` helpers already exist
    (`:122`, `:155`, `:185`, `:242`) → thin wrappers, exactly as 3a wrapped `_handle_*`. Make `name`
    required (`name: str`, no default) in all four. The `_NAME_RE` validation currently in the
    `skill_manage` body (`:333-338`) moves into each wrapper (or a shared `_require_valid_name`
    helper) since there is no longer a single dispatch entry. Mirror the existing decorator attrs
    (`visibility=ALWAYS`, `approval=True`) per tool; do **not** add `is_concurrent_safe` — a skill
    write triggers a global `refresh_skills`, so the writes are not independently concurrent-safe.
  - **Wrappers add nothing beyond dispatch.** The existing helpers already own all the side-effects —
    `model_requests_since_skill_review = 0` resets (`:151/181/238`), the `>= 30` `size_warning`
    (`:146`), and the usage-counter calls (`record_create`/`bump_patch`/`forget`). These survive
    untouched inside `_skill_create/_edit/_patch/_delete`; do NOT duplicate them into the wrappers
    (double-bump). `test_skill_manage_resets.py` already covers the reset behavior.
- **Integration touch-points (mandatory — zero-backward-compat removes `skill_manage`):**
  - **`agent/toolset.py` import (`:37`):** `from co_cli.tools.system.skills import skill_manage,
    skill_view` → `skill_create, skill_delete, skill_edit, skill_patch, skill_view`. Registration is
    decorator + import only (no central list), same as 3a.
  - **Approval-subject fns (behavioral):** `_skill_manage_approval_subject` (`skills.py:270-279`)
    reads `args["action"]` to build `tool:skill_manage:<action>:<name>`. The split has no `action`
    arg. Replace with the shipped `_subject_fn(tool_name, arg_key)` factory from
    `tools/memory/manage.py:21-34` (added by 3a) — `_subject_fn("skill_create","name")`, etc. New
    subjects: `tool:skill_create:<name>`, `tool:skill_edit:<name>`, `tool:skill_patch:<name>`,
    `tool:skill_delete:<name>`. Delete the old `_skill_manage_approval_subject`.
  - **Dream `skill_reviewer` (behavioral):** `daemons/dream/_reviewer.py:67-69`
    `SKILL_REVIEW_SPEC.tool_names` grants `skill_manage`. Replace with the write tools it actually
    uses — `skill_create`, `skill_edit`, `skill_patch` — plus the existing `skill_view`/`memory_search`.
    Do **not** grant `skill_delete`: `prompts/skill_review.md:17` explicitly forbids deletion. Reword
    that prompt line ("Call skill_manage(action='delete')" → "Delete a skill") so it no longer names
    a now-nonexistent action. Without this grant change the dream skill reviewer can no longer write.
  - **`skill_view` docstring (same file, `:42-48`):** the "Call before `skill_manage(action='edit')`
    or `skill_manage(action='patch')`" steer must become `skill_edit`/`skill_patch`.
  - **Display map:** `tools/display.py` `TOOL_START_DISPLAY_ARG` (`:15-34`) has **no** `skill_manage`
    entry today. Add four entries `skill_create`/`skill_edit`/`skill_patch`/`skill_delete` → `"name"`
    (3a's memory entries each map to that tool's real name-bearing arg — `name_title`/`filename_stem`;
    here all four skill tools take `name`, so all four map to `"name"`).
  - **Rule prompts:** `context/rules/06_skill_protocol.md` (4 refs — patch `:39`, edit `:41`, create
    `:50`, create `:67`) and `context/rules/07_memory_protocol.md:81` (`skill_manage(action='create')`)
    → split tool names.
  - **skill-creator skill:** `skills/skill-creator.md` frontmatter description (`:2`) and body (`:47`)
    reference `skill_manage(action='create')` → `skill_create`.
  - **`deps.py:161` comment** mentions `skill_manage(create|edit|patch)` → update names.
- **Spec sync (post-delivery via sync-doc, enumerated for completeness — mirrors 3a):**
  - `docs/specs/skills.md` (heaviest): refresh_skills caller (`:29`), single-write-entry-point row
    (`:30`), Path-3 prose (`:52`), dream-reviewer prose (`:54`), lint-attach prose (`:213`), drift-fix
    prose (`:234`), offer-to-save prose (`:236`), the `skill_manage(action, name, ...)` signature
    heading + subject line (`:288-290`), Files table (`:345`), verification rows (`:363,370,402`).
  - `docs/specs/tools.md`: native-tool count **33 → 36**, Tool Groups row (`:32`), Files table (`:278`).
  - `docs/specs/01-system.md`, `docs/specs/dream.md`: `skill_manage` references.
- **Tests (rewrite per new surface):**
  - `tests/test_flow_skills_manage.py` (~20 `skill_manage(action=…)` calls → split tools).
  - `tests/tools/system/test_skill_manage_resets.py` (create/edit/patch reset + delete-no-reset).
  - `tests/test_flow_skill_usage.py` (sidecar-counter integration via the tools).
  - `tests/test_flow_skill_creator_dispatch.py` (assertion that the dispatched input references
    `skill_create`, not `skill_manage`).
  - Evals: `eval_domain_review.py` reword `skill_manage` → split names. `eval_skills.py` is mostly a
    reword **except `case_w4_e_discovery` (`:527-601`)** — a gated re-flip diagnostic hard-keyed on
    `deps.tool_index.get("skill_manage")` + a `DEFERRED`-visibility gate (`:588-600`). After the split
    that key no longer exists, so it falls permanently into its inert SOFT_PASS branch and the
    diagnostic goes un-revivable (it self-guards, so it won't redden the suite — it just dies silently).
    Decide explicitly: **retarget the discovery probe at one split tool** (e.g. flip `skill_create` to
    DEFERRED as the probe), or **retire W4.E** with a note. Not a blanket reword.
- **Fallback if unsplit:** state per-action ignored params + defaults inline (the four HIGH
  rewrites above) and make `name` required.

### 3c. `file_patch(mode=…)` → split
- **File:** `co_cli/tools/files/write.py:544-587`
- **Critical issues:** `mode='replace'` (single-file string edit via path/old_string/new_string)
  and `mode='patch'` (V4A multi-file via `patch`) share **zero** params. `path`/`old_string`/
  `new_string` are typed-optional but mandatory under replace (`:435-440`) and silently dead under
  patch (paths come from the patch text).
- **Dead/conditional (HIGH each):** `patch` (patch-only `:589-590`, ignored in replace),
  `replace_all` (replace-only; default not named; hardcoded False in V4A `:317`), `show_diff`
  (replace-only; default not named; patch always emits a diff `:322`).
- **Guidance/load (HIGH/LOW):** no when-to-use steer or concrete replace example; 7 params with
  4-5 conditional per call.
- **Fix (split):**
  - `file_patch(path, old_string, new_string, replace_all=False, show_diff=False)` — single-file
    replace. Args become unconditionally required/optional; add example
    `file_patch(path="a.py", old_string="foo", new_string="bar")`.
  - `file_apply_patch(patch)` — V4A multi-file; `patch` is its single required arg.
- **Fallback if unsplit:** state dead-in-X-mode on every conditional line; add a when-to-use
  steer + replace example.

---

## Task 4 — cross-family naming & semantic alignment

### Working-directory: one name, one contract (HIGH ×2)
- `shell_exec.workdir` (`shell/execute.py:18`) — workspace-relative, traversal-guarded via
  `enforce_workspace_boundary`.
- `task_start.working_directory` (`tasks/control.py:38`) — any absolute path, **no** boundary
  check.
- **Fix:** pick one name (`working_directory`) and one path contract across both. Either enforce
  the workspace boundary in `task_start` too, or explicitly document that it accepts absolute
  paths (currently silent). Suggested `shell_exec` wording: `working_directory: Subdirectory
  relative to the workspace root (e.g. "src/api"). Default None = workspace root. Absolute paths
  and ".." traversal are rejected.`

### `google_calendar` defaults: reconcile (HIGH/LOW)
- `days_ahead` defaults to **1** in `google_calendar_list` (`calendar.py:94`) but **30** in
  `google_calendar_search` (`:167`). Same param name, two defaults.
- **Fix:** document the rationale in both (`list` = narrow today-window; `search` = next-month
  hunt), or align. Reword `days_back` in both away from the misleading "today onward" →
  `days_back: How many past days to include before today (default 0 = start from today 00:00).`
  Add the "Recurring events are expanded into individual occurrences" caveat to `search` for
  parity (`:184`).

### `max_requests` magic sentinel (HIGH)
- `web_research` (`delegation.py:83`): `0` means "use config default budget" (10), the opposite of
  its literal reading. (Was HIGH ×2 with `knowledge_analyze`; that tool is now deleted.)
- **Fix:** `max_requests: Upper bound on the agent's internal LLM calls. Leave at 0 to use the
  configured default budget (10). Higher = more thorough but slower/costlier.`

### `google_drive_search.page` stateful contract (HIGH)
- `page` is session-stateful (hidden page tokens, `drive.py:20-29`); page N errors without N-1
  fetched first (`ModelRetry :25`). Only google tool using `page` vs siblings' `max_results`.
- **Fix:** reword (`:95`): `page: 1-based page number (default 1). Must be requested
  sequentially — page N requires page N-1 fetched earlier this session; you cannot jump ahead.`
  Consider exposing `max_results` for family parity (LOW).

### `web_search.max_results` silent clamp (HIGH)
- `web/search.py:310` — docstring "max 8" reads like validation but is a silent clamp; Caveats
  "capped regardless of max_results" is contradictory.
- **Fix:** `max_results: How many results to return, 1-8 (default 5). Values above 8 are silently
  clamped to 8.` Drop the contradictory Caveats bullet.

### `web_fetch.format` non-HTML no-op (HIGH)
- `web/fetch.py:151` — `format` only applies to HTML responses; for JSON/XML/text all three
  values produce identical output.
- **Fix:** `format: How to render HTML responses — "markdown" (default), "html", or "text".
  Ignored for JSON/XML/plain-text responses, which are returned as-is.`

### Minor naming (LOW)
- `task_list.status_filter` (`tasks/control.py:203`): keep the name; tighten Args to mirror the
  row schema and enumerate the four status values.
- `memory_view.name` (`memory/view.py:34`): tighten to disambiguate from `memory_manage` — `name:
  The filename_stem from a memory_search hit (not the artifact title).`
- `google_gmail_draft.to` (`gmail.py:162`): state comma-separated multiple-recipient support.

---

## Task 5 — sibling disambiguation steers (HIGH)

Each near-duplicate pair needs reciprocal when-to-use / when-NOT-to-use lines.

- **`web_search` ↔ `web_research`:**
  - `web_search` (`search.py:286`): add "For a single quick lookup of snippets/URLs use this
    tool; for a multi-page question needing reading + synthesis use `web_research`."
  - `web_research` (`delegation.py:112`): add to "When NOT to use" — "a quick lookup where ranked
    snippets/URLs suffice — use `web_search` (single call, not a multi-step agent)."
- **`google_gmail_list` ↔ `google_gmail_search`:**
  - `search` (`gmail.py:100`): add "For a plain most-recent-inbox overview with no filters, use
    `google_gmail_list`." (`list` already points to `search` at `:62`.)
  - Family note: list-vs-search is a deliberate pattern (mirrors `obsidian_list`/`obsidian_search`)
    — keep both; do **not** unilaterally merge. A family-wide list-vs-search consolidation
    decision is out of scope for this plan.
- **`google_calendar_list` ↔ `google_calendar_search`:** covered by the defaults reconciliation
  in Task 4 (cross-reference the differing windows in both docstrings).

---

## Acceptance / verification

- Tasks 1, 2, 4, 5 are docstring/wording (+ a couple of small semantic alignments) — verify with
  lint and the existing tool behavioral tests; no eval needed.
- Task 3 (splits) needs: new per-operation tools registered, old tool removed, `agent/toolset.py`
  imports updated, result-marker/display maps updated, `docs/specs/tools.md` updated (Tool Groups,
  native-tool count, Files table), and tests rewritten per new surface. Run the full suite.
- Cross-check against the `prefill-trim` family before editing shared docstrings so size-trim and
  correctness-fix edits don't clobber each other.

## Open decisions for Gate 1

1. **Splits vs unsplit-with-inline-conditionals** for `memory_manage`/`skill_manage`/`file_patch`.
   Recommendation: split (matches doctrine and the `file_search` precedent; internal handlers
   already exist). Splitting raises the native-tool count (3 tools → ~11), which trades token
   budget for correctness — weigh against the `prefill-trim` budget guard.
   **Resolved at Gate 1 for Task 3b (2026-06-01): SPLIT.** `memory_manage` (3a) already shipped as a
   split; leaving `skill_manage` discriminated would be incoherent, and the `_skill_*` helpers already
   exist. The +3 native-tool count is doctrine-aligned (`feedback_tool_split_small_model`), not a
   prefill regression. (3c `file_patch` remains open.)
2. **`task_start` working-dir security contract** — enforce workspace boundary (behavior change)
   vs document absolute-path acceptance (doc-only).
3. **`google_drive_search`** — expose `max_results` for family parity (signature change) vs
   document the fixed page size (doc-only).

## Final — Team Lead

Plan approved.

## Gate 1 — PO + TL verdict (Task 3b, 2026-06-01)

**Status: PASS.** Scoped to Task 3b (`skill_manage` split) only.

- **PO:** right problem (polymorphic write tool, 1 critical + 5 HIGH, violates the small-model
  monomorphic doctrine), correct scope (one split + integration touch-points, mirrors shipped 3a, no
  creep), value justified (+3 native-tool count is doctrine-aligned, not a prefill regression).
- **TL:** code refs verified; thin-wrapper-over-existing-helpers shape confirmed; all behavioral
  touch-points covered (dream `skill_reviewer` grant + prompt, `_subject_fn` swap, `skill_view`
  self-ref, display map, rules 06/07, skill-creator, `deps.py`); W4.E eval + wrappers-thin hazards
  pinned.
- **Open decision #1 resolved for 3b: SPLIT** (see updated note above).

> Cleared to implement. Run: `/orchestrate-dev tool-surface-small-model-audit` (Task 3b).
> Tasks 3c, 4, 5 and open decisions #2/#3 remain unapproved — out of this gate.

## Delivery Summary — Task 3b (2026-06-01)

| Task | done_when | Status |
|------|-----------|--------|
| 3b — `skill_manage` → split | 4 monomorphic tools registered, `skill_manage` removed, scoped tests green | ✓ pass |

**What shipped:**
- `co_cli/tools/system/skills.py` — `skill_manage(action=…)` replaced by `skill_create(name, content)`,
  `skill_edit(name, content)`, `skill_patch(name, old_string, new_string, replace_all=False)`,
  `skill_delete(name)`. All `approval=True`, `name` required, validated via `_require_valid_name`.
  Thin wrappers over the unchanged `_skill_*` helpers (no duplicated side-effects). Per-tool approval
  subjects via the `_subject_fn(tool_name, arg_key)` factory; old `_skill_manage_approval_subject`
  removed. `skill_view` docstring + module docstring + delete error message de-staled.
- Integration: `agent/toolset.py` import; `tools/display.py` (+4 entries); dream `_reviewer.py`
  `SKILL_REVIEW_SPEC` grant (create/edit/patch, not delete) + `prompts/skill_review.md` reword;
  `context/rules/06_skill_protocol.md` (×3) + `07_memory_protocol.md`; `skills/skill-creator.md`
  (×2); `deps.py` comment.
- Tests/evals (Dev-1): `test_flow_skills_manage.py`, `test_skill_manage_resets.py`,
  `test_flow_skill_usage.py`, `test_flow_skill_creator_dispatch.py` rewritten; `eval_skills.py`
  (W4.E discovery probe retargeted to `skill_create`; W4.C/D renamed) + `eval_domain_review.py`.
- Registration verified live: native index lists `skill_create/edit/patch/delete/view`; `skill_manage` absent.

**Tests:** scoped — 85 passed (57 skill-split + 28 adjacent dream/pin/session-review), 0 failed.
**Doc Sync:** fixed — skills.md, tools.md (count 33→36), 01-system.md, dream.md, uat_evals.md. Zero
residual `skill_manage` in live surface (only an accurate test-file *path* reference remains).

**Overall: DELIVERED**
Task 3b shipped; surface is now monomorphic per the small-model doctrine. 3c (`file_patch`) and Tasks
4/5 remain unstarted.

**Next step:** `/review-impl tool-surface-small-model-audit` — full suite + evidence scan → verdict.
