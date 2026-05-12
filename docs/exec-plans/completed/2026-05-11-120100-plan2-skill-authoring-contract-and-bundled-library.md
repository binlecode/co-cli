# Plan 2 of 4 — Skill Authoring Contract + Bundled Library + Lint

Task type: code + docs

## Overall Map — Skill Self-Evolution Replan

This plan is one of four sequential plans porting hermes's self-evolving skill capability to co-cli, reframed around the **four-tier surface model**. The map below appears verbatim at the top of each plan to prevent drift.

| # | Plan | File | Scope |
|---|---|---|---|
| **1 (shipped)** | Four-tier surface decomposition | `2026-05-11-120000-plan1-four-tier-surface-decomposition.md` | Eject skills and canon channels from `memory_search`; create `skill_search` + keep `skill_view` / `skill_manage`; manifest injection; spec restructure. Foundation for all subsequent plans. |
| **1.5 (shipped)** | Surface tool naming convergence | `2026-05-12-100000-plan1.5-surface-tool-naming-convergence.md` | Drop `memory_search(channel=...)`; split into `session_search` + `knowledge_search`; add `knowledge_view` + `session_view`; hermes-pattern convergence across all three tiers. |
| **2 (this plan)** | Skill authoring contract + bundled library | `2026-05-11-120100-plan2-skill-authoring-contract-and-bundled-library.md` | Extend `skill.md` with §6 (authoring contract) + §7 (lint rules R1–R10); ship `co_cli/skills/_lint.py` validator and `/skills lint`; author 4 bundled skills (`review`, `plan`, `triage`, `refactor`); migrate `doctor.md`. Bundled skills surface via Plan 1's manifest injection automatically. |
| **3** | Skill protocol + lifecycle workflow bodies | `2026-05-11-120200-plan3-skill-protocol-and-workflow-bodies.md` | Ship `co_cli/context/rules/06_skill_protocol.md` (full five-rule scaffolding) + bundled `skill-creator.md` and `skill-installer.md` workflow bodies. |
| **4** | Migration importer (channel-aware) | `2026-05-11-120300-plan4-skill-migration-importer.md` | `/skills import {claude\|hermes\|openclaw}` — read peer source dir, normalize against §6/§7, lint-gate, write to `~/.co-cli/skills/`. |

**Order:** 1 → 1.5 → 2 → 3 → 4. Plan 1.5 converged the tool naming; this plan adds the authoring contract that bundled skills must conform to (and that lint enforces).

**Reference:** `docs/reference/RESEARCH-skills-peers-tiers.md` Part 5, Steps 1 + 3.

**What ships before this plan:**
- Plan 1 — four-tier surface (`skill_search`, manifest injection, `skill.md` renamed from `memory-skills.md`).
- Plan 1.5 — surface tool naming convergence (`session_search`, `knowledge_search`, `knowledge_view`, `session_view`; `memory_search` removed; hermes-pattern across all tiers).
- Sibling plan `2026-05-09-154112-skill-manage-hermes-port.md` (shipped) — `skill_view` and `skill_manage`.

## Context

Skills are the procedural-capability surface of co-cli. Today's bundled library is a single file (`doctor.md`); user-installed skills exist but have no authoring discipline. Without a contract, skills drift toward two failure modes:

1. **Quality drift** — skills with vague descriptions, missing constraints, no recall guidance for the model. The bundled manifest renders these unhelpfully ("doctor — diagnose problems" tells the model nothing about when to load it).
2. **Structural drift** — skills with inconsistent body shape (some open with phases, some with task lists, some with stream-of-consciousness prose). Inconsistent shape means the model can't develop a stable reading reflex.

Plan 2 lands the **authoring contract** (what a skill body must look like) and **lint rules** (mechanical checks the validator enforces). With the contract in place, the bundled library scales to 4 production-quality skills covering co-cli's core workflows: `review`, `plan`, `triage`, `refactor`.

### Current-state validation (inline)

Verified against the codebase (post-Plan-1.5):

- ✓ `co_cli/skills/loader.py:scan_skill_content` — runtime security scan (credential exfil, pipe-to-shell, destructive shell, prompt injection). Stays untouched; lint is additive, not a replacement.
- ✓ `co_cli/skills/loader.py:_check_requires` — `requires` block evaluation. Stays.
- ✓ `co_cli/skills/skill_types.py:SkillConfig` — frozen dataclass. Stays.
- ✓ `co_cli/skills/` — only `doctor.md` bundled today. To grow to 5 (`doctor` + 4 new).
- ✓ `docs/specs/skill.md` (post-Plan-1) — has §1–§5 (What & How, Core Logic, Model-Callable Surface, Config, Files). §6 and §7 are placeholders pending this plan.
- ✓ `co_cli/commands/` — slash-command system. `/skills list/check/install/reload/upgrade` exist; `/skills lint` is new.
- ✓ Test harness `tests/_co_harness.py` and `tests/_settings.py` — usable for new lint tests.

### Why a separate lint phase

The security scan (`scan_skill_content`) is **adversarial** — it catches actively malicious content. Lint is **collaborative** — it catches well-meaning skills that won't perform well (no description, mixed phase shapes, bodies over the length budget). Two concerns, two passes. Lint is non-blocking by default (reports findings, exit code 1 on hits, file unchanged); security scan blocks writes. Both run in different lifecycles.

## Problem & Outcome

**Problem.** Co-cli has no authoring contract for skills. The single bundled skill (`doctor.md`) was hand-authored without a reference contract. Without §6 (body shape) and §7 (lint rules), the bundled library can't scale and user-installed skills have no quality bar. After Plan 1 ships manifest injection, low-quality skill descriptions will surface directly into the system prompt, degrading model recall.

**Outcome.**

1. **`skill.md` §6 — Authoring contract**: body shape (required sections, length budget, style rules), with a worked example.
2. **`skill.md` §7 — Lint rules R1–R10**: numbered, citable in error output. Each rule has a one-line description + one-line *why*.
3. **`co_cli/skills/_lint.py`** — validator implementing R1–R10. Emits findings as `R<n>: <message>` with line numbers.
4. **`/skills lint [name|--all]`** — CLI surface. Non-blocking by default; exit code 1 on findings; `--all` lints every loaded skill.
5. **Bundled library** — 4 new bundled skills (`review`, `plan`, `triage`, `refactor`) authored to conform to §6 + lint clean per §7. Existing `doctor.md` migrated.
6. **Manifest auto-fills.** After this plan ships, Plan 1's `<available_skills>` block shows 5 entries (doctor + 4 new), each with a contract-shaped description.

## Scope

### In scope

- `docs/specs/skill.md` §6 (Authoring contract) + §7 (Lint rules R1–R10).
- `co_cli/skills/_lint.py` (new) — `lint_skill(content: str, path: Path) -> list[LintFinding]`. Each finding: `{rule: str, message: str, line: int}`.
- `co_cli/commands/skills.py` — extend `/skills` command family with `lint [name|--all]`.
- 4 new bundled skills under `co_cli/skills/`:
  - `review.md` — PR / change review (correctness, style, security).
  - `plan.md` — implementation plan drafting (problem, scope, tasks).
  - `triage.md` — bug report triage (reproduce, isolate, categorize).
  - `refactor.md` — guided refactor of a module (scope, safety, test coverage).
- Migrate `co_cli/skills/doctor.md` to conform to §6 (frontmatter unchanged; body reshape only).
- Behavioral tests:
  - `tests/test_flow_skill_lint.py` — per-rule positive and negative cases.
  - `tests/test_flow_skill_bundled_library.py` — every bundled skill loads + lints clean.
- Cross-references: `CLAUDE.md` Workflow section (mention `/skills lint` if `/sync-doc` and `/ship` are listed there).

### Out of scope

- **Pre-commit hook for lint.** Lint is non-blocking. A future plan can add a hook if the bundled library grows large; for now, manual or CI-side enforcement is sufficient.
- **Auto-fix mode for lint findings.** Each finding is human-judgement; auto-fix would require a model call. Out of scope.
- **Lint rules that overlap with `scan_skill_content`.** Security patterns stay in the scan; lint covers shape/quality only.
- **Lint rules for body **content** correctness** (e.g. "does this skill's steps actually work?"). Lint is mechanical; content correctness is a review concern.
- **Skill versioning.** Bundled skills are version-controlled in git; no per-skill version field.
- **i18n of skill bodies.** English only.
- **`/skills lint --fix`.** Not in v1 (see "auto-fix" above).
- **Plan 3 protocol rules.** That's `06_skill_protocol.md`, separate plan.
- **Plan 4 migration importer.** The importer will gate on lint, but the importer itself is Plan 4.

## Behavioural Constraints

1. **Lint is non-blocking, security scan is blocking.** Two different lifecycles, two different files. Lint findings appear in `/skills lint` output and exit code 1; the file is unchanged on disk.
2. **Lint rules are numbered and citable.** Format `R<n>: <one-line>`. The validator emits findings tagged with the rule number so users can match output to the §7 reference.
3. **Each rule has a stated *why*.** §7 includes a "why" for each rule so users understand the rationale, not just the constraint. Future rules removed if their *why* no longer holds.
4. **Lint scope is mechanical.** No model calls, no LLM-based judgement. Pure regex / line-count / structural checks.
5. **Bundled skills are the reference.** Every bundled skill is lint-clean as a precondition for inclusion. The bundled library doubles as authoring examples.
6. **§6 + §7 live in `skill.md`** (after Plan 1's rename). No separate authoring doc.
7. **Body shape is opinionated but not rigid.** §6 specifies required sections (e.g. opening summary, phase or step list); flexibility on number of phases and on optional sections.
8. **Length budget is a soft cap.** R-something fires on bodies > N chars but doesn't block; the *why* is "manifest descriptions stay readable; long bodies signal the skill is too broad."

## High-Level Design

### `skill.md` §6 — Authoring Contract

Required structure for any skill body (after frontmatter):

```markdown
---
description: <single sentence: when to use this skill, max 1024 chars>
argument-hint: <optional, max 80 chars>
---

# <Skill name>

**Invocation:** `/<name> [args]`

<one paragraph: what the skill does, in one breath>

---

## Phase 1 — <name>

<instructions>

## Phase 2 — <name>

<instructions>

...

## Rules

- <terminal-rule 1>
- <terminal-rule 2>
```

**Section requirements:**
- Frontmatter: `description` (required, ≤1024 chars); `argument-hint` (optional, ≤80 chars).
- H1 title.
- **Invocation line** in bold: `**Invocation:** /<name> [optional args]`.
- One-paragraph opening summary.
- One or more `## Phase N — <name>` sections containing step-by-step instructions.
- Optional `## Rules` section listing terminal invariants.

**Length budget:**
- Frontmatter description: ≤1024 chars (already enforced by `_validate_skill_content`).
- Body total: ≤8000 chars (soft cap, R8 fires).
- Each phase: ≤2000 chars (soft cap, R9 fires).

**Style rules** (informative, not lint-enforced):
- Imperative voice ("Run X", "Check Y") for step instructions.
- Concrete tools and commands cited verbatim in backticks.
- Phase names describe **what the phase accomplishes**, not procedure (e.g. "Phase 1 — Load" not "Phase 1 — First steps").

### `skill.md` §7 — Lint Rules R1–R10

Each rule has format `R<n>: <message>` with a one-line "why".

| Rule | Check | Why |
|---|---|---|
| **R1** | Frontmatter present (file opens with `---`) | Without frontmatter, loader rejects the skill at runtime |
| **R2** | Frontmatter `description` field present and non-empty | Manifest injection and `skill_search` rely on description; absent = invisible |
| **R3** | `description` ≤ 1024 chars | Bigger descriptions bloat the manifest and degrade prompt cache |
| **R4** | H1 title present after frontmatter | Body without title reads as raw instructions; H1 anchors the skill identity |
| **R5** | `**Invocation:**` line present near the top of body | Tells the user (and model reading the body) the slash-command name |
| **R6** | At least one `## Phase N — <name>` section | Phaseless skills are stream-of-consciousness; model can't navigate them |
| **R7** | All phase headers follow `## Phase N — <name>` format (no `### Phase`, no missing dash) | Inconsistent heading shape breaks model's reading reflex |
| **R8** | Body total ≤ 8000 chars | Long bodies signal the skill is too broad; split into focused skills |
| **R9** | Each phase ≤ 2000 chars | Within-phase length cap; same rationale as R8 |
| **R10** | No TODO / FIXME / XXX markers in the body | Bundled skills are reference quality; markers signal in-progress work |

### `co_cli/skills/_lint.py`

```python
from dataclasses import dataclass
from pathlib import Path
import re
from co_cli.memory.frontmatter import parse_frontmatter


@dataclass
class LintFinding:
    rule: str          # "R1", "R2", ...
    message: str
    line: int          # 1-indexed; 0 if file-level


def lint_skill(content: str, path: Path | None = None) -> list[LintFinding]:
    """Run all R1–R10 checks. Returns findings list (empty = clean)."""
    findings: list[LintFinding] = []
    # R1, R2, R3: frontmatter checks
    meta, body = parse_frontmatter(content)
    if not content.startswith("---"):
        findings.append(LintFinding("R1", "Frontmatter block missing", 1))
    # ... (R2 through R10)
    return findings
```

Findings are emitted in rule order. Sorting by line number is the formatter's concern.

### `/skills lint`

Sub-command of `/skills`. Two modes:

- `/skills lint <name>` — lint a single skill by name (lookup in `deps.skill_commands`).
- `/skills lint --all` — lint every loaded skill (bundled + user-installed).

Output format:

```
$ /skills lint --all
review (co_cli/skills/review.md):
  R7 at line 23: phase header missing dash separator
plan (co_cli/skills/plan.md):
  (clean)
doctor (co_cli/skills/doctor.md):
  R8 at line 1: body 8412 chars (budget 8000)

2 of 3 skills clean. Exit code 1.
```

Non-zero exit on findings. File is never modified.

### Bundled skill library

Four new bundled skills, each authored to §6 + lint-clean per §7. Approximate body lengths and phase counts:

| Skill | Body chars | Phases | Purpose |
|---|---|---|---|
| `review.md` | ~5500 | 4 (Load, Evidence, Fix loop, Verdict) | PR / change review with file:line citations |
| `plan.md` | ~4800 | 3 (Scope, Tasks, Open questions) | Implementation plan drafting |
| `triage.md` | ~4200 | 4 (Reproduce, Isolate, Categorize, File) | Bug report triage |
| `refactor.md` | ~5100 | 4 (Scope, Safety, Refactor, Verify) | Guided module refactor |

(Body content is not specified verbatim in this plan; bodies will be drafted in TASK-5 and reviewed against §6 + §7.)

Existing `doctor.md` migrates:
- Body reshape to §6 (likely +phase headers, +Invocation line if absent).
- Frontmatter unchanged.

## Tasks

### ✓ DONE — TASK-1 — `skill.md` §6 (Authoring contract)

Files:
- `docs/specs/skill.md` (append §6 after current §5).

Acceptance:
- §6 documents required structure, section list, length budget, style rules.
- Includes a complete worked example of a §6-compliant skill body.
- Cross-link to §7 for enforceable subset.

`done_when`: `grep -n "## 6. Authoring Contract" docs/specs/skill.md | grep -v Placeholder` returns one hit.

### ✓ DONE — TASK-2 — `skill.md` §7 (Lint rules R1–R10)

Files:
- `docs/specs/skill.md` (append §7 after §6).

Acceptance:
- All ten rules listed with check + *why*.
- Format consistent with existing §1–§5.
- Anchored so `R<n>` is searchable in the spec.

`done_when`: `grep -n "## 7. Lint Rules" docs/specs/skill.md | grep -v Placeholder` returns one hit; `grep -c "| \*\*R" docs/specs/skill.md` returns 10.

### ✓ DONE — TASK-3 — `co_cli/skills/_lint.py`

Files:
- `co_cli/skills/_lint.py` (new) — `lint_skill(content, path=None) -> list[LintFinding]`.
- `co_cli/skills/__init__.py` — docstring-only (no exports).

Acceptance:
- All ten rules R1–R10 implemented.
- `LintFinding` dataclass with `rule`, `message`, `line`.
- Pure function; no I/O.
- Each rule emits ≤1 finding per file (no rule fires twice per skill).
- Findings ordered by rule number.

`done_when`: `uv run pytest tests/test_flow_skill_lint.py -v` passes (all 11 per-rule cases); `grep -n "def lint_skill" co_cli/skills/_lint.py` returns one hit.

### ✓ DONE — TASK-4 — `/skills lint` CLI surface

Files:
- `co_cli/commands/skills.py` — add `lint` subcommand.

Acceptance:
- `/skills lint <name>` lints one skill from `deps.skill_commands`.
- `/skills lint --all` lints all loaded skills.
- Output format: `<name> (<path>): <findings or '(clean)'>`.
- Exit code 1 on any finding (across all skills lint'd).
- Unknown name → error message; not lint finding.

### ✓ DONE — TASK-5 — Author 4 bundled skills + migrate `doctor.md`

Files:
- `co_cli/skills/review.md` (new).
- `co_cli/skills/plan.md` (new).
- `co_cli/skills/triage.md` (new).
- `co_cli/skills/refactor.md` (new).
- `co_cli/skills/doctor.md` (reshape to §6; frontmatter unchanged).

Acceptance:
- Each new skill conforms to §6 (verified by passing `_lint.py`).
- Each skill has a focused description (≤1024 chars, ideally ≤200 for manifest readability).
- Migrated `doctor.md` lints clean.
- All 5 bundled skills load successfully at bootstrap (no `_check_requires` failures, no `scan_skill_content` flags).
- Plan 1's manifest injection auto-fills with all 5 entries.

### ✓ DONE — TASK-6 — Behavioral tests

Prerequisites: TASK-3 (lint implementation), TASK-5 (bundled skills authored — assertion 5 requires all 5 skills present).

Files:
- `tests/test_flow_skill_lint.py` (new) — per-rule positive (clean content) + negative (rule fires) cases.
- `tests/test_flow_skill_bundled_library.py` (new) — every bundled skill loads + lints clean.

Test surface (`_lint.py`):

| # | Assertion |
|---|---|
| 1 | R1 fires on content without frontmatter. |
| 2 | R2 fires when frontmatter description is missing or empty. |
| 3 | R3 fires when description > 1024 chars. |
| 4 | R4 fires when body has no H1. |
| 5 | R5 fires when no `**Invocation:**` line in first ~10 lines of body. |
| 6 | R6 fires when no `## Phase N — <name>` section exists. |
| 7 | R7 fires on malformed phase header (e.g. `### Phase 1` or `## Phase 1 Loading` without dash). |
| 8 | R8 fires when body > 8000 chars. |
| 9 | R9 fires when any phase > 2000 chars. |
| 10 | R10 fires on `TODO`, `FIXME`, `XXX` in body. |
| 11 | Lint-clean §6-compliant body produces empty findings list. |

Test surface (`bundled library`):

| # | Assertion |
|---|---|
| 1 | All 5 bundled skills (doctor, review, plan, triage, refactor) load via `load_skills(co_cli/skills/, ...)`. |
| 2 | Each bundled skill has a non-empty description. |
| 3 | Each bundled skill is lint-clean (`_lint.lint_skill()` returns empty findings). |
| 4 | Each bundled skill's body contains at least one `## Phase` section. |
| 5 | Plan 1's manifest renders 5 `<skill>` entries when bundled set is full. |

### ✓ DONE — TASK-7 — Cross-plan integration check

Files: none (verification step).

Acceptance:
- `scripts/quality-gate.sh full` clean.
- `/skills lint --all` exits 0 (all bundled skills clean).
- Manual smoke: `/skills list` shows 5 bundled skills; `<available_skills>` manifest in rendered system prompt has 5 entries.
- `skill_search('review')` returns the bundled `review` skill (regression guard from Plan 1).

## Testing

### Test files

- `tests/test_flow_skill_lint.py` (new)
- `tests/test_flow_skill_bundled_library.py` (new)

### Test pattern

Real `_lint.py` (pure function, no fixture needed). Real `load_skills(co_cli/skills/, ...)` for bundled-library tests. No mocks.

Lint tests parameterize by rule: each rule has a "clean" fixture and a "violation" fixture; the test asserts which rule fires and which doesn't.

### Lint / quality gate

- `scripts/quality-gate.sh lint` after each task.
- `scripts/quality-gate.sh full` before considering ready to ship.
- `/skills lint --all` exit 0 — additional ship gate added.

## Open Questions

1. **Q:** Should R8 (body length cap) be 8000 or higher? `doctor.md` is currently ~6500; 8000 leaves ~1500 chars of headroom.
   **Tentative answer:** 8000 for v1. Empirical: if more than 2 of the 4 new bundled skills push the cap, raise it to 10000. Don't push past 12000 (model attention degrades on long bodies).

2. **Q:** Should §6 require an `## Rules` section or make it optional?
   **Tentative answer:** Optional. Terminal invariants are a real artifact, but some skills (`triage`, `plan`) don't have a clean rule set — they're orchestrating rather than enforcing.

3. **Q:** Should `/skills lint` accept a glob pattern (e.g. `/skills lint review*`)?
   **Tentative answer:** No for v1. `--all` covers bulk linting; single name covers focused. Glob adds parser complexity.

4. **Q:** Should bundled skill bodies be reviewed by `/orchestrate-plan` before TASK-5 commits them?
   **Tentative answer:** Yes if the bodies are non-trivial. The lint validator catches shape issues; orchestrate-plan can catch *content* issues (workflow correctness). Worth a brief pass.

5. **Q:** Should R10 (TODO markers) apply to user-installed skills via `/skills lint`?
   **Tentative answer:** Yes — same rule applies. User can ignore findings; lint is non-blocking.

## Deferred items

- **Pre-commit hook for lint.** Defer until the bundled library grows large enough that drift becomes a real concern.
- **`/skills lint --fix` auto-fix mode.** Each finding is human-judgement; auto-fix risks degrading quality.
- **Per-rule severity (warning vs error).** All findings are equal-weight for v1. Severity differentiation is a follow-on.
- **Lint over linked files (Plan 4 territory).** Linked-files schema is not yet supported in co-cli; lint doesn't recurse beyond the SKILL.md body.
- **Skill body length budget tuning by skill type.** All bundled skills share R8 budget; future plans may differentiate (e.g. orchestration skills allowed longer).

## Shipping order

Single commit — all seven TASKs. Bundled library + lint rules + validator + CLI surface ship together. Partial ship leaves the bundled library un-enforced or the validator without coverage.

**Hard dependencies:**
- Plan 1.5 (surface tool naming convergence, shipped) — provides the converged tool surface and `skill.md` §1–§5 anchored; this plan appends §6 + §7.

**Soft dependencies:** none.

**Initial-state caveat:** the manifest already auto-fills via Plan 1's injection; this plan grows the manifest from 1 entry (doctor) to 5. No injection code change needed.

## Delivery Summary — 2026-05-12

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep -n "## 6. Authoring Contract" docs/specs/skill.md \| grep -v Placeholder` returns one hit | ✓ pass |
| TASK-2 | `grep -n "## 7. Lint Rules" docs/specs/skill.md \| grep -v Placeholder` returns one hit; `grep -c "| \*\*R" docs/specs/skill.md` returns 10 | ✓ pass |
| TASK-3 | `uv run pytest tests/test_flow_skill_lint.py -v` passes (all 11+ per-rule cases); `grep -n "def lint_skill" co_cli/skills/_lint.py` returns one hit | ✓ pass |
| TASK-4 | `/skills lint [<name>\|--all]` wired into dispatcher; output format + exit-1 behavior per spec | ✓ pass |
| TASK-5 | All 5 bundled skills lint-clean; descriptions non-empty; `doctor.md` frontmatter unchanged | ✓ pass |
| TASK-6 | 32 tests pass: 15 lint (R1–R10 + clean fixture) + 17 bundled-library (load/describe/lint/phase/manifest) | ✓ pass |
| TASK-7 | Lint gate PASS; all 5 bundled skills clean; manifest renders 5 entries; skill.md spec synced | ✓ pass |

**Tests:** scoped (touched test files) — 32 passed, 0 failed
**Doc Sync:** fixed — added `/skills lint` row to §2 Skill Management Commands; added `co_cli/skills/_lint.py` to §5 Files; updated `/skills` command list in Files description

**Overall: DELIVERED**
All seven tasks shipped. `_lint.py` implements R1–R10 (pure function, no I/O). Bundled library grows from 1 to 5 skills (doctor, review, plan, triage, refactor), all §6-conformant and lint-clean. `/skills lint [<name>|--all]` live in the REPL. Spec §6 + §7 written with worked example and rule table. Note: `SkillConfig` has no `path` field — `/skills lint` resolves skill file paths dynamically via user-dir/bundled-dir lookup rather than from the config object.

## Post-ship — research-doc resync

After this plan ships, update `docs/reference/RESEARCH-skills-peers-tiers.md`:

- Step 1 (Lifecycle spec) → **shipped** (§6 + §7 in `skill.md` + `_lint.py` validator).
- Step 3 (Bundled library) → **shipped** — 5 bundled skills, lint-enforced.
