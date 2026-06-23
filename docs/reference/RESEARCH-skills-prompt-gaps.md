# RESEARCH: Skill System — co-cli vs hermes + opencode

How `co-cli` authors, discovers, surfaces, and maintains skills, compared against the two primary peers with a comparable first-class skill system: `hermes-agent` and `opencode`. Scope is `co-cli`'s own implementation (bundled `co_cli/skills/*/SKILL.md`, user-global `~/.co-cli/skills/*/SKILL.md`), not the Claude Code harness skill files. `openclaw` (`skills/`) and `codex` (`.codex/skills/`) also ship skill systems; they are consulted only as fallback where neither primary peer has a parity analog for a given co skill (see `doctor` in the per-skill review below).

**Authoritative spec:** `docs/specs/skills.md`. **Injected discipline:** `co_cli/context/rules/06_skill_protocol.md`.

## Scope

`co-cli` files:

- `docs/specs/skills.md` — skill system spec
- `co_cli/skills/` — 5 bundled skills: `doctor`, `documents`, `office`, `plan`, `skill-creator` (each a `<name>/SKILL.md` folder). `refactor`, `review`, and `triage` were removed — coding-agent skills that don't fit co's knowledge-work positioning (`refactor` additionally hardcoded co's own repo tooling; `review` hardcoded co's house rules). `review`/`triage` are irreducibly software-engineering (changed-code review; bug triage) and don't generalize; `plan` was kept and regeneralized because its subject (request → scoped plan) is domain-agnostic and its scoping-only discipline is a WEAK_LOCAL-specific guard. Refactoring/reviewing/triaging co-cli itself is owned by the `/orchestrate-*` dev workflow.
- `co_cli/skills/skill_types.py` — `SkillInfo` dataclass
- `co_cli/skills/manifest.py` — `render_skill_manifest()` → `<available_skills>` XML
- `co_cli/skills/loader.py`, `lifecycle.py`, `index.py`, `lint.py`, `usage.py` — load, hot-reload, model-facing subset, R1–R4 lint, usage sidecar
- `co_cli/tools/system/skills.py` — `skill_view` + `skill_create`/`skill_edit`/`skill_patch`/`skill_delete`
- `co_cli/agent/_instructions.py:75-85` — `skill_manifest_prompt` per-turn instruction callback
- `co_cli/context/rules/06_skill_protocol.md` — discovery/use/drift/create reflexes
- `co_cli/commands/core.py:110-156` (`dispatch`) + `co_cli/commands/skills.py` (`/skills` family)
- `co_cli/bootstrap/core.py` — `create_deps()` skill loading
- `co_cli/daemons/dream/_reviewer.py` + `_housekeeping.py` — background skill reviewer, merge/decay

Peer files (HEAD):

- `hermes-agent` (`bb7ff7dc3`): `skills/` (18 categories, 71 skills); `agent/prompt_builder.py:1334-1600` (`build_skills_system_prompt`, mandatory-scan at 1564-1589) and `:173-180` (`SKILLS_GUIDANCE`); `agent/system_prompt.py:113/470` (three-tier assembly); `agent/skill_utils.py` (frontmatter, `skill_matches_platform`); `tools/skills_tool.py:1577-1638` (`skill_view`); `tools/skill_manager_tool.py:1099-1233` (`skill_manage`, 6 actions); `tools/env_passthrough.py` (session-scoped env allowlist)
- `opencode` (`fbf889db8`): `packages/core/src/skill.ts` (`SkillV2` + frontmatter), `skill/discovery.ts` (local + remote-URL catalogs), `skill/guidance.ts` (`<available_skills>` system context), `tool/skill.ts` (model-callable `skill` loader); `system-context/registry.ts` (lexicographic block ordering); `config/command.ts` (separate command construct with `$ARGUMENTS`)

## Architectural positions

All three expose skills to the model through an index plus a model-callable load tool. They diverge on three axes: **how discovery is framed**, **whether the model can author skills**, and **how skill bodies reach the turn**.

### `co-cli` — hybrid: overlay + model-loadable reference + model-writable

Three invocation paths (`docs/specs/skills.md:41-54`):

1. **User slash-command** — `/doctor` → `dispatch()` (`co_cli/commands/core.py:134-151`) expands the body (with `$ARGUMENTS`/`$N` substitution) and returns `DelegateToAgent`; the REPL runs a fresh `run_turn()` with the body as input. The skill body *is* the prompt for that turn.
2. **Model inline use** — the model reads the per-turn `<available_skills>` manifest, calls `skill_view(name)`, and the body returns as a tool result inside the current turn. Primary agent-initiated path.
3. **Model write** — `skill_create` / `skill_edit` / `skill_patch` / `skill_delete` (all DEFERRED, approval-gated) write a user skill; `refresh_skills(deps)` makes it live immediately.

The manifest is rendered **per-turn** via `skill_manifest_prompt` (`co_cli/agent/_instructions.py:75-85`), post-static, so newly-created skills appear on the next turn and skill mutations never churn the static prefix. Discovery framing is deliberate and light: "If exactly one skill clearly applies, load it… Skip discovery for trivial single-step replies" (`06_skill_protocol.md:10-20`).

### Hermes — model-loadable reference, mandatory-scan

The skills index is baked into the system prompt with a hard instruction (`agent/prompt_builder.py:1565-1567`):

> Before replying, scan the skills below. If a skill matches or is even partially relevant to your task, you MUST load it with skill_view(name) and follow its instructions.

The model loads via `skill_view(name)` (optionally a linked `file_path` for multi-file skills) and may revisit across turns. Authoring is a single mode-dispatched tool `skill_manage` with six actions — `create`, `patch`, `edit`, `delete`, `write_file`, `remove_file` (`tools/skill_manager_tool.py:1136`) — paired with a prompt nudge to save/patch proactively (`SKILLS_GUIDANCE`, `prompt_builder.py:173-180`). Creation is gated only by that prompt norm ("confirm with user"), not a code-level approval interrupt.

### Opencode — read-only knowledge reference, model-loadable, externally authored

`SkillV2` (`packages/core/src/skill.ts`) discovers `*.md`/`**/SKILL.md` from local dirs, embedded definitions, **or remote URL catalogs** (`skill/discovery.ts` — fetches `index.json`, path-traversal-guarded). The model sees them through `skill/guidance.ts`, which renders the same `<available_skills>` XML shape co uses, permission-filtered per agent. The model loads a skill via the built-in `skill` tool (`tool/skill.ts`), which returns the body plus a sampled inventory of sibling file *paths* (up to 10) — not their contents. Frontmatter is minimal — `name`, `description`, `slash` (`skill.ts:59-63`). There is **no model authoring path**: skills are discovered and applied, never created or patched by the model. Templated/argument-bearing prompts live in a *separate* `command` construct (`config/command.ts`).

### Convergence and divergence

- **Convergence:** co and opencode independently use the same `<available_skills>` index and both reject hermes's "MUST load if even partially relevant" framing for a lighter touch. All three give the model a load tool.
- **Authoring:** co and hermes are *model-writable* (co via four monomorphic tools, hermes via one dispatched tool); opencode is *read-only* to the model.
- **Body delivery:** co uniquely keeps the slash-command overlay path (body drives a whole turn) alongside the inline-reference path; hermes and opencode are reference-only.
- **Create gating:** co enforces a hard approval interrupt on `skill_create` (`tools/system/skills.py:305-309`); hermes relies on a prompt-level "confirm with user" norm; opencode has no create path.

## Prompt architecture: the skill-protocol rule vs the tool surface

co surfaces skill *behavior* through a dedicated always-on rule (`co_cli/context/rules/06_skill_protocol.md`) separate from the skill *tool surface* (the docstrings on `skill_view`/`skill_create`/`skill_edit`/`skill_patch`/`skill_delete`).

### Why the protocol is separated from the tool docstrings

Three reasons, the first decisive:

1. **DEFERRED write tools force an always-on reflex home.** co's skill-write tools are `VisibilityPolicyEnum.DEFERRED` (`tools/system/skills.py:305-309` …), so their docstrings are absent from the prompt until loaded via `tool_view`. If the "when to create a skill" trigger lived only in the `skill_create` docstring, the model would never see it until it had already decided to create — circular. The trigger must precede the tool reach, mandating an always-injected surface. (`skill_view` is `ALWAYS`, but the create/patch reflex still must precede any tool reach.)
2. **The protocol is cross-tool.** Discover → `skill_view` → use → drift-fix (`skill_patch`/`skill_edit`) → create → delete, plus the non-tool slash-dispatch path. A workflow spanning five tools has no single-docstring home.
3. **Concern separation.** The rule is *policy* (when/whether/dedup/naming); the docstring is *mechanics* (args, §6 shape, approval). The rule sits in the cached static prefix; deferred docstrings load per-call.

### Peer backing: separation is best practice

Both peers keep skill guidance as a prompt-level block separate from the skill tool's schema:

- **hermes** — `SKILLS_GUIDANCE` (`agent/prompt_builder.py:173-180`) and the skills index `build_skills_system_prompt()` (`:1334-1600`) live in prompt assembly, distinct from the `skill_manage` schema (`tools/skill_manager_tool.py:1099-1211`). Memory follows the same pattern: `MEMORY_GUIDANCE` (`prompt_builder.py:144`) is a separate constant.
- **opencode** — `skill/guidance.ts:56-67` renders `<available_skills>` as a `SystemContext` source, decoupled from the `skill` tool (`tool/skill.ts`).

co's separation of `06_skill_protocol.md` from the tool docstrings matches both peers — the choice is peer-validated.

**Duplication caveat.** The *dual-home* pattern (criteria stated in both a prompt-level block and the tool's own description) is hermes-parity, not a co-unique flaw — hermes embeds when-to-create policy in both `SKILLS_GUIDANCE` and the `skill_manage` schema description (`skill_manager_tool.py:1101-1129`). co's only excess is a *third* copy in the `skill-creator` skill, with the "3+ coherent steps" threshold stated verbatim in three places (rule `:49`, `skill_create` docstring `:317`, skill-creator Phase 1). The rule should be the single source for policy; the docstring should carry only a brief last-chance restatement. The current spread is drift-prone — tune the threshold once, three edits.

### Rule set and ordering

co's rule set is `01_interaction · 02_safety · 03_reasoning · 04_tool_protocol (generic) · 05_workflow (placeholder) · 06_skill_protocol · 07_memory_protocol`. Skills and memory are singled out as dedicated rules because they are the two operational tiers that are **proactively engaged** (recall and skill-discovery must fire on conversational cues, not on explicit request), **agent-curated** (read + write + maintain a persistent store), and **mutually bounded** (facts vs procedures — `07_memory_protocol.md:99-100` adjudicates the boundary in prose). The read-only `session` tier folds into `07`'s recall cascade rather than getting its own rule; doctrine is intrinsic and queryless; the episodic tools (google/tasks/vision/session-view) are reactive and need only their docstring plus the computed deferred-tool stub.

Every concern co encodes has a peer analog:

| Concern | hermes | opencode | Verdict |
|---|---|---|---|
| interaction / persona / style | `DEFAULT_AGENT_IDENTITY`, help guidance | `anthropic.txt` Tone & Style | aligned |
| safety / credentials / approval | scattered (env hints, profile, enforcement) | thin (file-creation, code-ref discipline) | co more explicit |
| reasoning / verification | `TASK_COMPLETION_GUIDANCE` | "Doing tasks" | aligned (co more developed) |
| generic tool-use discipline | `PARALLEL_TOOL_CALL_GUIDANCE`, `TOOL_USE_ENFORCEMENT_GUIDANCE` | Tool-usage policy | aligned |
| workflow / todo | kanban / task-completion | TodoWrite section | aligned |
| skill protocol | `SKILLS_GUIDANCE` + skills index (separated) | `skill/guidance.ts` (separated) | aligned — both separate it |
| memory protocol | `MEMORY_GUIDANCE` + volatile snapshots | none (no memory tier) | aligned w/ hermes; opencode N/A |

The **set** is peer-backed. The **ordering** is not: co's `01→07` is a deliberate semantic sequence (general conduct → acting → managed knowledge tiers), but neither peer orders this way. hermes orders by cache tier — stable → context → volatile (`agent/system_prompt.py:113/470`) — with tool-conditional injection, even splitting memory across an early behavioral block and a late volatile-snapshot block. opencode orders by lexicographic registry key (`system-context/registry.ts:39`) or base-prompt-then-appended, with skill guidance landing last. The only shared gradient is "identity/conduct early, capability guidance later." co's strict numbered hierarchy optimizes human legibility and attention-priming (safety first), not prompt-cache or any measured behavioral effect — a defensible co-specific choice, not an industry practice.

## Where co is distinctive

### 1. Turn-scoped skill-env injection with rollback (unique)

`skill-env` frontmatter injects env vars only for the dispatched turn, restored in a `finally` block (`docs/specs/skills.md:145-156`), filtered through `_SKILL_ENV_BLOCKED` (blocks `PATH`/`PYTHONPATH`/`HOME`/shell-loaders). hermes's `env_passthrough.py` is an allowlist scoped to the **session** (ContextVar-backed) that gates which host vars reach sandboxes — it does not inject. opencode has no skill env mechanism.

### 2. Self-improvement is a full loop, not just a prompt nudge

co pairs in-session reflexes (`06_skill_protocol.md`) and model-write tools with a **background** layer no peer has: the dream daemon's `skill_reviewer` scans transcripts for drift/new procedures and patches/creates skills autonomously (`requires_approval=False`), and scheduled `merge_skills`/`decay_skills` consolidate similar user skills and archive stale ones (recall-protected, pin-exempt). Usage sidecars (`skills/usage.py`) track recall days to drive decay protection. hermes has the prompt nudge + `skill_manage` but no background reviewer or decay; opencode has neither half.

### 3. Authoring contract codified and enforced

A documented shape (`skills.md:172-238`), R1–R4 advisory lint attached to every `skill_create`/`skill_edit`/`skill_patch` result as `lint_warnings`, integrity checks (`description` ≤1024, total ≤50k) hard-blocking the write, a B1 no-marker gate for the bundled library in CI, and a `skill-creator` skill that turns the contract into a guided workflow. hermes has a strong de-facto structure but no lint/gate; opencode's frontmatter is minimal with no contract.

### 4. Monomorphic write tools (deliberate small-model trade)

co exposes four single-purpose tools (`skill_create`/`skill_edit`/`skill_patch`/`skill_delete`) where hermes uses one action-dispatched `skill_manage`. This is the same monomorphic-vs-dispatched trade co makes elsewhere — easier for a small model to call correctly. Not a consolidation candidate.

## Per-skill prompt-design: borrowable devices

co ships 5 bundled skills in a consistent house style: `description` + `**Invocation:**` + phase/step structure + fenced output-contract templates + a terminal `## Rules` block. They are well-scoped and template-driven. Compared skill-by-skill against the closest peer analog, the gaps are not in *structure* but in two missing **device classes** — anti-rationalization framing and hard iteration caps.

| co skill | closest peer analog | borrowable device co lacks |
|---|---|---|
| `plan` | hermes `plan` (338 ln) | **ADOPTED** — a stated core principle up front (`plan/SKILL.md:72`) and a Common Mistakes block with bad→good contrasts (`:292-313`), both re-authored for co's scoping style (see note below). Full adoption of hermes's complete-code-at-plan-time style was **rejected** (model-capability — see §full-adoption below). |
| `doctor` | none in hermes/opencode → codex/openclaw `doctor` *command* | structural only — no peer skill-prompt analog (see below) |
| `skill-creator` | hermes `hermes-agent-skill-authoring` (196) | named failure-mode vocabulary (Premature Completion / Duplication / Sediment / Sprawl / No-op Prose, `:79-85`); "if a line doesn't change behavior, cut it" (`:70-77`) |
| `documents` / `office` | hermes `ocr-and-documents` (172) | at/above parity — co adds the scanned→vision tier, error-line tables, and reciprocal cross-skill handoff hermes lacks; borrow only if multiple extractors are added: a feature-comparison matrix (`:35-50`) |

opencode contributes one device, from its only real (domain-knowledge) skill `effect`: a **source-of-truth hierarchy** ("verify against the repo-local reference; do not answer from memory", `SKILL.md:8-16,29-30`) — forward-looking for any co domain-knowledge skill, none of which exist today.

### The two device classes (superseded by the coding-skill removal)

> **Superseded.** Both device classes below targeted the coding skills `triage`/`review`/`refactor` — all since **removed** from the bundle (off-mission for a knowledge-work agent). They are no longer actionable for the current bundle and are retained only as a record of the peer comparison. You don't upgrade a skill you've cut.

1. **Anti-rationalization layer.** Hermes's highest-discipline skills (`systematic-debugging`, `test-driven-development`) pair a **Red Flags → STOP** list with a **rationalization table** (excuse → reality) that counters the model's tendency to skip a gate under pressure. It was the "near-unconditional reflex on an observable cue" co's instruction-design doctrine calls for, and `triage`/`review` would have been the homes — both now removed.
2. **Hard iteration caps.** The removed `review` fix loop was uncapped; hermes caps fix attempts ("max 2 reverify cycles"; "Rule of Three → stop and question the architecture"). Relevant only to the cut coding skills.

Lower-value, optional and still applicable: an explicit one-line **core principle** at the top of each surviving skill — hermes opens every skill this way; co opens with a descriptive summary (`plan` already adopted this).

### `plan` adoption (shipped) and why full adoption was rejected

The two `plan` devices above were **adopted** into `co_cli/skills/plan/SKILL.md` (+11 lines): a one-line core principle after the intro, and a Common Mistakes block before `## Rules`. The block's *content* was re-authored for co's scoping philosophy (vague `Done when`, scope leak, oversized task, skipped open-questions) — hermes's own mistakes (Incomplete Code, Missing File Paths) were **not** copied: one directly contradicts co's "no implementation code in plans" rule (`plan/SKILL.md:71`), the others are already structurally enforced.

**Full adoption of hermes's `plan` style was rejected.** hermes's plan is an *executable spec* — bite-sized TDD steps with complete copy-pasteable code at plan time (`plan/SKILL.md:88, :139-180, :241`). co's default backend `qwen3.6:35b-a3b-agentic` is `WEAK_LOCAL` — "the local MoE the behavioral rules are calibrated to counter" (`co_cli/config/llm.py:44`). Demanding correct, complete code at plan time is exactly that model's weakest point, and freezing it into a trusted plan artifact removes the test/lint feedback loop that normally catches its coding errors. co's scoping-only plan (file names + verifiable `Done when`, no code) is a deliberate WEAK_LOCAL adaptation, not a gap; hermes's full style pays off only for a frontier planner.

**A/B regression check (no regression).** Baseline (pre-edit body) vs treatment (edited body), 3 planning tasks × 2 trials, driven through real `run_turn` on the configured `qwen3.6:35b-a3b-agentic`, judged by `gemini-3.5-flash`: mean judge 9.67→9.67, structural conformance 5.0→5.0, zero code dumps in either arm. Caveat: ceiling effect — clean tasks let both arms saturate the rubric, so the check proves *no harm*, not measurable upside.

### doctor: no peer skill analog

`doctor` is co-specific — a skill-prompt that wraps `capabilities_check` (Probe → Diagnose → Report). Neither hermes nor opencode has an equivalent. Both **codex** and **openclaw** have a `doctor`, but as a **compiled CLI command**, not a skill prompt — so there is no prompt-design device to copy. They offer only a structural model: codex's doctor is categorized, deliberately-passive read-only checks (`codex-rs/cli/src/doctor/{system,git,runtime,updates,background}.rs`); openclaw's emits **structured findings** (`checkId`/`severity`/`message`/`path`/`fixHint`) across three postures — Inspect (`openclaw doctor`) / Lint (`--lint`, read-only structured findings) / Repair (`--fix`) (`docs/cli/doctor.md:29-31` posture table, `:113-117` finding shape). co's doctor Report (free-form: Likely issue / What works / Active fallback / Next step) could optionally adopt a per-finding `severity` + `fixHint` shape. The `--fix` repair posture is **rejected by design** — co's doctor is deliberately recommend-only ("Doctor recommends — does not repair") — a divergence, not a gap.

## Remaining deltas (peer features co lacks)

Small and optional — co is at or beyond parity overall.

1. **Remote skill catalogs.** opencode discovers skills from a remote URL (`index.json` fetch, path-traversal-guarded — `skill/discovery.ts`). co loads only bundled + local user-global dirs; the `/skills` family is `list/check/lint/reload/usage/pin/unpin` — there is no `install`/`install <url>` path. If skill sharing becomes a goal, a vetted-source install path is the missing piece; gate it on the existing `scan_skill_content` + integrity validation.

2. **Multi-file skills (text only).** hermes `skill_view(name, file_path)` reads a specific linked file, and `skill_manage` has `write_file`/`remove_file` (allowlisted to `references/`/`templates/`/`scripts/`/`assets/`); opencode's `skill` tool surfaces sibling file paths. co's loader globs only `*/SKILL.md` and ignores sibling files for discovery (a `scripts/` asset is driven via `shell_exec`, not loaded into context). Note the editing surface is **text-only in every system**: hermes `write_file` takes a `str` written in text mode, and `skill_view` returns a size stub for binaries (`tools/skills_tool.py:1267-1280`) — binary assets are human/build-time authored everywhere. For co, matching hermes means adding a `file_path` axis to `skill_patch` plus `skill_write_file`/`skill_delete_file` for user-skill text files; the cheaper middle ground is surfacing a sibling-file inventory in `skill_view` (opencode's read-only model). Worth it only if a user skill needs the model to maintain multiple files — most procedural skills are single-body.

3. **Per-skill read gating.** opencode gates each skill load by agent permission (`tool/skill.ts` `permission.assert`). co gates skill *writes* (approval) but `skill_view` reads are ungated. Low priority — reads are non-mutating.

## Peer weaknesses

1. **hermes mandatory-scan is over-aggressive.** "You MUST load any even partially relevant skill" (`prompt_builder.py:1565-1567`) drives tool churn and devotes prompt mass to an index many tasks don't need. co's "load exactly one clearly-applicable skill, skip for trivial replies" is better-calibrated.

2. **hermes skill bodies are heavy.** The `plan` SKILL.md is 338 lines; loading it via `skill_view` consumes real context for a short task. co's R4 lint (body ≤8000 chars, warn) and umbrella-merge curation push the other way.

3. **opencode has no self-improvement.** Skills are static read-only knowledge with minimal frontmatter; the model cannot fix a stale skill or promote a discovered procedure — that work is fully out-of-band. The axis where co is furthest ahead.

## Bottom line

co's skill system is at or beyond parity with both peers. It is the only one with turn-scoped env injection + rollback and autonomous background skill curation, and it avoids both hermes's mandatory-scan churn and opencode's read-only static skills. Its prompt architecture — a dedicated skill-protocol rule separate from the tool surface — matches peer best practice; the dual-home guidance duplication is hermes-parity (co's only excess is the skill-creator third copy); and the deliberate semantic rule ordering is a co-specific legibility choice, not a peer-validated one.

The remaining deltas are optional and small: a vetted remote-install path (opencode has one), multi-file text-skill editing (hermes/opencode have read access, hermes has write), and per-skill read gating (opencode). None is urgent; the first is the only one worth a plan if skill sharing becomes a product goal.
