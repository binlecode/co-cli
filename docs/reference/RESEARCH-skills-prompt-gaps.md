# RESEARCH: Prompt Gaps — Skill Prompts

_Original: 2026-04-21 · Refreshed: 2026-06-22 (peers + co source re-verified at HEAD)_

This doc covers `co-cli`'s **skill system** — how skill bodies are authored, discovered, surfaced to the agent, and maintained. Scope is `co-cli`'s own skill implementation (bundled `co_cli/skills/*/SKILL.md`, user-global `~/.co-cli/skills/*/SKILL.md`), not the Claude Code harness skill files.

**Peers (exactly two):** `hermes-agent` and `opencode`. Both ship a first-class skill system with model-side discovery, so both are real comparators on this surface. fork-cc and codex have no comparable skill abstraction and are out of scope.

**Refresh note:** The original (2026-04-21) edition was a forward-looking gap analysis written when `co-cli` shipped exactly one bundled skill (`doctor`) and had no model-side skill channel. **Almost all of those gaps have since shipped.** This refresh re-verifies every claim against current source and re-frames the doc as a three-way comparison of three mature skill systems, with the live deltas that remain. The original gap numbering is preserved in §4 with current status so the history is legible.

**Related work:**
- `docs/specs/skills.md` — current skill-system spec (authoritative)
- `co_cli/context/rules/06_skill_protocol.md` — injected discovery/drift/create discipline

## Scope

Reviewed `co-cli` files (re-verified at HEAD):

- `docs/specs/skills.md` — skill system spec
- `co_cli/skills/` — 8 bundled skills: `doctor`, `documents`, `office`, `plan`, `refactor`, `review`, `skill-creator`, `triage` (each a `<name>/SKILL.md` folder)
- `co_cli/skills/skill_types.py` — `SkillInfo` dataclass (was `_skill_types.py`/`SkillConfig` in the original; moved + renamed)
- `co_cli/skills/manifest.py` — `render_skill_manifest()` → `<available_skills>` XML
- `co_cli/skills/loader.py`, `lifecycle.py`, `index.py`, `lint.py`, `usage.py` — load, hot-reload, model-facing subset, R1–R4 lint, usage sidecar
- `co_cli/tools/system/skills.py` — `skill_view` + `skill_create`/`skill_edit`/`skill_patch`/`skill_delete`
- `co_cli/agent/_instructions.py:75-85` — `skill_manifest_prompt` per-turn instruction callback
- `co_cli/context/rules/06_skill_protocol.md` — discovery/use/drift/create reflexes
- `co_cli/commands/core.py:110-156` (`dispatch`) + `co_cli/commands/skills.py` (`/skills` family)
- `co_cli/bootstrap/core.py` — `create_deps()` skill loading
- `co_cli/daemons/dream/_reviewer.py` + `_housekeeping.py` — background skill reviewer, merge/decay

Peer files reviewed:

- `hermes-agent` (HEAD `bb7ff7dc3`): `skills/` (18 top-level categories, 71 skills); `agent/prompt_builder.py:1334-1600` (`build_skills_system_prompt`, mandatory-scan block at 1564-1589) and `:173-180` (`SKILLS_GUIDANCE`); `agent/skill_utils.py` (frontmatter parse, `skill_matches_platform`, condition filtering); `tools/skills_tool.py:1577-1638` (`skill_view`); `tools/skill_manager_tool.py:1099-1233` (`skill_manage`, 6 actions); `tools/env_passthrough.py` (session-scoped env allowlist)
- `opencode` (HEAD `fbf889db8`): `packages/core/src/skill.ts` (`SkillV2` service + frontmatter schema), `skill/discovery.ts` (local + remote-URL catalogs), `skill/guidance.ts` (`<available_skills>` system context), `tool/skill.ts` (model-callable `skill` loader); `config/command.ts` (separate command construct with `$ARGUMENTS`)

> Note (out of scope, for the spec owner): `docs/specs/skills.md` §4 still cites `co_cli/context/manifests/skill_manifest.py` for `render_skill_manifest()`; the live path is `co_cli/skills/manifest.py`. Fix via `/sync-doc`, not here.

## Architectural positions

All three systems now expose skills to the model through an index plus a model-callable load tool. They diverge on three axes: **how discovery is framed**, **whether the model can author skills**, and **how skill bodies reach the turn**.

### `co-cli` — hybrid: overlay + model-loadable reference + model-writable

Three invocation paths (`docs/specs/skills.md:41-54`):

1. **User slash-command** — `/doctor` → `dispatch()` (`co_cli/commands/core.py:134-151`) expands the body (with `$ARGUMENTS`/`$N` substitution) and returns `DelegateToAgent`; the REPL runs a fresh `run_turn()` with the body as input. The skill body *is* the prompt for that turn.
2. **Model inline use** — the model reads the per-turn `<available_skills>` manifest, calls `skill_view(name)`, and the body returns as a tool result inside the current turn. This is the primary agent-initiated path.
3. **Model write** — `skill_create` / `skill_edit` / `skill_patch` / `skill_delete` (all DEFERRED, approval-gated) write a user skill and `refresh_skills(deps)` makes it live immediately.

The manifest is rendered **per-turn** via `skill_manifest_prompt` (`co_cli/agent/_instructions.py:75-85`), post-static, so newly-created skills appear on the very next turn and skill mutations never churn the static prefix. Discovery framing is deliberate and light: "If exactly one skill clearly applies, load it… Skip discovery for trivial single-step replies" (`06_skill_protocol.md:10-20`) — explicitly *not* hermes's mandatory-scan.

### Hermes — model-loadable reference, mandatory-scan

The skills index is baked into the system prompt with a hard instruction (`agent/prompt_builder.py:1565-1567`):

> Before replying, scan the skills below. If a skill matches or is even partially relevant to your task, you MUST load it with skill_view(name) and follow its instructions.

The model loads via `skill_view(name)` (optionally a linked `file_path` for multi-file skills) and may revisit across turns. Authoring is a single mode-dispatched tool `skill_manage` with six actions — `create`, `patch`, `edit`, `delete`, `write_file`, `remove_file` (`tools/skill_manager_tool.py:1136`) — paired with a prompt nudge to save/patch skills proactively (`SKILLS_GUIDANCE`, `prompt_builder.py:173-180`).

### Opencode — read-only knowledge reference, model-loadable, externally authored

`SkillV2` (`packages/core/src/skill.ts`) discovers `*.md`/`**/SKILL.md` from local dirs, embedded definitions, **or remote URL catalogs** (`skill/discovery.ts` — fetches `index.json`, path-traversal-guarded). The model sees them through `skill/guidance.ts`, which renders the same `<available_skills>` XML shape co uses, permission-filtered per agent. The model loads a skill via the built-in `skill` tool (`tool/skill.ts`), which returns the body plus related files. Frontmatter is minimal — `name`, `description`, `slash` (`skill.ts:59-63`). There is **no model authoring path**: skills are discovered and applied, never created or patched by the model (file writes happen out-of-band; next session re-discovers). Templated/argument-bearing prompts live in a *separate* `command` construct (`config/command.ts`), not in skills.

### Consequence: convergence and divergence

- **Convergence:** co and opencode independently landed on the same `<available_skills>` index and both rejected hermes's "MUST load if even partially relevant" framing in favor of a lighter touch. All three now give the model a load tool — the original doc's headline gap ("model can't reach skills") is closed everywhere.
- **Divergence on authoring:** co and hermes are *model-writable* (co via four monomorphic tools, hermes via one dispatched tool); opencode is *read-only* to the model.
- **Divergence on body delivery:** co uniquely keeps the slash-command overlay path (body drives a whole turn) alongside the inline-reference path; hermes and opencode are reference-only.

## §4. Original gaps — current status

| # | Original gap (2026-04-21) | Status now | Evidence |
|---|---|---|---|
| 1 | No system-prompt skill discovery surface | **CLOSED** | `<available_skills>` rendered per-turn by `skill_manifest_prompt` (`_instructions.py:75-85`, `skills/manifest.py`); discovery reflex in `06_skill_protocol.md:10-20` |
| 2 | No skill authoring contract | **CLOSED** | `docs/specs/skills.md:172-238` (minimum shape, length budget, recommended phase structure); R1–R4 lint surfaced on every write (`skills/lint.py`); B1 no-marker gate for bundled set; `skill-creator` bundled meta-skill |
| 3 | No skill self-improvement loop | **CLOSED — and past parity** | In-session drift/create reflexes (`06_skill_protocol.md:32-62`); model-write tools (`tools/system/skills.py`); **plus** background dream-daemon `skill_reviewer` + `merge_skills`/`decay_skills` + usage sidecar (`daemons/dream/_reviewer.py`, `_housekeeping.py`, `skills/usage.py`) — neither peer has background curation |
| 4 | Sparse bundled library (1 skill) | **SUBSTANTIALLY CLOSED** | 8 bundled skills (`doctor`, `documents`, `office`, `plan`, `refactor`, `review`, `skill-creator`, `triage`); two ship executable assets (`co-extract-pdf`, `co-extract-office`) |
| 5 | No authoring-vs-installation quality tiering | **CLOSED** | Bundled skills gated by `tests/test_flow_skill_bundled_library.py` (load + R1–R4 + B1 + manifest count); user skills get startup/reload security scan (`scan_skill_content`) + integrity validation (`_validate_skill_content`) on every write |

The original recommended ordering (contract → library → discovery → self-improvement) was effectively followed, and all four landed.

## Where `co-cli` is now distinctive

### 1. Turn-scoped skill-env injection with rollback (still unique)

`skill-env` frontmatter injects env vars only for the dispatched turn, restored in a `finally` block (`docs/specs/skills.md:145-156`), filtered through `_SKILL_ENV_BLOCKED` (blocks `PATH`/`PYTHONPATH`/`HOME`/shell-loaders). Hermes's `env_passthrough.py` is an allowlist scoped to the **session** (ContextVar-backed), not the turn, and does not inject — it gates which host vars reach sandboxes. Opencode has no skill env mechanism. co is the only one with true per-turn injection + rollback.

### 2. Self-improvement is a full loop, not just a prompt nudge

co pairs in-session reflexes (`06_skill_protocol.md`) and model-write tools with a **background** layer no peer has: the dream daemon's `skill_reviewer` scans transcripts for drift/new procedures and patches/creates skills autonomously (`requires_approval=False`), and scheduled `merge_skills`/`decay_skills` consolidate similar user skills and archive stale ones (recall-protected, pin-exempt). Usage sidecars (`skills/usage.py`) track recall days to drive decay protection. Hermes has the prompt nudge + `skill_manage` but no background reviewer or decay; opencode has neither half.

### 3. Authoring contract is codified *and* enforced

Beyond a documented shape (`skills.md:172-238`), R1–R4 advisory lint attaches to every `skill_create`/`skill_edit`/`skill_patch` result as `lint_warnings`, integrity checks (`description` ≤1024, total ≤50k) hard-block the write, and the bundled library has its own B1 gate in CI. The `skill-creator` skill turns the contract into a guided workflow. Hermes has a strong de-facto structure but no lint/gate; opencode's frontmatter is minimal with no contract.

### 4. Monomorphic write tools (deliberate small-model trade)

co exposes four single-purpose tools (`skill_create`/`skill_edit`/`skill_patch`/`skill_delete`) where hermes uses one action-dispatched `skill_manage`. This is the same monomorphic-vs-dispatched trade co makes elsewhere — easier for a small model to call correctly. Not a consolidation candidate.

## Genuine remaining deltas (peer features co lacks)

These are small and optional — co's skill system is at or beyond parity overall.

1. **Remote skill catalogs.** Opencode discovers skills from a remote URL (`index.json` fetch, path-traversal-guarded — `skill/discovery.ts`). co loads only bundled + local user-global dirs; the `/skills` family is `list/check/lint/reload/usage/pin/unpin` — there is **no `install`/`install <url>`** path today (the original doc referenced one; it is not in the current command set). If skill sharing becomes a goal, a vetted-source install path is the missing piece. Gate any such path on the existing `scan_skill_content` + integrity validation.

2. **Multi-file skills.** Hermes `skill_view(name, file_path)` loads a specific linked file within a skill, and `skill_manage` has `write_file`/`remove_file` actions; opencode's `skill` tool returns related files. co's loader globs only `*/SKILL.md` and ignores sibling files for *discovery* (a `scripts/` asset is driven at runtime via `shell_exec`, not loaded into context). For most procedural skills the single-body model is sufficient; revisit only if a skill needs the model to read multiple reference files.

3. **Per-skill permission gating.** Opencode gates each skill load by agent permission (`tool/skill.ts` `permission.assert`). co gates skill *writes* (approval) but skill *reads* (`skill_view`) are ungated. Low priority — reads are non-mutating.

## Peer weaknesses (unchanged or worse)

1. **Hermes mandatory-scan is still over-aggressive.** "You MUST load any even partially relevant skill" (`prompt_builder.py:1565-1567`) drives tool churn and devotes prompt mass to an index many tasks don't need. co's "load exactly one clearly-applicable skill, skip for trivial replies" is the better-calibrated framing — keep it.

2. **Hermes skill bodies are heavy.** The `plan` SKILL.md is 338 lines; loading it via `skill_view` consumes real context for a short task. co's R4 lint (body ≤8000 chars, warn) and umbrella-merge curation actively push the other way.

3. **Opencode has no self-improvement.** Skills are static read-only knowledge with minimal frontmatter; the model cannot fix a stale skill or promote a discovered procedure — that work is fully out-of-band. This is the axis where co is furthest ahead.

## Bottom line

The 2026-04-21 edition called co's skill system "architecturally lean but under-used" with four load-bearing gaps. As of 2026-06-22 those gaps are closed: a per-turn `<available_skills>` manifest, model-callable read + write tools, an injected discovery/drift/create discipline, an enforced authoring contract with lint, 8 bundled skills, and a background curation loop. Against the two peers co is at or beyond parity — it is the only one of the three with turn-scoped env rollback and autonomous background skill curation, and it avoids both hermes's mandatory-scan churn and opencode's read-only static skills.

The remaining deltas are optional and small: a vetted remote-install path (opencode has one, co does not), multi-file skill bodies (hermes/opencode have them), and per-skill read gating (opencode). None is urgent; the first is the only one worth a plan if skill sharing becomes a product goal.
