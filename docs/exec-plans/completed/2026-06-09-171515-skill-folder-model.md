# Skill Folder Model — every skill is a directory with `SKILL.md`

Task type: code (infrastructure refactor)

## Context

co's skill model is **flat**: each skill is a single `<name>.md` file in `co_cli/skills/`
(bundled) or `~/.co-cli/skills/` (user). The loader globs `*.md` and derives the skill name
from the file stem. This cannot carry **bundled assets** (helper scripts, reference files): a
skill that needs more than prose has nowhere to put it that ships with the package.

This blocks the `documents` PDF-extraction skill (plan
`2026-06-09-093734-skill-documents.md`), whose `extract_pdf.py` helper must ship inside the
package — `scripts/` is build-time-only dev tooling and is **not** in the hatchling wheel.
The user's directive: **every skill becomes its own folder with a `SKILL.md` entry file
(Anthropic skill convention), even single-file skills** — so any skill can carry a `scripts/`
or `references/` payload uniformly.

This is the foundational change; `skill-documents` depends on it and adds only
`documents/SKILL.md` + `documents/scripts/extract_pdf.py`.

### Code Accuracy Verification (source-read 2026-06-09, v0.8.328)

| Claim | Verified | Cite |
|---|---|---|
| Bundled loader globs `*.md` flat/non-recursive; name = `path.stem` | ✓ | `co_cli/skills/loader.py:89,141,145` |
| `discover_skill_files` (the `/skills` listing) has its own flat `*.md` glob | ✓ | `co_cli/skills/lifecycle.py:18,20`; caller `co_cli/commands/skills.py:34` |
| User CRUD writes/finds `user_skills_dir / f"{name}.md"`; delete `path.unlink()` | ✓ | `co_cli/tools/system/skills.py:74,133,256` |
| Shadowed-bundled check reads `skills_dir / f"{name}.md"` | ✓ | `co_cli/tools/system/skills.py:245,257` |
| Usage sidecar path `user_skills_dir / f"{name}{SIDECAR_SUFFIX}"`; agent-created probe `user_skills_dir / f"{name}.md"` | ✓ | `co_cli/skills/usage.py:57,70` |
| 6 bundled skills today: doctor, review, plan, triage, refactor, skill-creator | ✓ | `co_cli/skills/*.md`; gate `_BUNDLED_NAMES` lines 10–18 |
| Lint docstring scopes "the shipped reference library (co_cli/skills/*.md)" | ✓ | `co_cli/skills/lint.py:12` |
| skill-creator output doc says `~/.co-cli/skills/<name>.md` | ✓ | `co_cli/skills/skill-creator.md:11` |
| `SkillInfo.path` exists, no current external consumers | ✓ | `co_cli/skills/skill_types.py:27` |
| hatchling default ships only `co_cli/` (no custom build target) | ✓ | `pyproject.toml:1-3` |

## Problem & Outcome

**Problem.** Flat single-file skills cannot carry bundled assets. A skill that needs a helper
script has no in-package home for it, so any non-trivial capability is forced to either
become a new tool (model-surface churn) or live in the unshipped `scripts/` dir (broken on
install).

**Failure cost:** every future skill richer than prose is blocked. `documents` is the first
casualty; vision/OCR and any scripted skill would hit the same wall.

**Outcome.**
1. Skill = a **directory** `<name>/` with a `SKILL.md` entry; the directory name is the skill
   name. Applies uniformly to bundled (`co_cli/skills/<name>/SKILL.md`) and user
   (`~/.co-cli/skills/<name>/SKILL.md`) skills, including single-file ones.
2. Loader + `/skills` discovery glob `*/SKILL.md`; name derives from the parent directory.
3. User CRUD (create/edit/patch/delete) and usage sidecars operate on the folder.
4. All 6 bundled skills migrated via `git mv` into `<name>/SKILL.md`.
5. Bundled gate + affected tests green; no flat-file fallback anywhere (zero backward compat).

## Scope

### In scope
- `co_cli/skills/loader.py` — discovery glob + name derivation.
- `co_cli/skills/lifecycle.py` — `discover_skill_files` glob.
- `co_cli/tools/system/skills.py` — user CRUD paths (create mkdir, delete folder cleanup),
  shadowed-bundled checks.
- `co_cli/skills/usage.py` — sidecar + agent-created paths (sidecar lives inside the folder).
- `co_cli/skills/lint.py` — docstring scope wording; `co_cli/skills/skill-creator.md` — output-path guidance.
- `git mv` all 6 bundled skills into `<name>/SKILL.md` folders.
- `tests/test_flow_skill_bundled_library.py` + affected skill tests.

### Out of scope
- **The `documents` skill and `pymupdf` deps** — that is plan `skill-documents`, which depends on this.
- **`__init__.py` in skill folders** — plain skill folders are filesystem dirs, never imported.
  Only a skill that ships **importable** Python (e.g. `documents/scripts/`, run via `-m`) needs
  `__init__.py`, and that is added by the dependent plan, not here.
- **Backward-compat dual-mode discovery** — no flat `*.md` fallback. A user's pre-existing
  flat `~/.co-cli/skills/<name>.md` stops loading; the migration is a manual one-off `mv`
  (documented in the release note), never production migration code.
- **Spec entry** — `docs/specs/skills.md` is updated by `sync-doc` post-delivery.

## Behavioral Constraints
1. **Uniform folder model.** Every skill is `<name>/SKILL.md`; the directory name is the
   authoritative skill name (not the file stem). No flat `<name>.md` skills remain.
2. **Zero backward compat.** Discovery globs `*/SKILL.md` only — no flat fallback, no
   migration shim, no alias. Existing skills move by `git mv`.
3. **Asset-ready.** A skill folder may hold sibling files/dirs (`scripts/`, `references/`);
   discovery keys on `SKILL.md` and ignores everything else in the folder.
4. **User CRUD parity.** create mkdirs `<name>/` and writes `SKILL.md`; delete removes the
   skill folder (not just the file); create-rollback removes the folder it just made.
5. **Sidecar co-location.** Usage sidecar moves inside the folder
   (`<name>/SKILL.usage.json`), so deleting the folder takes its sidecar with it.
6. **No behavior drift.** Loaded `SkillInfo` (name, description, body, env, flags) is identical
   to today for the same content — only the on-disk layout and path plumbing change.
7. **Single-level discovery by design.** Discovery globs exactly `*/SKILL.md` (one level), not
   a recursive `**/SKILL.md`. Category nesting (peer-style `skills/<category>/<name>/SKILL.md`,
   as in hermes) is explicitly out of scope. The single-level glob is the simpler *and* safer
   choice: it structurally cannot mis-discover a `SKILL.md` vendored inside a skill's own
   `references/`/`scripts/` payload, so co needs none of the exclude-list / depth-bound
   machinery the recursive peers carry (hermes' `EXCLUDED_SKILL_DIRS`, openclaw's depth ≤6).
   Adding categories later would require switching to a recursive glob plus an exclude list.
8. **Name derives from the directory, not frontmatter.** The skill name is the parent dir name
   (preserving today's `path.stem` semantics — the loader never reads a frontmatter `name:`).
   This diverges from the three peers (hermes/openclaw/opencode), which prefer frontmatter
   `name:` and fall back to the dir name. Consequence: a `SKILL.md` copied from an external
   ecosystem whose frontmatter `name:` differs from its folder name loads under the **folder
   name**, silently ignoring `name:`. Accepted — co imports skills by hand, the folder is the
   single source of truth, and this matches co's existing behavior; the divergence is recorded,
   not introduced here.

## High-Level Design

**Discovery.** `loader.load_skills` and `lifecycle.discover_skill_files` change their glob
from `<dir>.glob("*.md")` to `<dir>.glob("*/SKILL.md")`. In `_load_skill_file`, name derives
from `path.parent.name` instead of `path.stem`. Symlink rejection and the security scan are
unchanged. `/skills` listing (`commands/skills.py`) is downstream of `discover_skill_files`
and inherits the change; verify its display-name derivation uses the parent dir, not the stem.

**User CRUD.** All `user_skills_dir / f"{name}.md"` and `skills_dir / f"{name}.md"`
constructions become `<dir> / name / "SKILL.md"`. `_skill_create` creates the parent dir
(`mkdir(parents=True, exist_ok=True)`) before the atomic write; `_scan_or_rollback` and
`_skill_delete` remove the skill folder when they tear a skill down. `_find_user_skill`
probes `<name>/SKILL.md`.

**Usage sidecar.** `_sidecar_path` and `is_agent_created` resolve inside the folder
(`user_skills_dir / name / "SKILL.usage.json"` and `…/name/SKILL.md`). `iter_records` (the read
path the dream/curation daemon consumes for decay) must move with them: its glob changes from
`<dir>.glob(f"*{SIDECAR_SUFFIX}")` to `<dir>.glob(f"*/SKILL{SIDECAR_SUFFIX}")`, and the name
derivation from `path.name.removesuffix(SIDECAR_SUFFIX)` to `path.parent.name`. Missing this is
silent: curation would see zero records and nothing would decay. Sidecars are created lazily as
today; co-location means folder deletion is self-cleaning.

**Migration.** `git mv co_cli/skills/<name>.md co_cli/skills/<name>/SKILL.md` for all six
bundled skills. No content edits to the skill bodies themselves.

## Tasks

### ✓ DONE TASK-1 — Folder discovery in loader + lifecycle
- **files:** `co_cli/skills/loader.py`, `co_cli/skills/lifecycle.py`
- **prerequisites:** none
- **done_when:** discovery globs `*/SKILL.md` in both bundled and user dirs; `SkillInfo.name`
  derives from the parent directory name; a folder containing sibling files/dirs besides
  `SKILL.md` still loads exactly one skill (siblings ignored); a folder with no `SKILL.md` is
  skipped.
- **success_signal:** `load_skills` over a folder-layout fixture returns the same `SkillInfo`
  fields it returned for the equivalent flat file.

### ✓ DONE TASK-2 — User CRUD + usage sidecar on folders
- **files:** `co_cli/tools/system/skills.py`, `co_cli/skills/usage.py`
- **prerequisites:** TASK-1
- **done_when:** `skill_create` makes `<name>/SKILL.md` (creating the dir); `skill_delete`
  removes the whole skill folder; create-rollback (security-scan reject) leaves no orphan
  folder; shadowed-bundled detection reads `<name>/SKILL.md`; usage sidecar reads/writes
  `<name>/SKILL.usage.json` and is removed with the folder; **`iter_records` globs
  `*/SKILL.usage.json` and derives the name from the parent dir** (so the curation/decay read
  path still sees every sidecar — verify a written record round-trips through `iter_records`).
- **success_signal:** create → edit → delete round-trip on a user skill leaves the user dir
  clean, with usage tracking intact across the edit.

### ✓ DONE TASK-3 — Migrate the 6 bundled skills + docs
- **files:** `co_cli/skills/{doctor,review,plan,triage,refactor,skill-creator}/SKILL.md`
  (via `git mv`), `co_cli/skills/lint.py` (docstring), `co_cli/skills/skill-creator.md`→
  `skill-creator/SKILL.md` (output-path wording).
- **prerequisites:** TASK-1
- **done_when:** all six skills live at `<name>/SKILL.md`; `git mv` preserves history (no
  delete+add); lint docstring and skill-creator output guidance say
  `~/.co-cli/skills/<name>/SKILL.md`; no body content changed.
- **success_signal:** `/skills list` after `/skills reload` shows all six unchanged.

### ✓ DONE TASK-4 — Gate + affected tests
- **files:** `tests/test_flow_skill_bundled_library.py` + skill tests touching `.md` paths
  (`test_flow_skills_manage.py`, `test_flow_skills_pin.py`, `test_flow_skill_usage.py`,
  `test_flow_skill_manifest.py`, `test_flow_skill_creator_dispatch.py`, others surfaced by the run).
- **prerequisites:** TASK-1, TASK-2, TASK-3
- **done_when:** the bundled gate loads all six folder skills; tests that construct skill file
  paths use the folder layout; full scoped run green. Assertions stay behavioral.
- **success_signal:** N/A (test update).

## Testing
Scoped run, fail-fast, tee'd:
`uv run pytest -x tests/test_flow_skill_bundled_library.py tests/test_flow_skills_manage.py tests/test_flow_skill_usage.py tests/test_flow_skill_manifest.py tests/test_flow_skill_creator_dispatch.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-skill-folder-model.log`,
then broaden to any failures the run surfaces. Manual: `/skills list`, `/skills reload`,
`skill_view <name>`, and a user-skill create→delete round-trip.

## Open Questions
1. **Q:** Sidecar inside the folder vs alongside it? **A (resolved):** Inside —
   `<name>/SKILL.usage.json`; folder deletion is then self-cleaning (Constraint 5).
2. **Q:** Pre-existing user flat skills? **A (resolved):** No auto-migration (zero-backward-
   compat). They silently stop loading until the user `mv`s them; covered by a release note,
   not code.
3. **Q:** `__init__.py` in every skill folder? **A (resolved):** No — plain skill folders are
   never imported. Only importable-Python payloads need it, added by the dependent plan.
4. **Q:** No flat-file fallback at all? **A (resolved):** Correct, folder-only. This matches
   hermes and openclaw (both folder-only); opencode is the lenient outlier (accepts flat
   `{*.md,**/SKILL.md}` and reads `~/.claude/skills`). co's strict stance is consistent with its
   zero-backward-compat doctrine; the only cost is pre-existing user flat skills going dark,
   handled by the release note (Out of scope), not code.
5. **Q:** Should the skill *directory* be surfaced to the agent for relative-path resolution?
   **A (deferred):** Out of scope here — this plan only enables the layout. All three peers
   inject the skill dir into the agent ("resolve `scripts/foo` against this directory"); co has
   no such affordance. The dependent `skill-documents` plan sidesteps it entirely via `-m`
   module invocation (resolves through `sys.path`), so nothing is blocked. A future skill that
   references a **non-Python** asset (`references/*.md`, a data file) would need this; `SkillInfo.path`
   is the `SKILL.md` file, so `.parent` is the folder and the data is derivable when needed.
   Recorded as a follow-up, not built now.

## Final — Team Lead

Draft for Gate 1.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Blocks `skill-documents`; ship this first, then run `/orchestrate-dev skill-folder-model`.

## Delivery Summary — 2026-06-10

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | discovery globs `*/SKILL.md`; name from parent dir; sibling files ignored; no-`SKILL.md` folder skipped | ✓ pass |
| TASK-2 | create makes `<name>/SKILL.md`; delete removes folder; rollback no orphan; shadowed-bundled reads `<name>/SKILL.md`; sidecar `<name>/SKILL.usage.json`; `iter_records` globs `*/SKILL.usage.json` (name from parent) | ✓ pass |
| TASK-3 | 6 bundled skills at `<name>/SKILL.md` via `git mv` (history preserved); lint + skill-creator doc wording updated | ✓ pass |
| TASK-4 | bundled gate loads all 6; affected tests use folder layout; scoped run green | ✓ pass |

**Tests:** scoped — 82 passed, 0 failed (9 files: bundled_library, skills_manage, skill_usage, skills_pin, skill_manifest, skill_creator_dispatch, dream/skill_housekeeping, system/skill_manage_resets, skills/usage_recall_days).
**Doc Sync:** fixed — skills.md (12 sites + pre-existing phantom `load_skills(settings)` param), 01-system.md (2), dream.md (scope-expanded: 3 confirmed-stale layout refs).

**Scope additions (TL-discovered during execution):**
1. `co_cli/commands/skills.py` — plan only half-flagged it; fixed 3 breaking sites: `_cmd_skills_check` display (`path.stem`→`path.parent.name`), `_cmd_skills_reload` re-scan path, `_classify_skill` (drives `/skills pin/unpin` — would classify every skill "unknown").
2. `co_cli/daemons/dream/_housekeeping.py` — **production gap the plan missed entirely** (escalated, user approved folding in). `_load_user_skill_candidates` globbed `*.md` (matched nothing post-migration → dream skill curation silently dead) and `_archive_user_skill` moved a flat file (orphaning the folder + sidecar). Fixed: glob `*/SKILL.md`, name from parent, archive the whole `<name>/` folder. Same bug class as the `iter_records` gap caught in review. Satisfies Constraint 6 (no behavior drift).

**Overall: DELIVERED**
All four tasks pass `done_when`; lint clean; 82 scoped tests green; docs synced. Two scope additions beyond the written plan (commands.py + dream housekeeping) were required for "no behavior drift" and are folded in.

## Implementation Review — 2026-06-10

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | discovery globs `*/SKILL.md`; name from parent dir; siblings ignored; no-`SKILL.md` skipped | ✓ pass | `loader.py:141,145` glob `*/SKILL.md`; `loader.py:89` `name = path.parent.name`; `lifecycle.py:18,20`. Behaviorally verified: alpha loads with `scripts/`+`references.md` siblings ignored, beta (no SKILL.md) skipped |
| TASK-2 | create makes `<name>/SKILL.md`; delete removes folder; rollback no orphan; shadowed reads `<name>/SKILL.md`; sidecar in folder; `iter_records` globs `*/SKILL.usage.json` | ✓ pass | `skills.py:140-141` mkdir+write; `:260` `shutil.rmtree(path.parent)`; `_scan_or_rollback :104-107` unlink+rmdir-if-empty; `:253,265` shadowed; `usage.py:58,71,122-123` sidecar+iter_records. Round-trip leaves user dir clean |
| TASK-3 | 6 skills at `<name>/SKILL.md` via `git mv`; lint+skill-creator wording | ✓ pass | `git status` shows R (rename, history preserved) for all 6; `lint.py:12` `co_cli/skills/*/SKILL.md`; `skill-creator/SKILL.md:11` `~/.co-cli/skills/<name>/SKILL.md`. Loader returns all 6 |
| TASK-4 | gate loads 6; tests use folder layout; run green | ✓ pass (after fix) | Plan's named test files use folder layout; **3 unnamed test files were missed** (see below) |

Full consumer sweep: every `.glob(` and skill path-join in `co_cli/` uses the folder layout — zero flat-file leftovers across all 6 documented consumer paths (loader, lifecycle, usage, tools/system/skills, commands/skills, dream/_housekeeping).

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Test writes flat `<name>.md`, loads via `*/SKILL.md` → skill never enters catalog; `test_positional_arg_expansion_three_args` failed (`Unknown command: /myskill`) | `test_flow_slash_dispatch.py:63` | blocking | `_write_skill` now mkdirs `<name>/` and writes `SKILL.md` |
| Same flat-write defect in `_write_skill_to_disk` | `test_flow_review_background.py:67` | blocking | Now writes `<name>/SKILL.md` |
| Same flat-write defect; `load_skills` membership assert would fail | `test_flow_bootstrap_config_loading.py:127` | blocking | Now writes `test-bootstrap-skill/SKILL.md` |
| Docstrings call skill name a "filename_stem" — no such field in skill manifest (memory-domain term), contradicts Constraint 8 (name = directory name) | `skills.py:49,356,387,413` | minor | Reworded to "the name listed in the skill manifest" / "the name of an existing user skill" |

**Root cause of the 3 blocking findings:** TASK-4's `files:` enumerated only the test files the author anticipated; three other suites (slash-dispatch, review-background, bootstrap-config) construct skill paths via their own helpers and were not in scope. Same bug class as the `iter_records` and dream-housekeeping gaps caught earlier — flat-layout assumptions scattered across consumers. The scoped run was green precisely because it excluded these three files; only the full suite surfaced them.

### Tests
- Command: `uv run pytest -q` (full suite)
- Result: **652 passed, 0 failed** (1 warning), 428s
- Log: `.pytest-logs/<ts>-review-impl.log`

### Behavioral Verification
- `co status`: N/A — no such command in this CLI (commands: chat/tail/trace/dream/google).
- Skill loading (real `load_skills` over `co_cli/skills/`): all 6 bundled skills load under folder layout — `['doctor','plan','refactor','review','skill-creator','triage']`. **TASK-3 `success_signal` verified.**
- `SkillInfo` field parity (doctor): name/description/body populated, `path` is `SKILL.md`, name derives from parent dir. **TASK-1 `success_signal` verified.**
- Sibling-ignore / no-SKILL.md-skip: confirmed in a temp fixture. **TASK-1 `success_signal` verified.**
- User-skill folder delete round-trip: leaves user dir clean. **TASK-2 `success_signal` verified.**
- TASK-4 `success_signal`: N/A (test update).

### Staged-file note (for `/ship`)
The working tree carries unrelated uncommitted changes — `co_cli/index/{_retrieval,schema}.py`, `evals/*`, `uv.lock`, and four other exec-plans/RESEARCH docs (some deleted). Verified zero skill references; these are pre-existing/coworker work, **not** part of this delivery. Stage only the skill-folder-model files for the ship commit.

### Overall: PASS
All four `done_when` pass; 4 findings (3 blocking test breakages from the same flat-layout miss + 1 minor docstring) fixed; full suite green (652 passed); lint clean; every `success_signal` verified behaviorally.
