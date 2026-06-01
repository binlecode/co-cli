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
  - Append/replace/delete already dispatch to separate `_handle_*` fns — only the surface is
    conflated. Type `source_type` as the enum/Literal so invalid values are schema-rejected.
- **Fallback if unsplit:** every conditional Args line must state `REQUIRED for action=X; ignored
  otherwise. Default None.` and add an Examples block (create/append/replace/delete one-liners).

### 3b. `skill_manage(action=…)` → split
- **File:** `co_cli/tools/system/skills.py:297-360`
- **Critical issue:** `action` bundles create/edit/patch/delete; of 6 params, validity is
  per-action. create/edit use `content`; patch uses `old_string`/`new_string`/`replace_all`;
  delete uses only `name`.
- **Dead/conditional params (HIGH each):** `content` (create/edit-only `:303,338`), `old_string`
  (patch-only `:304,339`), `new_string` (patch-only `:305,340`), `replace_all` (patch-only
  `:306,341`; default `False` not named inline).
- **Required-as-optional (HIGH):** `name` defaults to `''` (`:300`) but every action requires a
  valid name (`_NAME_RE` guard `:343`) — required field presented as optional.
- **Fix (split):** `skill_create(name, content)`, `skill_edit(name, content)`,
  `skill_patch(name, old_string, new_string, replace_all=False)`, `skill_delete(name)`. Internal
  `_skill_create/_skill_edit/_skill_patch/_skill_delete` helpers exist (`:132-277`) → thin
  wrappers. Make `name` required (`name: str`, no default) in all four.
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
2. **`task_start` working-dir security contract** — enforce workspace boundary (behavior change)
   vs document absolute-path acceptance (doc-only).
3. **`google_drive_search`** — expose `max_results` for family parity (signature change) vs
   document the fixed page size (doc-only).
