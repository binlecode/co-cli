# Plan 3 of 4 — Skill Protocol + Lifecycle Workflow Bodies

Task type: code + docs

## Overall Map — Skill Self-Evolution Replan

This plan is one of four sequential plans porting hermes's self-evolving skill capability to co-cli, reframed around the **four-tier surface model**. The map below appears verbatim at the top of each plan to prevent drift.

| # | Plan | File | Scope |
|---|---|---|---|
| **1 (shipped)** | Four-tier surface decomposition | `2026-05-11-120000-plan1-four-tier-surface-decomposition.md` | Eject skills and canon channels from `memory_search`; create `skill_search`; manifest injection; spec restructure. Foundation. |
| **1.5 (shipped)** | Surface tool naming convergence | `2026-05-12-100000-plan1.5-surface-tool-naming-convergence.md` | Drop `memory_search(channel=...)`; split into `session_search` + `knowledge_search`; add `knowledge_view` + `session_view`; hermes-pattern convergence across all three tiers. |
| **2** | Skill authoring contract + bundled library | `2026-05-11-120100-plan2-skill-authoring-contract-and-bundled-library.md` | `skill.md` §6 + §7; `_lint.py` + `/skills lint`; 4 new bundled skills + `doctor.md` migration. |
| **2.5 (shipped)** | Skill prompt discipline | `2026-05-12-150000-plan2.5-skill-prompt-discipline.md` | Enrich `skill_manage`/`skill_search`/`skill_view` docstrings with behavioral triggers; ship `06_skill_protocol.md` (five-reflex rule). Extracted from Plan 3 + hermes comparison gap. |
| **3 (this plan)** | Skill lifecycle workflow bodies | `2026-05-11-120200-plan3-skill-protocol-and-workflow-bodies.md` | Bundled `skill-creator.md` and `skill-installer.md` workflow bodies. (`06_skill_protocol.md` extracted to Plan 2.5; TASK-1 below.) |
| **4** | Migration importer (channel-aware) | `2026-05-11-120300-plan4-skill-migration-importer.md` | `/skills import {claude\|hermes\|openclaw}` — read peer source dir, normalize against §6/§7, lint-gate, write to `~/.co-cli/skills/`. |

**Order:** 1 → 1.5 → 2 → 3 → 4. Plan 3 can ship independently of Plan 4 (both depend on 1.5 + 2).

**Reference:** `docs/reference/RESEARCH-skills-peers-tiers.md` Part 5, Step 2 (drift discipline) + Step 4 (awareness layer).

**What ships before this plan:**
- Plan 1 — four-tier surface, `skill_search` tool, bundled manifest injection.
- Plan 1.5 — surface tool naming convergence (`session_search`, `knowledge_search`, `knowledge_view`, `session_view`; `memory_search` removed).
- Plan 2 — `skill.md` §6 + §7, bundled library (`doctor`, `review`, `plan`, `triage`, `refactor`).
- Sibling shipped plans — `skill_view`, `skill_manage(create/edit/patch/delete/install)`.

## Context

Plan 1 ships the *surface* (where skills live). Plan 2 ships the *contract* (what skills look like). This plan ships the *discipline* — the prompt-side rules that drive the model to actually use and evolve skills:

- **Discovery** — when to scan the bundled manifest vs. call `skill_search`.
- **Use** — what to do once a body is loaded.
- **Drift** — when to patch an outdated skill.
- **Create** — when to promote a recurring procedure into a skill.
- **Offer-to-save** — when to surface a skill creation suggestion to the user.

Hermes encodes all five reflexes in its system prompt (`SKILLS_GUIDANCE` constant + mandatory `<available_skills>` block in `prompt_builder.py:850–876`). Co-cli's current state has only a thin one-liner in `04_tool_protocol.md`'s Memory section (and Plan 1 removed even that). Without the five reflexes, co-cli ships the *tools* (`skill_search`, `skill_view`, `skill_manage`) but not the *behavior* that makes them productive.

### Current-state validation (inline)

Verified against the codebase (post-Plan-1.5; Plan-2 not yet shipped):

- ✓ `co_cli/context/rules/` — five rules files (01_identity, 02_safety, 03_reasoning, 04_tool_protocol, 05_workflow). `06_` is the next free slot.
- ✓ `co_cli/context/rules/04_tool_protocol.md` — Memory section (post-Plan-1) no longer mentions skills. Clean foundation for the protocol file to land.
- ✓ `co_cli/skills/` — 5 bundled skills (Plan 2). Two new ones land in this plan: `skill-creator.md`, `skill-installer.md`.
- ✓ Plan 1's `<available_skills>` block injects bundled-only skills. After this plan, the block has 7 entries (5 from Plan 2 + 2 from this plan).
- ✓ `skill_manage(action='create'|'install'|'patch'|'edit')` — write actions used by workflow bodies and create reflex.
- ✓ Tier-distinction sentence — referenced in Plan 1 §High-Level Design but not yet landed in `memory.md` (Plan 1 deferred to Plan 3 to consolidate with protocol framing).

### Why protocol lives in `06_skill_protocol.md`

`04_tool_protocol.md` is correctly scoped to tool-use protocol — preambles, parallel calls, error recovery. Skills are a behavioral domain (when to engage with a dynamic catalog), parallel to `03_reasoning.md` (how to reason) and `05_workflow.md` (how to execute). The convention is one numbered file per behavioral domain; skill protocol is its own domain.

### Why workflow bodies live alongside the protocol

`skill-creator.md` and `skill-installer.md` are bundled workflow bodies — when invoked (`/skill-creator` or `/skill-installer`), their bodies become the next-turn prompt. They concretize the protocol's create / install reflexes by *walking the model through* the workflow rather than just stating the rule. The protocol file says **when** to invoke; the workflow bodies say **how** to execute.

## Problem & Outcome

**Problem.** Co-cli ships the skill tools (Plan 1) and the authoring contract (Plan 2) but has no prompt-side discipline to drive use and evolution. Without the five reflexes, the model:
- May never call `skill_search` (no discovery trigger).
- May treat a loaded skill body as reference text instead of procedure (no use rule).
- Will skip patching outdated skills (no drift rule).
- Will never promote a successful pattern to a skill (no creation rule).
- Will never offer the user a save (no offer-to-save rule).

The skill *tools* are inert without the *discipline*. This plan ships the discipline.

**Outcome.**

1. **`co_cli/context/rules/06_skill_protocol.md`** — full five-rule scaffolding, opening with the tier-distinction sentence:
   > *Skills sit at a different operational tier than memory. Memory holds facts you recall to inform reasoning during a task; skills hold procedures that define how to structure the task itself.*
2. **Bundled `skill-creator.md`** — workflow body for the create reflex. Walks the model through deciding whether a procedure is skill-worthy, drafting the body to §6, and invoking `skill_manage(action='create')`.
3. **Bundled `skill-installer.md`** — workflow body for the install reflex. Walks the model through validating a URL or path, security-scanning, and invoking `skill_manage(action='install')`.
4. **Tier-distinction language landed in `memory.md` §1** (a sentence Plan 1 sketched but deferred to consolidate with protocol framing).
5. **Plan 1's manifest auto-includes the two new workflow skills**, surfacing them in `<available_skills>`.
6. **Small/medium-model targeting**: pre-scan rule is conditional on "multi-step task," not blanket mandatory (sharper than hermes's pattern).

## Scope

### In scope

- `co_cli/context/rules/06_skill_protocol.md` (new) — five-rule scaffolding.
- `co_cli/skills/skill-creator.md` (new) — bundled workflow body, §6-compliant, lint-clean per Plan 2 §7.
- `co_cli/skills/skill-installer.md` (new) — bundled workflow body, §6-compliant, lint-clean per Plan 2 §7.
- `docs/specs/memory.md` §1 — add the tier-distinction sentence.
- `docs/specs/skill.md` — add a §8 "Protocol" section cross-linking `06_skill_protocol.md`.
- Behavioral tests:
  - `tests/test_flow_skill_protocol.py` — verifies `06_skill_protocol.md` is loaded into the rendered system prompt.
  - `tests/test_flow_skill_creator_dispatch.py` — verifies `/skill-creator` dispatches and produces a `DelegateToAgent` outcome.
  - `tests/test_flow_skill_installer_dispatch.py` — same for `/skill-installer`.

### Out of scope

- **Artifact maintenance / drift rule.** Skill-side patching is the focus; an analogous rule for artifacts (when to `artifact_manage(action='replace')` an outdated decision) is a separate follow-on.
- **Auto-creation of skills.** The protocol *suggests* and *offers* — actual creation is gated by `skill_manage(action='create')` and (for offer-to-save) user confirmation. No auto-promote.
- **Multi-language skill bodies.** English only, same as Plan 2.
- **Tracking skill usage / popularity.** Out of scope; no telemetry on which skills are loaded.
- **Modifying `04_tool_protocol.md`.** Plan 1 already removed the stale skills note; nothing new here.
- **Plan 4 migration importer.** Separate plan; `skill-installer.md` body covers single-skill install, not bulk migration.

## Behavioural Constraints

1. **Five distinct reflexes, separate sections.** Each reflex is its own `##` section in `06_skill_protocol.md` (Discovery, Use, Drift, Create, Offer-to-save).
2. **Pre-scan is conditional, not mandatory.** Hermes uses "before replying, you MUST scan." Co-cli uses "at the start of any multi-step task" — sharper trigger, less false-positive overhead for trivial turns.
3. **Use rule is explicit.** "A skill body loaded via `skill_view` is your procedure for the task, not reference material to summarize." Without this, the model may paraphrase a skill instead of executing it.
4. **Drift rule covers both `patch` and `edit`.** Patch for surgical fixes (single string), edit for structural overhauls.
5. **Create reflex has a quantitative trigger.** "After a coherent procedure of 3+ steps you'd repeat" (lower threshold than hermes's "5+ tool calls" because co-cli tasks tend smaller). Tuning is a deferred concern.
6. **Offer-to-save is interactive.** "Offer to the user; confirm before creating." Distinct from the model autonomously creating a skill mid-task.
7. **Tier-distinction sentence is canonical.** Same sentence appears in `06_skill_protocol.md` opening and in `memory.md` §1. Single source of truth; one-line change to both if it ever evolves.
8. **Workflow bodies are §6-compliant.** Both new bundled skills lint clean per Plan 2 §7. Same authoring discipline applies to bundled lifecycle skills.
9. **No mandatory load.** Hermes's *"you MUST load it"* is softened to *"err on the side of loading."* The latter respects the model's judgement when no skill clearly fits.

## High-Level Design

### `06_skill_protocol.md` content

```markdown
# Skill protocol

Skills sit at a different operational tier than memory. Memory holds
facts you recall to inform reasoning during a task; skills hold
procedures that define how to structure the task itself. Before any
multi-step task, check the skill surface first; recall facts within
whatever procedure applies.

The bundled skill manifest is visible above (in the <available_skills>
block). User-installed and dynamically-created skills are searchable
via `skill_search`.

## Discovery

At the start of any multi-step task, scan the bundled skill manifest.
If exactly one skill clearly applies, load it with `skill_view(name)`
and follow it.

If the manifest does not cover what you need, call
`skill_search(query)` with keywords from your goal. User-installed
and skills you created in this session live there. Err on the side of
searching — it is cheaper to look and find nothing than to skip a
relevant skill.

Skip discovery for trivial single-step replies (a direct answer, a
single tool call). Discovery overhead is wasted on tasks the manifest
doesn't apply to.

## Use

A skill body loaded via `skill_view` is your procedure for the task,
not reference material to summarize. Follow its steps. The skill
defines how the task is done here — its phases, rules, and terminal
invariants take precedence over your default approach.

If the skill calls for tools or commands you don't recognize, look them
up before executing. Don't substitute.

## Drift

If a skill you loaded has stale steps, wrong commands, missing
pitfalls, or no longer matches the codebase, fix it immediately. Call
`skill_manage(action='patch', name=<skill>, old_string=..., new_string=...)`
for surgical fixes, or `skill_manage(action='edit', name=<skill>,
content=...)` for structural overhauls. Don't wait to be asked.
Unmaintained skills become liabilities.

## Create

After completing a multi-step task (3+ coherent steps), consider
whether the procedure is reusable. If yes — same steps you'd run for
similar tasks — promote it to a skill with
`skill_manage(action='create', name=<task-type>, content=<§6-shaped
body>)`. Name by task type, not the specific instance. The bundled
`skill-creator` skill walks you through the authoring shape if you
need it.

Don't create for one-offs. The bar is "would I run this again for the
same kind of task."

## Offer-to-save

After difficult or iterative work where you executed a coherent
procedure, briefly offer the user a skill creation suggestion:
*"This looked like a reusable procedure. Want me to save it as a
/<task-type> skill?"* Skip for simple one-offs. Confirm with the user
before invoking `skill_manage(action='create')` on their behalf — the
create reflex above covers autonomous creation; this rule covers
collaborative creation.
```

Approx 50 lines. Same tone as existing `04_tool_protocol.md` and `05_workflow.md`.

### `skill-creator.md` workflow body (sketch)

```markdown
---
description: Walk through promoting a procedure into a bundled skill — frontmatter, §6 shape, and skill_manage(action='create').
argument-hint: <task-type-name>
---

# skill-creator

**Invocation:** `/skill-creator <task-type-name>`

Promote a procedure you just executed into a reusable skill. Output
lands in `~/.co-cli/skills/<name>.md`.

---

## Phase 1 — Decide

Ask: would I run this same procedure again for the same kind of task?
- Yes, same steps → continue.
- No, one-off → stop. Don't create.

## Phase 2 — Shape

Draft body per `skill.md` §6:
- Frontmatter with single-sentence description (≤1024 chars).
- H1 title matching the slash name.
- `**Invocation:**` line.
- One-paragraph summary.
- 2–5 `## Phase N — <name>` sections.
- Optional `## Rules` section.

## Phase 3 — Lint

Run the §7 checklist mentally: frontmatter present, description set,
phases formatted correctly, body ≤8000 chars. Or call `/skills lint`
post-create.

## Phase 4 — Write

Call `skill_manage(action='create', name=<task-type-name>, content=<body>)`.
On success, `refresh_skills` makes the skill available immediately.

## Rules

- Name by task type (e.g. `review`, not `review-pr-123`).
- One skill per file. Don't fold multiple procedures into one body.
- Lint clean is non-negotiable for bundled-style quality.
```

### `skill-installer.md` workflow body (sketch)

```markdown
---
description: Walk through installing a skill from a URL or local path with security validation.
argument-hint: <source-url-or-path>
---

# skill-installer

**Invocation:** `/skill-installer <source>`

Install a skill from a `.md` source — URL or local file. Output lands
in `~/.co-cli/skills/<filename>`.

---

## Phase 1 — Validate source

Check the source:
- URL → must be https; verify domain is trusted.
- Local path → must end in `.md`.
- Reject anything else.

## Phase 2 — Install

Call `skill_manage(action='install', source=<source>)`. The tool will:
1. Fetch content.
2. Run `scan_skill_content` (security gates).
3. Validate frontmatter (description required, ≤1024 chars).
4. Reject collisions with existing user skills.
5. Atomic write + reload.

## Phase 3 — Verify

After install, call `skill_view(name=<installed-name>)` and confirm
the body is what you expected. Run `/skills lint <name>` to check
authoring conformance.

## Rules

- Never `install` over an existing skill; use `edit` to update.
- Don't install from untrusted URLs without inspection.
- If security scan flags content, the file is auto-rolled back; do not
  retry the same source.
```

### Tier-distinction sentence in `memory.md` §1

Add as a paragraph immediately after the current "Memory is never injected wholesale..." line:

```markdown
Memory and skill surfaces sit at different operational tiers. Memory
holds facts you recall to inform reasoning during a task; skills hold
procedures that define how to structure the task itself. The skill
surface is documented in [skill.md](skill.md) and governed by
[06_skill_protocol.md](../../co_cli/context/rules/06_skill_protocol.md).
```

### `skill.md` §8 — Protocol cross-link

Append after current §7:

```markdown
## 8. Protocol

Prompt-side discipline governing when to engage with the skill surface
lives in [`co_cli/context/rules/06_skill_protocol.md`](../../co_cli/context/rules/06_skill_protocol.md).
Five reflexes: discovery, use, drift, create, offer-to-save. The
protocol file is loaded into the system prompt at agent construction.
```

## Tasks

### ✓ DONE — TASK-1 — Draft `06_skill_protocol.md` (moved to Plan 2.5)

> **Moved to Plan 2.5** (`2026-05-12-150000-plan2.5-skill-prompt-discipline.md`).
> Shipped alongside docstring enrichment so the protocol rule and per-tool
> behavioral guidance land as a coherent LLM instruction layer.

Files:
- `co_cli/context/rules/06_skill_protocol.md` (new).

Acceptance:
- Five sections: `## Discovery`, `## Use`, `## Drift`, `## Create`, `## Offer-to-save`.
- Opens with the tier-distinction sentence.
- ≤80 lines total.
- Discovery rule is conditional (on multi-step task), not blanket mandatory.
- Use rule mentions "procedure, not reference material."
- Drift rule mentions both `patch` and `edit`.
- Create rule has a quantitative trigger (3+ coherent steps).
- Offer-to-save mentions user confirmation.

### ✓ DONE — TASK-2 — Author `skill-creator.md` bundled workflow body

Files:
- `co_cli/skills/skill-creator.md` (new).

Acceptance:
- §6-compliant (passes Plan 2's `_lint.py`).
- ≤4000 chars body.
- Walks through Phase 1 (Decide), Phase 2 (Shape per §6), Phase 3 (Lint), Phase 4 (Write via `skill_manage(action='create')`).
- Description ≤200 chars for clean manifest display.

### ✓ DONE — TASK-3 — Author `skill-installer.md` bundled workflow body

Files:
- `co_cli/skills/skill-installer.md` (new).

Acceptance:
- §6-compliant.
- ≤4000 chars body.
- Walks through Phase 1 (Validate), Phase 2 (Install via `skill_manage(action='install')`), Phase 3 (Verify).
- Description ≤200 chars.

### ✓ DONE — TASK-4 — Tier-distinction in `memory.md` §1

Files:
- `docs/specs/memory.md` (insert paragraph in §1).

Acceptance:
- Tier-distinction sentence added immediately after the existing "Memory is never injected wholesale..." paragraph.
- Cross-links `skill.md` and `06_skill_protocol.md`.
- No other §1 content changes.

### ✓ DONE — TASK-5 — `skill.md` §8 protocol cross-link

Files:
- `docs/specs/skill.md` (append §8).

Acceptance:
- §8 cross-links to `06_skill_protocol.md`.
- Lists the five reflexes.
- ≤15 lines.

### ✓ DONE — TASK-6 — Behavioral tests

Files:
- `tests/test_flow_skill_protocol.py` (new).
- `tests/test_flow_skill_creator_dispatch.py` (new).
- `tests/test_flow_skill_installer_dispatch.py` (new).
- `tests/test_flow_skill_bundled_library.py` (extend — Plan 2's bundled-library test now covers 7 bundled skills).

Test surface (protocol rendering):

| # | Assertion |
|---|---|
| 1 | `06_skill_protocol.md` content appears in the rendered system prompt. |
| 2 | All five `##` reflex section headers present in rendered prompt. |
| 3 | Tier-distinction sentence appears once (in protocol rule, optionally once more in memory.md §1 if rendered together). |
| 4 | `<available_skills>` manifest (from Plan 1) includes `skill-creator` and `skill-installer`. |

Test surface (workflow bodies):

| # | Assertion |
|---|---|
| 1 | `/skill-creator review` dispatches → `DelegateToAgent(delegated_input=<body>, skill_name='skill-creator')`. |
| 2 | `/skill-installer https://example.com/x.md` dispatches → `DelegateToAgent(...)`. |
| 3 | Both bundled bodies lint clean per Plan 2 `_lint.py`. |
| 4 | Both load successfully at bootstrap (no `_check_requires` failures). |

### ✓ DONE — TASK-7 — Cross-plan integration check

Files: none (verification step).

Acceptance:
- `scripts/quality-gate.sh full` clean.
- `/skills list` shows 7 bundled skills (doctor + 4 from Plan 2 + 2 from this plan).
- `/skills lint --all` exits 0.
- Rendered system prompt (manual inspection via `co_cli/context/` test harness) contains all five reflex sections from `06_skill_protocol.md`.
- `<available_skills>` manifest has 7 entries.
- `skill_search('create')` returns `skill-creator` (regression guard).

## Testing

### Test files

- `tests/test_flow_skill_protocol.py` (new)
- `tests/test_flow_skill_creator_dispatch.py` (new)
- `tests/test_flow_skill_installer_dispatch.py` (new)
- `tests/test_flow_skill_bundled_library.py` (extend from Plan 2)

### Test pattern

Real prompt-assembly path. Construct a `CoDeps` with bundled skills loaded; render the system prompt; assert the protocol rule's text appears and the manifest contains the expected entries. No mocks.

Dispatch tests use the real `dispatch(raw_input, ctx)` function and inspect the returned `DelegateToAgent` outcome.

### Lint / quality gate

- `scripts/quality-gate.sh lint` after each task.
- `scripts/quality-gate.sh full` before considering ready to ship.
- `/skills lint --all` exits 0 — additional ship gate.

## Open Questions

1. **Q:** Should the Create reflex trigger at "3+ coherent steps" or higher (e.g. 5+ like hermes)?
   **Tentative answer:** 3+ for v1. Co-cli tasks tend smaller and more focused than hermes's typical use; lower threshold encourages skill formation. Bump to 5+ if the bundled library bloats with low-value creations.

2. **Q:** Should the Discovery rule mention `skill_search` first or `<available_skills>` manifest first?
   **Tentative answer:** Manifest first. Cheaper (no tool call), more reliable (always in prompt), covers the high-leverage bundled procedures. `skill_search` is the fallback for user-installed long-tail.

3. **Q:** Should `06_skill_protocol.md` use hermes's mandatory "MUST" language or softer "should"?
   **Tentative answer:** Softer. "MUST" risks model rigidity (always loading even when no skill applies); "err on the side of loading" preserves judgement while leaning toward discovery.

4. **Q:** Should the Offer-to-save rule fire on every turn or only after complex tasks?
   **Tentative answer:** Only after complex tasks (matches Create reflex's trigger). Offering on every turn is noise.

5. **Q:** Should the workflow bodies use `$1` argument substitution or take args via the body?
   **Tentative answer:** `$1` for the primary arg (task-type-name or source-url). Reuses existing dispatch substitution; no new mechanism.

## Deferred items

- **Artifact-side drift rule.** When to `artifact_manage(action='replace')` an outdated decision. Same shape as skill drift but separate scope.
- **Skill usage telemetry.** Tracking which skills get loaded and how often, to inform pruning.
- **Auto-tuning of Create reflex threshold.** Currently 3+ steps; future plan could adapt based on bundled library size.
- **Per-skill protocol overrides.** A skill could declare in its frontmatter whether discovery should be aggressive ("always load if remotely relevant") or conservative. Out of scope.
- **Mandatory pre-scan flag.** For sub-7B targeting, a config flag could promote Discovery from conditional to mandatory. Defer until we actually target that tier.

## Shipping order

Single commit — all seven TASKs. Protocol rule + workflow bodies + spec updates + tests ship together. Partial ship leaves the protocol orphaned (rule with no example skills) or the workflow skills un-supported by the rule.

**Hard dependencies:**
- Plan 1.5 (surface tool naming convergence, shipped) — converged surface; provides `skill_search`, manifest injection, `skill.md` §1–§5.
- Plan 2 (authoring contract + lint) — provides §6, §7, `_lint.py`. Workflow bodies must pass lint.

**Soft dependencies:** none. Plan 4 (migration importer) is independent.

**Initial-state caveat:** Plan 1's manifest auto-fills with the two new workflow skills as soon as TASK-2 and TASK-3 commit. No manifest-code changes needed.

## Post-ship — research-doc resync

After this plan ships, update `docs/reference/RESEARCH-skills-peers-tiers.md`:

- Step 2 (Drift discipline) → **shipped** (drift reflex in `06_skill_protocol.md`).
- Step 4 (Awareness layer) → **shipped** (manifest from Plan 1 + Discovery reflex from this plan).
- Architecture comparison: co-cli's protocol shape (conditional pre-scan, both `<available_skills>` manifest + `skill_search` tool) is a hybrid of hermes (mandatory pre-scan, prompt-only manifest) and openclaw (search-only). Trade-off documented.

## Delivery Summary — 2026-05-12

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | (moved to Plan 2.5) | ✓ pass (pre-shipped) |
| TASK-2 | `skill-creator.md` lints clean, 4 phases, description ≤200 chars, body ≤4000 chars | ✓ pass |
| TASK-3 | `skill-installer.md` lints clean, 3 phases, description ≤200 chars, body ≤4000 chars | ✓ pass |
| TASK-4 | `grep "Memory and skill surfaces sit at different operational tiers" docs/specs/memory.md` returns output | ✓ pass |
| TASK-5 | `grep "## 8. Protocol" docs/specs/skill.md` returns output | ✓ pass |
| TASK-6 | `uv run pytest tests/test_flow_skill_bundled_library.py tests/test_flow_skill_protocol.py tests/test_flow_skill_creator_dispatch.py tests/test_flow_skill_installer_dispatch.py` — 37 passed | ✓ pass |
| TASK-7 | lint clean, 7 bundled skills load, all tests pass | ✓ pass |

**Tests:** scoped (touched test files) — 37 passed, 0 failed
**Doc Sync:** clean — memory.md and skill.md accurate; system.md index check clean

**Overall: DELIVERED**
All six pending tasks complete. Bundled library grows from 5 to 7 skills (`skill-creator` + `skill-installer`). Hermes parity checked: co-cli uses conditional discovery trigger (vs hermes's mandatory "MUST scan") and softer "err on the side of loading" language — deliberate design choices preserved.
