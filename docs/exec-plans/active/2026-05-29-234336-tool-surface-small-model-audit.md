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
- **Task 3 — monomorphic surface fixes for the three discriminated write tools (CRITICAL):**
  `memory_manage` and `skill_manage` split into per-operation tools (mirrors the shipped `file_search`
  split; internal `_handle_*`/`_skill_*` helpers already exist → thin wrappers). `file_patch` is
  resolved by **removing** the V4A multi-file mode (Option B), not splitting — see Task 3c.
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

### 3c. `file_patch(mode=…)` → remove V4A, keep single-file replace (Option B) — ✓ DONE

- **File:** `co_cli/tools/files/write.py:544-606` (signature `:544-553`, docstring `:554-587`,
  body `:588-606`).
- **Critical issue:** `mode='replace'` (single-file string edit via path/old_string/new_string)
  and `mode='patch'` (V4A multi-file via `patch`) share **zero** params. `path`/`old_string`/
  `new_string` are typed-optional but mandatory under replace (None-guards at `:435-440`) and silently
  dead under patch (paths come from the patch text). `patch` is patch-only (`:588-590`);
  `replace_all`/`show_diff` are declared on the signature (`:550-551`) but consumed only on the replace
  path.
- **done_when:** `_v4a.py` deleted; `file_patch` is a 5-param single-file replace
  (`path`, `old_string`, `new_string`, `replace_all`, `show_diff`) with `path`/`old_string`/`new_string`
  required-by-signature; no `mode`/`patch` params; the V4A test block removed; full suite green; the 2
  RESEARCH comparison rows corrected.
- **success_signal:** the model sees one monomorphic `file_patch(path, old_string, new_string,
  replace_all, show_diff)` — no `mode`/`patch`, no V4A surface anywhere; native-tool count unchanged.

#### Approach: remove V4A multi-file entirely (not split)

The original plan proposed splitting into `file_patch(replace)` + `file_apply_patch(patch)`. Peer
evidence overrides that: **V4A is the OpenAI-Codex-native patch format, and harnesses gate it to
OpenAI models only.**

- **opencode** (`registry.ts:322-325`): `usePatch = modelID.includes("gpt-") && !oss && !gpt-4`.
  GPT-Codex models get `apply_patch` **only**; Claude and every other model get `edit`+`write` only —
  mutually exclusive, a non-OpenAI model never sees V4A.
- **openclaw** (`pi-tools.ts:266-292, 661-668`): `apply_patch` enabled only when `isOpenAIProvider()`.
- **hermes** keeps the dual-mode `patch` for all models — but targets large frontier models, not the
  small local models co runs.

co's audit premise is small local models — the exact consumer opencode/openclaw deliberately shield
from V4A. For co's target models the peer-aligned surface is `{file_patch(replace), file_write}` and
**no** structured multi-file patch. Whole-file delete (which V4A uniquely provided — co has no
`file_delete`; verified) moves to `shell_exec` (`rm`), a content-free op the anti-shell-edit steer
does not cover; in-file content deletion is retained via `new_string=""`. This is *less* work than
the split and advances the small-model doctrine further. (Alternatives weighed: **A** split as
originally planned — keeps a frontier-format tool small models shouldn't use; **B′** drop V4A but add
a monomorphic `file_delete(path)` — adds a native tool (count regression against the prefill-budget
guard) to cover an op the agent already performs cleanly via `shell_exec rm` (content-free, so the
anti-shell-edit steer does not cover it), and makes co the only one of the four peers with a standalone
delete. **B chosen** — it rests on co's own doctrine (no count regression, delete is a content-free
shell op), not just peer parity.)

#### New surface (hermes schema-design conventions)

`file_patch` becomes a pure single-file replace tool — all params unconditional, defaults stated
inline with value *and* meaning, hermes's `old_string` uniqueness guidance and `new_string=""`
delete idiom carried over:

```python
@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS, approval=True, retries=1, is_concurrent_safe=False
)
async def file_patch(
    ctx: RunContext[CoDeps],
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    show_diff: bool = False,
) -> ToolReturn:
    """Make a targeted find-and-replace edit in a single existing file.

    Use this instead of sed/awk — never shell redirection — for editing. Requires
    file_read on the file first. Tries four matching strategies (exact, line-trimmed,
    indent-stripped, escape-expanded) so minor whitespace differences won't break the
    match. Returns the replacement count and strategy.

    When NOT to use: creating a new file or a full rewrite — use file_write. To delete
    a whole file, use shell_exec (rm).

    Args:
        path: File path relative to the workspace root.
        old_string: Exact text to find. Must be unique in the file unless replace_all=True;
            include surrounding context lines to make it unique.
        new_string: Replacement text, applied verbatim. Pass "" to delete the matched text.
        replace_all: Replace every occurrence instead of requiring a unique match
            (default False = require exactly one match).
        show_diff: Prepend a unified diff of the change to the output (default False).
    """
```

The three None-guards (`:435-440`) disappear — params are now required by signature. The body is
just the current `_file_patch_replace` logic (inline it or keep the helper).

#### Full-system V4A footprint (complete sweep — `git grep -i v4a` + directive/symbol tokens)

Confirmed: V4A is **not driven by any skill, rule prompt, eval, or spec** (zero hits in `co_cli/context/`,
`skills/`, `evals/`, `docs/specs/`). It lives entirely in the write-tool implementation + tests + two
reference docs. Every site below is removed/reconciled in this task.

**Production code (delete):**
- **`co_cli/tools/files/_v4a.py`** — delete the whole module (`OperationType`, `HunkLine`, `Hunk`,
  `PatchOperation`, `parse_v4a_patch`). Only consumers are `write.py` + the V4A tests.
- **`co_cli/tools/files/write.py`:** the `_v4a` imports (`:16-20`), the `PatchMode` type alias (`:9`),
  the parser comment block (`:271-277`), `_PendingWrite` (`:277`), `_insert_addition_hunk` (`:280-288`),
  `_compute_v4a_update` (`:291-323`), `_compute_v4a_add` (`:326-340`), `_compute_v4a_delete` (`:343-354`),
  `_write_v4a_pending` (`:357-393`), `_apply_v4a_patch` (`:396-423`), the `mode`/`patch` params + the
  V4A half of the docstring (`:546,554,561-575,580,586`), and the `mode=='patch'` branch in the tool
  body (`:588-604`).

**Tests (delete):**
- **`tests/test_flow_files_write.py:163-224`** — the `# file_patch — V4A patch mode` section: three
  tests (`test_file_patch_v4a_mode_updates_file_content` `:168`, `..._adds_new_file` `:195`,
  `..._rejects_unsupported_move_directive` `:212`). Remove the whole block.

**Task 1 supersession:** Task 1's third item added the V4A Move-directive rejection in `_v4a.py` + the
`..._rejects_unsupported_move_directive` test. Deleting `_v4a.py` supersedes that fix — both go.

**Reference-doc hygiene (factual-accuracy update, not behavioral — reference layer, not specs):**
- **`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md:123`** and
  **`docs/reference/RESEARCH-tools-peers-tiers.md`** (`:40,256,298,376-377,786-787`) carry comparison
  rows asserting co-cli "✓ supports V4A multi-file patch". After removal these are false. Update the
  co-cli column entries to "removed — V4A is OpenAI-Codex-format, incompatible with co's small local
  models" (cite the opencode/openclaw model-gating). These are point-in-time survey artifacts; a
  one-line correction per row suffices — do not rewrite the surveys.

**CHANGELOG.md — do NOT edit (append-only history):**
- `CHANGELOG.md:21-27` documents Task 1's *shipped* V4A parser fix at its release version. CHANGELOG is
  append-only; shipped entries are never rewritten. The V4A removal gets a **new** entry at `/ship`
  time noting the surface removal + Task-1 supersession.

**Excluded false positive:** `co_cli/tools/web/_ssrf.py:30` (`IPv4Address`) — IP networking, not V4A.

#### Integration touch-points (simpler than the split — most need no change)

- **`agent/toolset.py:17`** — `from co_cli.tools.files.write import file_patch, file_write` is
  **unchanged** (no `file_apply_patch` added). file_patch keeps its name.
- **`tools/display.py:21`** — `"file_patch": "path"` stays correct (file_patch keeps `path`). No change.
- **`tools/categories.py`** — `file_patch` stays in `PATH_NORMALIZATION_TOOLS` (`:12`) and `FILE_TOOLS`
  (`:22`); it still has a single `path` arg. No change.
- **`approvals.py:90-126`** — the `("file_write", "file_patch")` path-subject branch stays correct and
  gets **simpler**: the pre-existing degenerate `file_patch(path=None)` display under the old V4A mode
  is eliminated (V4A had no `path`), so the `old_string`/`new_string` display path is now always the
  real one. Optional cleanup: drop the now-dead `path or ""` empty-path fallback. `test_flow_approval_subject.py:66-121`
  stays valid (replace-mode is path-based).
- **`deps.py:52`** comment ("shared across file_write and file_patch for the same directory") stays
  accurate. **`shell/execute.py:29`** steer ("file_write / file_patch instead of shell redirection")
  stays accurate.

#### Spec sync (post-delivery via sync-doc)

- **`docs/specs/tools.md`:** native-tool count is **unchanged** (file_patch stays one tool, no new
  tool added). Grep is clean today — the file_patch rows (`:31,73,170,181,187,210,274`) carry no
  V4A/multi-file/mode wording, so there is nothing to remove; just confirm none reappears, and
  optionally add the `shell_exec rm` whole-file-delete note to the file_patch row.
- `docs/specs/01-system.md` likewise has no actionable V4A prose (only the generic `file_write /
  file_patch land here` at `:221`); confirm clean post-removal.

#### Tests

- **`tests/test_flow_files_write.py`:** remove the V4A block (see footprint above); the remaining
  replace-mode tests drop the now-absent `mode=` kwarg.
- **`tests/test_flow_approval_subject.py`:** no change (file_patch path-subject unchanged).
- **`tests/test_flow_agent_tool_concurrent_default.py`:** verify `is_concurrent_safe=False` still
  asserted on file_patch (decorator retained).

---

## Task 4 — cross-family naming & semantic alignment

### Working-directory: one name, one contract (HIGH ×2)

**Naming — ✓ RESOLVED & IMPLEMENTED (2026-06-02, ahead of Gate 1).** Both params renamed to
**`work_dir`**. Name chosen against the house `_dir` convention, not the plan's original
`working_directory` proposal: the codebase has 20+ `_dir`-suffixed identifiers (`workspace_dir`,
`memory_dir`, `sessions_dir`, `tool_results_dir`, `user_skills_dir`, …) and `_directory` appeared
exactly 3 times — all the lone `task_start.working_directory` outlier. `workdir` (shell_exec) also
violated the convention (no underscore). Standardizing on `working_directory` would have propagated
the outlier and demoted the dominant pattern; `work_dir` follows the convention, keeps `dir` as a
sanctioned standard shorthand (`feedback_naming_no_abbreviations`), and stays disambiguated from
`workspace_dir` (the project root / write anchor) — `work_dir` is an optional per-call subdirectory
*under* it.
  - `shell_exec.work_dir` (`shell/execute.py:18`) — workspace-relative, traversal-guarded via
    `enforce_write_boundary`.
  - `shell_exec.work_dir` (`shell/execute.py:18`) and `task_start.work_dir` (`tasks/control.py:38`)
    now share both name and contract.
  - Delivered: param + body + docstrings on both tools, `tests/test_flow_shell_exec.py` (kwargs +
    test names), and `docs/specs/tools.md` (cwd-anchoring bullets) all renamed; lint clean; 15
    shell/task tests green. Pure rename, no behavior change.

**Contract — ✓ RESOLVED & IMPLEMENTED (2026-06-02; decision #2 → option (a) enforce).** `task_start`
previously accepted any `work_dir` unchecked and defaulted `None → Path.cwd()` (two divergences from
`shell_exec`, which boundary-guards and defaults `None → workspace_dir`). One name + two contracts is
a Pattern-6 small-model hazard, and the unguarded path was a real escape gap — *worse* on a detached,
longer-lived background command than on the foreground sibling. Conformed by mirroring the
`shell_exec` block (`execute.py:72-79`) into `task_start`:
  - `tasks/control.py` — added the `enforce_write_boundary` import; `cwd` now boundary-guards
    `work_dir` (rejects absolute / `..` → `tool_error`) and defaults `None → workspace_dir`. The
    boundary check runs before the task is registered/spawned, so a rejected path leaves no task.
  - `commands/background.py` — the `/background` slash command anchor changed `Path.cwd()` →
    `ctx.deps.workspace_dir` (per user decision); dropped the now-unused `Path` import. Every
    shell-launch path (foreground tool, background tool, slash command) now shares the `workspace_dir`
    anchor.
  - `docs/specs/tools.md` — extended the `shell_exec` working-directory section to state `task_start`
    shares the contract and `/background` shares the anchor.
  - Tests: added `test_task_start_work_dir_scopes_to_subdir` + `test_task_start_work_dir_escape_rejected`
    (`tests/test_flow_background_tasks.py`); `_make_task_ctx` now sets `workspace_dir=tmp_path`.
  - **Behavior change:** background tasks can no longer run outside the workspace, and `None` now
    anchors to `workspace_dir` rather than the process launch dir (identical in the common
    no-`workspace_path` case). **Uncommitted** — to be committed with the rest of Task 4 (or on request).

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
- `memory_view.name` (`memory/view.py:34`): tighten to disambiguate from `memory_create.name_title`
  (`memory_manage` was split in 3a) — `name: The filename_stem from a memory_search hit (not the
  artifact title).`
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
   prefill regression.
   **3c resolved differently (Option B — remove V4A, not split):** peer evidence (opencode
   `registry.ts:322-325` and openclaw `pi-tools.ts:266-292` both gate V4A `apply_patch` to OpenAI-Codex
   models only) shows V4A is the wrong surface for co's small local models. Remove V4A multi-file
   entirely; keep `file_patch` as a pure single-file replace; whole-file delete moves to `shell_exec`
   (`rm`), in-file deletion stays via `new_string=""`. Native-tool count is **unchanged** (no new tool).
   See Task 3c for the full A/B/B′ weigh and the hermes-aligned surface.
2. **`task_start` working-dir security contract** — enforce workspace boundary (behavior change)
   vs document absolute-path acceptance (doc-only).
   **✓ RESOLVED & IMPLEMENTED (2026-06-02): option (a) ENFORCE.** Both params renamed to `work_dir`
   per the house `_dir` convention, and `task_start` now boundary-guards `work_dir` + defaults
   `None → workspace_dir`, matching `shell_exec`; the `/background` slash command was aligned to the
   `workspace_dir` anchor too. See Task 4 Working-directory for the full delivery.
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

## Implementation Review — Task 3b (2026-06-01)

Scope: Task 3b only. Stance: issues exist — PASS earned. Two parallel evidence subagents (production
code + tests/evals) plus targeted adversarial verification of the two highest-risk false-PASS
candidates (dream-reviewer grant resolution; helper-guard reachability).

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| 3b | 4 tools registered, `skill_manage` gone, scoped tests green | ✓ pass | `skills.py:303-411` — 4 tools, `name: str` required, each `approval=True` + `_subject_fn(<tool>, "name")`; wrappers `return _skill_*(...)` to sync helpers (`:123/156/186/243`); old `_skill_manage_approval_subject` removed; native index = `[skill_create,delete,edit,patch,view]`, `skill_manage` absent |
| 3b (integration) | touch-points wired | ✓ pass | `toolset.py:37-43` import; `display.py:30-33` 4 entries; `_reviewer.py:67-72` grants create/edit/patch (NOT delete — verified resolves to real tools, no silent break); `skill_review.md:17` reworded; rules 06/07, skill-creator, `deps.py:161` zero `skill_manage` |
| 3b (tests/evals) | green, no mocks | ✓ pass | 4 test files: zero `skill_manage(` calls, no mocks/monkeypatch, real CoDeps + tmp filesystem; `test_flow_skill_creator_dispatch.py:52` asserts `skill_create`; `eval_skills.py:585` W4.E probe retargeted to `skill_create` (stays inert SOFT_PASS since ALWAYS); `eval_domain_review.py:127` consistent with SKILL_REVIEW_SPEC |

### Issues Found & Fixed
No blocking issues found. One non-blocking note (no action): `_skill_create/_skill_edit/_skill_patch`
retain `str | None` params with defensive `if not content`/`if new_string is None` guards now
unreachable from the required-`str` wrapper surface — pre-existing, type-compatible, not a
split-introduced regression. The empty-`new_string` deletion path remains intact (guard checks
`is None`, not falsiness).

### Tests
- Command: `uv run pytest -q` (full suite)
- Result: **654 passed, 0 failed** (289.68s)
- Log: `.pytest-logs/<ts>-review-impl-3b.log`
- Spec count `12 explicit approval-gated` verified accurate (full-surface basis incl. config-gated `google_gmail_draft`; default-config live count is 11).

### Behavioral Verification
- No `co status`/`logs` command in this project; `co chat` is the user-facing entry.
- `co chat` boots clean with the new surface: 7 skills loaded, tools register, `✓ Ready` (only the
  environmental TEI-reranker fallback shows "degraded" — unrelated to 3b).
- Skill-tool behavior exercised end-to-end by `test_flow_skills_manage.py` (real filesystem writes,
  no mocks). `success_signal` (model sees four monomorphic skill tools, not one discriminated tool)
  confirmed via the native tool index.

### Overall: PASS
Task 3b is correct, complete, and fully tested. Surface is monomorphic per the small-model doctrine;
zero blocking findings; full suite green. Ready for Gate 2 → `/ship`.

## Gate 1 — PO + TL verdict (Task 3c, 2026-06-02)

**Status: PASS.** Scoped to Task 3c (`file_patch` V4A removal / Option B) only. Converged at Cycle C1 —
both reviewers returned `Blocking: none`.

- **PO:** right problem (V4A is the OpenAI-Codex-native format; peer-verified that opencode/openclaw
  gate it to OpenAI models only — wrong surface for co's small local models), correct scope (tight V4A
  removal, no creep; RESEARCH-doc factual correction + no CHANGELOG rewrite is sound), value justified
  (smaller small-model-friendly surface; the capability "loss" strands nothing — zero V4A drivers in
  skills/rules/evals/specs). Option B over B′ confirmed: a standalone `file_delete` would be a
  native-count regression for an op `shell_exec rm` already covers cleanly.
- **TL:** removal scope verified complete against source (`git grep -i v4a` + symbol sweep — sole
  consumers are `write.py`, `_v4a.py`, and the 3 tests at `test_flow_files_write.py:168/195/212`); all
  "no-change" integration claims (approvals, display, categories, toolset) confirmed; `file_patch` body
  already delegates wholly to `_file_patch_replace`, so the required-param extraction is safe; no
  external importer of the deleted symbols.
- **Open decision #1 resolved for 3c: Option B (remove V4A, not split)** (see updated note above).

**C1 decisions (all 4 minor issues adopted):**

| Issue ID | Decision | Change |
|----------|----------|--------|
| CD-m-1 | adopt | Spec-sync bullet softened — `docs/specs/tools.md` has no V4A prose to remove (grep clean); reworded to "confirm clean + optionally add `shell_exec rm` note". |
| CD-m-2 | adopt | Critical-issue line: corrected stale `:317,322` citations → `replace_all`/`show_diff` declared on signature `:550-551`, consumed only on replace path. |
| PO-m-1 | adopt | Added explicit `done_when:` and `success_signal:` lines to the 3c section (consistency with 3b). |
| PO-m-2 | adopt | B′-rejection rationale strengthened — rests on co's own doctrine (no native-count regression; delete is a content-free shell op), not just peer parity. |

> Cleared to implement. Run: `/orchestrate-dev tool-surface-small-model-audit` (Task 3c).
> Tasks 4, 5 and open decisions #2/#3 remain unapproved — out of this gate.

## Delivery Summary — Task 3c (2026-06-02)

| Task | done_when | Status |
|------|-----------|--------|
| 3c — `file_patch` V4A removal (Option B) | `_v4a.py` deleted; `file_patch` is a 5-param single-file replace (`path`/`old_string`/`new_string` required-by-signature, no `mode`/`patch`); V4A test block removed; RESEARCH comparison rows corrected | ✓ pass |

**What shipped:**
- `co_cli/tools/files/_v4a.py` — **deleted** (`git rm`); sole consumers were `write.py` + the V4A tests.
- `co_cli/tools/files/write.py` — removed the `_v4a` imports, the `PatchMode`/`Literal` alias, and all
  V4A apply helpers (`_PendingWrite`, `_insert_addition_hunk`, `_compute_v4a_update/_add/_delete`,
  `_write_v4a_pending`, `_apply_v4a_patch`). `file_patch` is now monomorphic:
  `file_patch(path, old_string, new_string, replace_all=False, show_diff=False)` — all params
  unconditional, `path`/`old_string`/`new_string` required by signature (the three None-guards are
  gone), defaults stated inline, hermes `old_string`-uniqueness + `new_string=""` delete idiom in the
  docstring. The `mode`-dispatch and `_file_patch_replace` indirection were inlined into the tool body.
  Decorator (`approval=True`, `is_concurrent_safe=False`, `retries=1`) retained.
- **Integration touch-points: all confirmed no-change** (`agent/toolset.py` import, `tools/display.py`,
  `tools/categories.py`, `approvals.py` path-subject, `deps.py`/`shell/execute.py` comments) — `file_patch`
  keeps its name and single `path` arg.
- Reference-doc factual correction (3 co-cli-asserting rows): `RESEARCH-tools-gaps-co-vs-hermes.md:123`,
  `RESEARCH-tools-peers-tiers.md:377` and `:787` updated to "V4A removed — OpenAI-Codex format gated to
  OpenAI models by opencode/openclaw". Peer-inventory rows (`:40` hermes, `:298` openclaw `apply_patch`,
  `:256` model-filtering) left intact — they describe peer surfaces accurately. CHANGELOG untouched
  (append-only; new entry at `/ship`). Task 1's Move-directive fix is superseded by the `_v4a.py` deletion.

**Tests:** scoped — 28 passed, 0 failed (`test_flow_files_write.py` V4A block removed + `mode=` kwarg
dropped from replace tests + new `test_file_patch_deletes_matched_text_with_empty_new_string` for the
delete idiom; `test_flow_approval_subject.py` + `test_flow_agent_tool_concurrent_default.py` unchanged
and green — `file_patch` path-subject + `is_concurrent_safe=False` still assert).
**Doc Sync:** clean — specs carry no V4A/`mode`/`patch` prose (grep clean across `docs/specs/`,
`skills/`, `context/`, `evals/`); native-tool count unchanged; `/sync-doc` would be a no-op, not invoked.

**Overall: DELIVERED**
Task 3c shipped; `file_patch` is now a monomorphic single-file replace per the small-model doctrine,
V4A multi-file surface fully removed. Tasks 4/5 remain unstarted.

**Next step:** `/review-impl tool-surface-small-model-audit` — full suite + evidence scan → verdict.

## Implementation Review — Task 3c (2026-06-02)

Scope: Task 3c only. Stance: issues exist — PASS earned. One evidence subagent (full file read +
done_when re-execution + integration-trace) plus an adversarial subagent targeting the two
highest-risk false-PASS candidates: behavioral equivalence of the `_file_patch_replace` inlining, and
orphaned consumers of the deleted `_v4a.py`.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| 3c | `_v4a.py` deleted; `file_patch` 5-param single-file replace, no `mode`/`patch`; V4A test block gone; RESEARCH rows corrected; suite green | ✓ pass | `write.py:318-388` — signature `file_patch(ctx, path, old_string, new_string, replace_all=False, show_diff=False)`, all three text params required-by-signature, no None-guards; body flow (boundary→exists→preconditions(ModelRetry)→lock→size→resolve→write→mtime→lint) byte-identical to old `_file_patch_replace` minus the removed guards. `_v4a.py` deleted (`git status: D`). `git grep` V4A-symbols across all `*.py` → only the sanctioned `web/_ssrf.py:30` (IPv4Address). `tests/test_flow_files_write.py` — zero `mode=`/`patch=`, real-fs only, new `test_file_patch_deletes_matched_text_with_empty_new_string` asserts content after `new_string=""`. Integration no-change confirmed in source: `toolset.py:17`, `display.py:21`, `categories.py:15,26`, `tools/approvals.py:93`. RESEARCH: gaps `:123` + peers `:377/:787` co-cli cells → "V4A removed"; peer rows (`:40` hermes, `:298` codex) correctly untouched. |

### Issues Found & Fixed
No issues found. One non-blocking note (no action): `tools/approvals.py:94-96` retains a defensive
`path = args.get("path", "")` empty-path fallback — still reachable (guards a missing arg), not dead;
the plan flagged its removal as *optional* cleanup, out of scope here.

### Tests
- Command: `uv run pytest -q` (full suite)
- Result: **652 passed, 0 failed** (318.04s) — count moved 654→652 (−3 V4A tests, +1 delete-idiom test).
- Log: `.pytest-logs/<ts>-review-impl-3c.log`

### Behavioral Verification
- No `co status`/`logs` command in this project; `co chat` is the user-facing entry. `file_patch` is a
  chat-visible tool, so the surface change was verified against the **live registered tool schema** via
  `_build_native_toolset` + `get_tools`.
- **`success_signal` verified:** the model sees one monomorphic `file_patch` with params exactly
  `{path, old_string, new_string, replace_all, show_diff}` (required: `path`/`old_string`/`new_string`);
  no `mode`/`patch`, no `file_apply_patch`; native-tool count **29 (unchanged)**.
- Replace + `new_string=""` delete behavior exercised end-to-end by `test_flow_files_write.py` (real
  filesystem, no mocks).

### Overall: PASS
Task 3c is correct, complete, and fully tested. `file_patch` is now a monomorphic single-file replace
per the small-model doctrine; V4A multi-file surface fully removed with zero orphaned consumers; full
suite green; native-tool count unchanged. Ready for Gate 2 → `/ship`.
