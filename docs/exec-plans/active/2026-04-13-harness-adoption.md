# TODO: Harness Adoption

Gap analysis and phased adoption plan against the Harness engineering pattern.
Reference: https://openai.com/index/harness-engineering/
Reference implementation: https://github.com/binlecode/hermes-harness

Items are sequenced by coupling and coordination cost, not just ROI in isolation.
Items 1+2 are coupled and must ship together. Item 4 requires atomic multi-file coordination.

---

## Phase 1 — Enforcement harness (~3h, no dependencies)

### 1+2. Version-controlled hooks with pre-push enforcement (coupled)

`install-hooks.sh` generates hooks into `.git/hooks/` at runtime — unversioned and forgotten on
fresh clone. Pre-commit runs lint only; nothing blocks a push with broken tests.
These two are tightly coupled: a pre-push hook that isn't version-controlled solves nothing.

Tasks:
- [x] create `.githooks/pre-commit` — runs `scripts/quality-gate.sh lint` (same logic as current `install-hooks.sh`)
- [x] create `.githooks/pre-push` — runs `scripts/quality-gate.sh full`
- [x] add `git config core.hooksPath .githooks` to CLAUDE.md setup section
- [x] retire `scripts/install-hooks.sh` (or replace body with one-liner pointing to `.githooks/`)

Acceptance criteria:
- `.githooks/` is checked into repo and git-versioned
- commit is blocked by lint failures, push is blocked by test failures
- fresh clone + `git config core.hooksPath .githooks` → both hooks active immediately

---

### 3. Structural tests

All tests are behavior tests. Nothing verifies that required docs exist, required packages haven't
been deleted, or the docs tree hasn't drifted. The harness cannot enforce itself.

Tasks:
- [x] add `tests/test_repo_structure.py`
- [x] assert required docs: `CLAUDE.md`, `README.md`, `docs/DESIGN-system.md`, `docs/DESIGN-core-loop.md`, `docs/DESIGN-tools.md`, `docs/DESIGN-context.md`
- [x] assert required packages: `co_cli/tools/`, `co_cli/context/`, `co_cli/config/`, `co_cli/knowledge/`, `co_cli/memory/`, `co_cli/bootstrap/`, `co_cli/commands/`, `co_cli/observability/`
- [ ] assert `docs/ARCHITECTURE.md` once item 8 is shipped

Acceptance criteria:
- `pytest` fails if any required doc or package goes missing
- runs as part of `scripts/quality-gate.sh full`

---

### 4. GitHub Actions CI

No CI. Quality gate is purely local and voluntary. Nothing confirms tests pass on a clean
environment after push.

**Risk**: several tests require Ollama running locally and will fail on a clean Ubuntu runner.
CI must scope to the tests that don't require local model infrastructure, or use a pytest marker
to skip Ollama-backed tests. Resolve the test scope before writing the workflow.

Tasks:
- [x] audit test suite: identify which tests require Ollama or other local services
- [x] add `pytest.mark.local` (or similar) to tests that cannot run in CI without infrastructure
- [x] add `.github/workflows/ci.yml` triggering on push/PR to main
- [x] CI runs `scripts/quality-gate.sh full` with Ollama-gated tests skipped (`-m "not local"`)
- [x] confirm Python version in workflow matches `requires-python = ">=3.12"`

Acceptance criteria:
- CI runs automatically on every push and PR
- same gate command as local — no divergence in what is checked
- green CI on a clean ubuntu environment without Ollama

---

## Phase 2 — Workflow change (~2h, requires atomic multi-file coordination)

### ✓ DONE — 5. exec-plans lifecycle (replace TODO delete)

Current workflow explicitly deletes `docs/TODO-<slug>.md` after Gate 2 PASS. Plan, decisions,
and scope history disappear.

Reference design: `hermes-harness` at `~/workspace_genai/hermes-harness`.

**Naming convention**: `YYYY-MM-DD-<slug>.md` — date is the creation date. Migration uses
today's date (2026-04-13) for all existing TODOs.

**Coordination risk**: all files below must land in one commit — if any stays in "delete" mode,
the agent workflow diverges from the new policy mid-delivery:
- `CLAUDE.md` (artifact lifecycle + dev workflow sections)
- `orchestrate-dev` skill prompt
- `deliver` skill prompt
- auto-memory temp-file deletion entry (replace, not update)
- `test_repo_structure.py` (add dir assertions)

Tasks:
- [x] create `docs/exec-plans/active/` with `.gitkeep`
- [x] create `docs/exec-plans/completed/` with `.gitkeep`
- [x] update `tests/test_repo_structure.py`: assert both `docs/exec-plans/active/` and `docs/exec-plans/completed/` exist
- [x] update `CLAUDE.md` artifact lifecycle section: plans live at `docs/exec-plans/active/YYYY-MM-DD-<slug>.md`; on ship use `git mv` to `completed/`; remove "Delete TODO-<slug>.md" line from dev workflow diagram
- [x] update `orchestrate-dev` skill: use timestamped naming, emit `git mv active/ → completed/` at ship — not delete
- [x] update `deliver` skill: same
- [x] replace auto-memory entry: remove temp-file deletion rule; write new entry describing exec-plans lifecycle
- [x] migrate all `docs/TODO-*.md` files: rename to `2026-04-13-<slug>.md` and move to `docs/exec-plans/active/` via `git mv`

Acceptance criteria:
- `docs/exec-plans/active/` and `docs/exec-plans/completed/` exist and are git-tracked
- structural test asserts both dirs exist
- CLAUDE.md, both skill prompts, and memory all agree: `git mv` to `completed/`, no deletion
- all current TODO files migrated with timestamped names in `active/`
- git log shows the full lifecycle of every plan

---

## Phase 3 — Medium value (~3.5h)

### 6+7. Release automation + drop CHANGELOG.md (coupled)

CHANGELOG.md is manually maintained and duplicates commit history. Dropping it only makes sense
once automated releases exist to replace it as the release artifact.

Tasks:
- [x] add `.github/workflows/release.yml` triggering on `v*` tags
- [x] build wheel with `uv build`, attach to GitHub Release
- [x] add note to CLAUDE.md: "git history is the changelog; releases use GitHub Releases"
- [x] archive or remove CHANGELOG.md
- [x] remove "Step 3 — Sync CHANGELOG.md" section from `orchestrate-dev` SKILL.md Phase 5
- [x] remove CHANGELOG.md block from `deliver` SKILL.md Phase 4

Acceptance criteria:
- `git tag vX.Y.Z && git push origin vX.Y.Z` produces a GitHub Release with wheel attached
- no actively-maintained CHANGELOG.md in repo
- neither skill prompt references CHANGELOG.md

---

### 8. `docs/ARCHITECTURE.md` entrypoint

Architecture lives in `docs/DESIGN-system.md` which is thorough but doesn't explicitly document
one-way dependency rules. A coding agent looks for `ARCHITECTURE.md` — it doesn't exist.

Tasks:
- [ ] add `docs/ARCHITECTURE.md` as slim entrypoint stating dependency direction
- [ ] document the one-way rule: `main → bootstrap → agent → tools/context/config/knowledge/memory`
- [ ] point to `DESIGN-system.md` for full detail
- [ ] add `docs/ARCHITECTURE.md` to structural test assertions (item 3)

Acceptance criteria:
- dependency direction is explicitly stated and findable in one step
- `DESIGN-system.md` remains authoritative for deep architecture detail

---

## Phase 4 — Polish (as time allows)

### 9. Split CLAUDE.md + HARNESS.md

CLAUDE.md at 182 lines covers orientation, engineering rules, testing discipline, review
checklist, and workflow all in one file. Dense for cold-start agent navigation.

Tasks:
- [ ] extract orientation layer into `docs/HARNESS.md` (pattern origin, principles, repo map, SDLC flow)
- [ ] keep CLAUDE.md as working rules + setup + entry point index (target: under 80 lines)
- [ ] update CLAUDE.md to list `docs/HARNESS.md` as first entry point

Acceptance criteria:
- new coding agent can orient from CLAUDE.md in under 30 seconds
- HARNESS.md contains the full pattern explanation and reading order

---

### 10. `docs/product-specs/`

Product intent is split across README (user-facing) and DESIGN files (implementation-facing).
No single document captures what co-cli is, is not, and what success looks like.

Tasks:
- [ ] add `docs/product-specs/assistant-core.md` with goal, functional areas, non-goals, success criteria
- [ ] source content from README product description + DESIGN-system.md overview

---

### 11. Move ROADMAP to `docs/` root

`docs/reference/ROADMAP-co-evolution.md` is a roadmap, not reference material. Wrong location.

Tasks:
- [ ] move to `docs/ROADMAP.md`
- [ ] add status fields and phase dependency chain

---

### 12. `docs/PLANS.md`

Planning policy is implicit in CLAUDE.md workflow section. Not explicitly documented as a
standing rule.

Tasks:
- [ ] add `docs/PLANS.md` covering exec-plan lifecycle, when to write a plan, changelog policy, release notes policy

---

## Summary

| Phase | # | Gap | Effort | Note |
|-------|---|-----|--------|------|
| 1 | 1+2 | Version-controlled hooks + pre-push | 30m | Coupled — ship together |
| 1 | 3 | Structural tests | 1h | |
| 1 | 4 | GitHub Actions CI | 1h | Audit test scope first |
| 2 | 5 | exec-plans lifecycle | 2h | Atomic: CLAUDE.md + 2 skills + memory |
| 3 | 6+7 | Release automation + drop CHANGELOG | 2h | Coupled — ship together; includes skill cleanup |
| 3 | 8 | `docs/ARCHITECTURE.md` entrypoint | 1h | |
| 4 | 9 | Split CLAUDE.md + HARNESS.md | 2h | Medium disruption |
| 4 | 10 | `docs/product-specs/` | 1h | |
| 4 | 11 | Move ROADMAP to `docs/` root | 15m | |
| 4 | 12 | `docs/PLANS.md` | 30m | |
