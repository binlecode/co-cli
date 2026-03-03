# TODO: Openclaw + Skills Adoption Review

Task type: doc+code

## Context

Three source TODO docs are being coordinated into a unified delivery roadmap:
- `docs/TODO-openclaw-adoption.md` — concrete implementation tasks for P1/P2 openclaw features (TASK-1 to TASK-9)
- `docs/TODO-gap-openclaw-analysis.md` — full problem analysis for §1–§14 gaps
- `docs/TODO-skills-system.md` — co-cli skills system gaps (Gap 1–10)

### Code Accuracy Verification

Inspection of current source code against the three source TODOs revealed:

1. **`TODO-skills-system.md` line 3–5**: Claims "Co-cli's current skill mechanism (`co_cli/_commands.py`) loads `.claude/skills/*.md` files as slash commands." **Inaccurate**: `_commands.py` contains only 8 hardcoded slash commands (`help`, `clear`, `status`, `tools`, `history`, `compact`, `model`, `forget`) and a static `COMMANDS: dict[str, SlashCommand]`. No skill loader, no directory scan, no dynamic registration exists anywhere in `main.py`, `_commands.py`, or `agent.py`. **This exposes a missing foundation task — Gap 0: Skills Loader Bootstrap — absent from all three source docs.**

2. **Test file mismatch for TASK-1**: `TODO-gap-openclaw-analysis.md §1` specifies `tests/test_approval.py`; `TODO-openclaw-adoption.md TASK-1` specifies `tests/test_commands.py`. This plan adopts `tests/test_approval.py` (co-located with `co_cli/_approval.py`).

3. **`TODO-skills-system.md` Gap 6 (/doctor skill)** — The gap description says the skill calls `check_capabilities()` which reads `ctx.deps` directly. It does NOT require subagent delegation. Only Gap 10 (`context: fork`) requires Subagent Phase A. This plan schedules /doctor in Batch 4, not blocked.

4. **`TODO-openclaw-adoption.md TASK-4`** references `_parse_created()` in `memory.py:101`. Reference is accurate per code inspection.

5. **`TODO-openclaw-adoption.md TASK-3 derive_pattern`** spec says collect up to 3 non-flag tokens then append ` *`. The gap-analysis doc (§2) says "prompt user for optional pattern (default: exact command)". These conflict. This plan adopts `derive_pattern()` auto-derivation (adoption.md) with the derived pattern shown in confirmation output — user can `/approvals clear <id>` to remove.

## Problem & Outcome

**Problem**: Three TODO docs describe overlapping work with no unified sequencing, a missing foundation task (skills loader), conflicting test file locations, and unclear subagent dependencies. Without coordination, implementation risks include: building Skills Gaps 1-5 on a non-existent loader, or sequencing subagent delegation earlier than needed.

**Outcome**: A single prioritized implementation roadmap with cost-benefit scores, a correct dependency graph, batched TASK items for `/orchestrate-dev` execution, and a clear deferred/blocked register.

## Scope

**In scope:**
- Batch 1–4 implementation tasks (TASK-01 to TASK-14 below)
- Gap 0 (skills loader bootstrap) — new task not in source TODOs
- Cost-benefit scoring for each feature
- Dependency graph for tool + skill system

**Out of scope (tracked in source TODOs or separate docs):**
- §8 MMR re-ranking, §9 Embedding provider — P3, separate evaluation tracks
- §10 Process registry / backgrounding — tracked in `TODO-background-execution.md`
- §11 Security audit (`co doctor`) command — P3, after §7
- Skills Gap 8 (allowed-tools), Gap 9 (shell preprocessing), Gap 10 (context: fork) — P3
- §13 Cron scheduling — requires session persistence + background-exec
- §14 Config includes — P3, team-use case only
- `/new` slash command (TASK-9) — blocked on `_index_session_summary()` in `_history.py`

## Feature Cost-Benefit Matrix

| Feature | Source | Effort | User Value | Security Value | Dependency | Decision |
|---------|--------|--------|------------|----------------|------------|----------|
| Shell arg validation | TASK-1 | S | Medium | High — closes path-escape gap | — | **Batch 1** |
| Exec approvals module | TASK-2 | M | Very High — eliminates daily re-prompting | High | — | **Batch 1** |
| Wire exec approvals + /approvals | TASK-3 | S | completes TASK-2 | — | TASK-2 | **Batch 1** |
| Skills loader bootstrap | Gap 0 (NEW) | S | High — foundation for all skills | — | — | **Batch 1** |
| Skills frontmatter parsing | Gap 1 | S | High — enables Gaps 2-6, 8 | — | Gap 0 | **Batch 2** |
| Skills arg substitution | Gap 2 | S | High — parameterized templates | — | Gap 1 | **Batch 2** |
| Skills description injection | Gap 3 | S | Medium — model-aware skill invocation | — | Gap 1 | **Batch 2** |
| Temporal decay scoring | TASK-4 | S | Medium — freshness in memory recall | — | — | **Batch 3** |
| Model fallback | TASK-5 | S | High — resilience, no session death on API error | — | — | **Batch 3** |
| Session persistence module | TASK-6 | M | Low — metadata only, not history | — | — | **Batch 3** |
| Wire session persistence | TASK-7 | S | completes TASK-6 | — | TASK-6 | **Batch 3** |
| Doctor security checks | TASK-8 | S | Medium — posture visibility | High — catches misconfigs | TASK-2 | **Batch 4** |
| Skills flags + env gating | Gap 4+5 | S | Medium — control + safety | — | Gap 1 | **Batch 4** |
| /doctor skill | Gap 6 | S | Medium — agent self-awareness UX | — | Gap 1-3, TASK-8 | **Batch 4** |
| Subagent Phase A (research) | Sub-A | M | High — focused delegation | — | optional bg-exec | **Batch 5** |
| Subagent Phase B (analysis) | Sub-B | S | Medium | — | Phase A | **Batch 5** |
| /new slash command | TASK-9 | S | High | — | `_index_session_summary` | **Blocked** |
| Skills allowed-tools | Gap 8 | M | Medium | — | Gap 1 | **P3 post-B5** |
| Skills context: fork | Gap 10 | M | High — clean history | — | Subagent Phase A | **P3 post-B5** |
| Skills shell preprocessing | Gap 9 | S | Medium | — | Gap 2 | **P3** |
| MMR re-ranking | §8 | S | Medium | — | TASK-4 | **P3** |
| Embedding provider | §9 | L | High | — | §8 + tag-junction | **P3, large** |
| Cron scheduling | §13 | XL | High | — | TASK-7 + bg-exec | **P3, separate** |
| Config includes | §14 | S | Low | — | — | **P3** |

## Subagent Delegation Dependency Map

Features that depend on `TODO-subagent-delegation.md`:

| Feature | Depends on | Notes |
|---------|------------|-------|
| Skills Gap 10 (`context: fork`) | Phase A | Fork context reuses research sub-agent machinery |
| Skills Gap 8 (`allowed-tools`) | None | Per-skill approval wiring in `_handle_approvals()` only |
| /doctor skill (Gap 6) | **None** | `check_capabilities()` reads `ctx.deps` directly — no delegation needed |
| §9 Embedding provider | None | Separate provider ABC, no delegation |

**Subagent Phase A prerequisite (`TODO-background-execution.md`)**: Listed as "should ship first" not "must". Phase A (synchronous `delegate_research` tool) works without background infrastructure. This plan schedules Phase A in Batch 5 as an independent track.

**Dependency graph:**

```
Gap 0 (loader) → Gap 1 (frontmatter) → Gap 2 (arg sub) → Gap 9 (P3)
                                      → Gap 3 (description inject)
                                      → Gap 4 (flags)
                                      → Gap 5 (env gating)
                                      → Gap 6 (/doctor, also needs TASK-8)
                                      → Gap 8 (allowed-tools, P3)

TASK-2 (exec approvals module) → TASK-3 (wire approvals + /approvals cmd)
TASK-2 → TASK-8 (security checks use exec-approvals loader)
TASK-6 (session module) → TASK-7 (wire session)
TASK-7 → §13 (cron, P3)
TASK-4 (decay) → §8 (MMR, P3)

Subagent Phase A → Gap 10 (context: fork, P3)
Subagent Phase A → Phase B → Phase C

TASK-9 (/new) → _index_session_summary [BLOCKED, not implemented]
§9 (embeddings) → §8 (MMR) + tag-junction table [BLOCKED]
```

## Tool + Skill System Refactoring Architecture

### Current State
- **Tools**: Hardcoded `agent.tool()` registrations in `agent.py:267-294`. No dynamic registration.
- **Skills**: Non-existent in co-cli. `.claude/skills/` belongs to Claude Code (the AI tool). No `.co-cli/skills/` scanning in any file.
- **Approval**: Session-scoped `auto_approved_tools` set in `CoDeps` + in-memory safe-command check. No persistence.
- **Slash commands**: Static `COMMANDS` dict in `_commands.py`, 8 hardcoded entries.

### Target State (after Batches 1–4)
- **Tools**: Unchanged. No tool registration refactor needed for B1-B4.
- **Skills**: Dynamic loader scans `.co-cli/skills/*.md` at startup; frontmatter controls behavior; skill body expands into user message on invocation.
- **Approval**: Exec approvals persisted to `.co-cli/exec-approvals.json` (mode 0o600); pattern-matched on each shell tool call.
- **Slash commands**: Static `COMMANDS` + dynamic `SKILL_COMMANDS` (separate dict, merged into completer). Reserved names (`help`, `clear`, etc.) protected from skill name collisions.

### Architectural Decisions

**AD-1: Skills path is `.co-cli/skills/`, NOT `.claude/skills/`**
`.claude/` is owned by Claude Code. Co-cli user skills go to `.co-cli/skills/`. The source TODO reference to `.claude/skills/` is a docs error.

**AD-2: Skills use a separate `SKILL_COMMANDS` dict, not injected into `COMMANDS`**
Hardcoded commands are preserved. Dispatch checks `COMMANDS` first (always wins), then `SKILL_COMMANDS`. Skills cannot shadow built-in commands.

**AD-3: Skills loader runs once at `chat_loop()` startup**
Skills scanned at startup; directory changes require restart. Matches Claude Code behavior.

**AD-4: `skill_registry` stored in `CoDeps` for system prompt injection**
Loaded skills (name + description) stored as `skill_registry: list[dict]` in `CoDeps`. The `@agent.system_prompt` callback reads `ctx.deps.skill_registry` for the `## Available Skills` section.

**AD-5: Subagent delegation is NOT a prerequisite for Batches 1–4**
Only Gap 10 is blocked on subagent. All other skills and tools ship independently.

**AD-6: Source TODOs are NOT deleted or merged**
`TODO-openclaw-adoption.md`, `TODO-gap-openclaw-analysis.md`, and `TODO-skills-system.md` remain as detailed reference. They are pruned as work ships per the CLAUDE.md lifecycle rule.

**AD-7: `SkillCommand` is a separate dataclass from `SlashCommand`**
`SlashCommand` is `@dataclass(frozen=True)` with fields `name`, `description`, `handler: Callable`. Skills cannot use `SlashCommand` because they carry `body`, `argument_hint`, `user_invocable`, `disable_model_invocation`, `requires` — fields incompatible with the `handler` callable model. `SkillCommand` is a parallel `@dataclass(frozen=True)` with its own field set. `dispatch()` checks `COMMANDS: dict[str, SlashCommand]` first, then `SKILL_COMMANDS: dict[str, SkillCommand]`. No subclassing.

**AD-8: Bundled skills ship inside the package; project-local skills override bundled**
System skills (like `/doctor`) must work regardless of `cwd`. `co_cli/bundled_skills/` is scanned first. Project-local `.co-cli/skills/` is scanned second; same-name files override bundled ones. This gives users a clean customization path without needing to copy bundled files.

## Implementation Plan

### Batch 1 — Security Foundation (P1)

#### TASK-01 — Shell Arg Validation

**Source**: `docs/TODO-openclaw-adoption.md TASK-1`

**files:**
- `co_cli/_approval.py` — add `_validate_args(args_str: str) -> bool`; wire into `_is_safe_command()` after prefix match
- `tests/test_approval.py` — new file with all cases from TASK-1

**done_when:** `uv run pytest tests/test_approval.py -v` passes. Specifically: `git diff --no-index /etc/passwd /dev/null` returns False; `git diff HEAD~1` returns True; `grep foo*` returns False; `git status --short` returns True.

---

#### TASK-02 — Exec Approvals Module

**Source**: `docs/TODO-openclaw-adoption.md TASK-2`

**files:**
- `co_cli/_exec_approvals.py` — new file: `load_approvals`, `save_approvals`, `find_approved`, `add_approval`, `update_last_used`, `prune_stale`, `derive_pattern`
- `tests/test_exec_approvals.py` — new file with all cases from TASK-2

**Clarification — `pattern == "*"` semantics**: `derive_pattern()` never produces a bare `*` — it always appends ` *` after at least one non-flag token (minimum: `"ls *"`). A bare `"*"` entry is a user-managed sentinel that `find_approved()` skips silently (safety guard against catch-all approvals). Test cases must verify: `derive_pattern("ls")` → `"ls *"` (not `"*"`); `find_approved("anything", [{"pattern": "*", ...}])` → None.

**done_when:** `uv run pytest tests/test_exec_approvals.py -v` passes all cases including: file mode 0o600 on first and second write; bare `"*"` pattern entries skipped by `find_approved` (returns None); `prune_stale` removes entries older than `max_age_days`; `derive_pattern("grep -r foo")` returns `"grep *"`; `derive_pattern("ls")` returns `"ls *"` (not bare `"*"`).

---

#### TASK-03 — Wire Exec Approvals + /approvals Command

**Source**: `docs/TODO-openclaw-adoption.md TASK-3`

**Prerequisite**: TASK-02

**Scope gate**: The persisted-approval lookup in `_handle_approvals()` is gated on `call.tool_name == "run_shell_command"` **only** — it must not apply to `save_memory`, `save_article`, `create_email_draft`, or MCP tools. Mirror the existing `_is_safe_command` guard. The `exec_approvals_path` field on `CoDeps` is consumed inside `_orchestrate._handle_approvals()`, not inside `_approval.py`.

**files:**
- `co_cli/deps.py` — add `exec_approvals_path: Path`
- `co_cli/main.py` — pass `exec_approvals_path` in `create_deps()`; startup stale-prune
- `co_cli/_orchestrate.py` — modify `_handle_approvals()`: gate exec-approvals lookup on `call.tool_name == "run_shell_command"` only; check persisted approvals before prompting; save on "a" choice
- `co_cli/_commands.py` — add `_cmd_approvals()` (subcommands: `list`, `clear [id]`); register as `"approvals"` in `COMMANDS`

**done_when:** `uv run pytest tests/test_exec_approvals.py tests/test_commands.py -v` passes (no regressions). Functional test in `test_exec_approvals.py` verifies round-trip: write approval file via `add_approval()` → call `find_approved()` with same cmd → assert match (simulates post-restart auto-approval). Manual: approve shell command with "a" → `.co-cli/exec-approvals.json` written with mode 0o600. Restart `co chat` → same command auto-approved without prompt.

---

#### TASK-04 — Skills Loader Bootstrap (NEW — not in any source TODO)

**Why new**: `TODO-skills-system.md` assumes a loader exists but none exists in `_commands.py`. All skills gaps (1–10) require this foundation.

**New type `SkillCommand`** (NOT a subclass of `SlashCommand` — frozen inheritance is fragile): define `@dataclass(frozen=True) class SkillCommand` with fields `name: str`, `description: str`, `body: str`, `argument_hint: str`, `user_invocable: bool`, `disable_model_invocation: bool`, `requires: dict`. `SKILL_COMMANDS: dict[str, SkillCommand]`.

**Dispatch → LLM bridge**: `CommandContext` is a mutable (non-frozen) dataclass — add `skill_body: str | None = None` field (same pattern as the existing `model_settings: ... = None` which `/model` sets post-dispatch). When `dispatch()` matches a skill, set `ctx.skill_body = skill.body` and return `True, None`. In `main.py`, after `dispatch_command()` returns `handled=True`: if `cmd_ctx.skill_body is not None`, set `user_input = cmd_ctx.skill_body` and fall through to the LLM turn (do NOT `continue`). If `skill_body is None`, `continue` as normal. No change to `dispatch()` return type.

**files:**
- `co_cli/_commands.py` — add `skill_body: str | None = None` to `CommandContext`; define `SkillCommand` dataclass; add `_load_skills(skills_dir: Path) -> dict[str, SkillCommand]`; scan `*.md` files; use existing `_frontmatter.parse_frontmatter()` for frontmatter; extract `body` (frontmatter-stripped); protect reserved names (warn + skip); update `dispatch()` to check `SKILL_COMMANDS` after `COMMANDS` miss, set `ctx.skill_body = skill.body`, return `True, None`
- `co_cli/main.py` — call `_load_skills(Path.cwd() / ".co-cli/skills")` in `chat_loop()` before completer build; merge skill names into `WordCompleter`; after dispatch returns `handled=True`, check `cmd_ctx.skill_body is not None` — if set, assign `user_input = cmd_ctx.skill_body` and fall through to LLM turn; else `continue` as before
- `tests/test_skills_loader.py` — new file

**done_when:** With `.co-cli/skills/test-skill.md` containing body "hello world" (no frontmatter), `dispatch()` called with `/test-skill` sets `cmd_ctx.skill_body == "hello world"` and returns `(True, None)`. Skill named `help.md` is rejected with a warning. `SkillCommand` is a separate type from `SlashCommand` (no inheritance). `uv run pytest tests/test_skills_loader.py -v` passes.

---

### Batch 2 — Skills System Core (P1)

#### TASK-05 — Skills Frontmatter Parsing

**Source**: `docs/TODO-skills-system.md Gap 1`

**Prerequisite**: TASK-04

**files:**
- `co_cli/_commands.py` — update `_load_skills()`: parse `description`, `argument-hint`, `user-invocable` (default True), `disable-model-invocation` (default False) from YAML frontmatter; populate `SkillCommand` fields; body must contain no frontmatter block
- `tests/test_skills_loader.py` — add frontmatter test cases

**done_when:** `uv run pytest tests/test_skills_loader.py -v -k frontmatter` passes. Skill `.md` with YAML frontmatter: `description` field on `SkillCommand.description`; `body` contains only Markdown content (no YAML block).

---

#### TASK-06 — Skills Argument Substitution

**Source**: `docs/TODO-skills-system.md Gap 2`

**Prerequisite**: TASK-05

**files:**
- `co_cli/_commands.py` — add substitution pass in skill dispatch: replace `$ARGUMENTS` (full remainder), `$0` (command name), `$1`…`$N` (positional args); backward-compat: if no `$ARGUMENTS` placeholder, append remainder after body
- `tests/test_skills_loader.py` — add substitution test cases

**done_when:** `/skill-name foo bar` with `$ARGUMENTS` in body produces user message with `$ARGUMENTS` replaced by `foo bar`. Skill without any `$` placeholder: args appended after body unchanged. `uv run pytest tests/test_skills_loader.py -v -k substitution` passes.

---

#### TASK-07 — Skills Description Injection into System Prompt

**Source**: `docs/TODO-skills-system.md Gap 3`

**Prerequisite**: TASK-05

**Assignment order**: In `chat_loop()`, `create_deps()` runs first (producing `deps` with `skill_registry=[]`), then `_load_skills()` runs, then `deps.skill_registry` is assigned from the loaded skills. `CoDeps` is a plain mutable dataclass — post-construction mutation is correct and consistent with how `_opening_ctx_state` and `_safety_state` are assigned (`main.py:145-146`).

**files:**
- `co_cli/deps.py` — add `skill_registry: list[dict] = field(default_factory=list)` (each dict: `name`, `description`, `disable_model_invocation`)
- `co_cli/main.py` — after `create_deps()` and after `_load_skills()`, assign `deps.skill_registry = [{"name": s.name, "description": s.description, "disable_model_invocation": s.disable_model_invocation} for s in skill_commands.values() if s.description and not s.disable_model_invocation]`
- `co_cli/agent.py` — add `@agent.system_prompt def add_available_skills(ctx)`: build `## Available Skills` section from `ctx.deps.skill_registry`; cap at 2KB total; if cap exceeded, append `(+N more — type / to see all)`
- `tests/test_skills_loader.py` — add system prompt injection test

**done_when:** In a session with a skill file that has a `description` field and `disable-model-invocation: false`, the per-turn system prompt contains `## Available Skills` with that skill's name and description. Skill with `disable-model-invocation: true` is excluded from this section. `deps.skill_registry` is assigned after `create_deps()`, not inside it. `uv run pytest tests/test_skills_loader.py -v -k description_inject` passes.

---

### Batch 3 — Resilience + Memory (P2)

#### TASK-08 — Temporal Decay Scoring

**Source**: `docs/TODO-openclaw-adoption.md TASK-4`

**files:**
- `co_cli/tools/memory.py` — add `_decay_multiplier(ts_iso: str, half_life_days: int) -> float`; apply in `recall_memory()` FTS path after building `matches` list; skip entries where `decay_protected=True`
- `co_cli/config.py` — add `memory_recall_half_life_days: int = Field(default=30, ge=1)` + env var `CO_MEMORY_RECALL_HALF_LIFE_DAYS`
- `co_cli/deps.py` — add `memory_recall_half_life_days: int = 30`
- `co_cli/main.py` — pass `memory_recall_half_life_days=settings.memory_recall_half_life_days` in `create_deps()`
- `tests/test_memory_decay.py` — new file with 5 cases from TASK-4

**done_when:** `uv run pytest tests/test_memory_decay.py -v` passes all cases. `_decay_multiplier(age_days=0)` returns 1.0; `age_days=30, half_life=30` returns ≈0.5 (±0.01); `decay_protected=True` entry not penalized; future-dated timestamp returns 1.0 (clamped). **Scope**: Decay applies to FTS5 path only — grep backend sort-by-recency is unchanged (document this in a comment in `_grep_recall()` to prevent future redundant additions).

---

#### TASK-09 — Model Fallback

**Source**: `docs/TODO-openclaw-adoption.md TASK-5`

**Implementation approach — in-place model swap (not `get_agent()` rebuild)**: Calling `get_agent()` mid-chat-loop re-runs `_migrate_memories_dir()`, re-instantiates MCP servers, and requires re-entering `AsyncExitStack`. Instead, use the in-place swap already demonstrated by `_switch_ollama_model()` in `_cmd_model`: swap `agent.model` and rebuild `agent.system_prompt` in-place. This avoids MCP reconnect issues entirely.

**Provider scope — same-provider fallback only**: `llm_fallback_models` lists models for the active provider only (Ollama → another Ollama model; Gemini → another Gemini model). The `_swap_model_inplace` helper is provider-aware: Ollama path reuses `_switch_ollama_model()` logic (OpenAIProvider); Gemini path updates the `google-gla:model_name` string on `agent.model` directly. Cross-provider fallback (e.g., Gemini primary → Ollama fallback) is NOT supported in MVP — add a startup warning if a configured fallback model name does not match the active provider's naming convention.

**Error triggering**: Fallback triggers on any `turn_result.outcome == "error"` (provider error after retries exhausted). Context-overflow is not explicitly detected — a context-overflow turn will also trigger fallback, which will fail on the same history and return a second error turn. This is acceptable for MVP: the user sees two consecutive error messages rather than a hang. Document this limitation in a comment at the fallback call site.

**Field naming**: `fallback_models` → `llm_fallback_models` to match the domain-prefix convention (`memory_max_count`, `web_http_max_retries`, `shell_max_timeout`). Env var: `CO_LLM_FALLBACK_MODELS`.

**files:**
- `co_cli/config.py` — add `llm_fallback_models: list[str] = Field(default_factory=list)` + comma-split `field_validator` (same pattern as `_parse_safe_commands`) + env var `CO_LLM_FALLBACK_MODELS`
- `co_cli/deps.py` — add `llm_fallback_models: list[str] = field(default_factory=list)`
- `co_cli/_commands.py` — refactor `_switch_ollama_model()` into provider-aware `_swap_model_inplace(agent, model_name, provider_name: str, settings)`; Ollama path: OpenAIProvider + OpenAIChatModel; Gemini path: update `google-gla:{model_name}` string; reused by both `/model` command and fallback
- `co_cli/main.py` — capture `pre_turn_history` before `run_turn()`; on `turn_result.outcome == "error"` with non-empty `deps.llm_fallback_models`: pop next model, call `_swap_model_inplace()` on existing agent, replay `run_turn()` with `pre_turn_history`; max one fallback per turn; add comment noting context-overflow limitation

**done_when:** `uv run pytest tests/test_agent.py -v` passes. Code review confirms: `pre_turn_history` captured before `run_turn()`; fallback uses in-place swap (no new `get_agent()` call); `CoDeps.llm_fallback_models` is `list[str]` defaulting to `[]`; MCP server count unchanged after fallback (verify with `uv run co status`); fallback triggers on any `outcome == "error"` (limitation comment present at call site).

---

#### TASK-10 — Session Persistence Module

**Source**: `docs/TODO-openclaw-adoption.md TASK-6`

**files:**
- `co_cli/_session.py` — new file: `load_session`, `save_session`, `is_fresh`, `new_session`, `touch_session`, `increment_compaction`
- `tests/test_session.py` — new file with 11 cases from TASK-6

**done_when:** `uv run pytest tests/test_session.py -v` passes all cases. `save_session()` mode 0o600 on first and second write verified. `is_fresh(None, 60)` returns False. Future-dated `last_used_at` returns True (clock skew guard). `touch_session()` / `increment_compaction()` do not mutate input dict.

---

#### TASK-11 — Wire Session Persistence

**Source**: `docs/TODO-openclaw-adoption.md TASK-7`

**Prerequisite**: TASK-10

**files:**
- `co_cli/config.py` — add `session_ttl_minutes: int = Field(default=60, ge=1)` + env var `CO_SESSION_TTL_MINUTES`
- `co_cli/main.py` — load/restore/save/touch/increment session in `chat_loop()`; save in outer `while True` loop body unconditionally (not inside `if mcp_servers:`)

**done_when:** `uv run pytest tests/test_agent.py tests/test_commands.py -v` passes (no regressions). Manual: `co chat` exit + restart within 60 min → same `session_id` in logs. Wait past TTL → new `session_id`.

---

### Batch 4 — Security Visibility + Skills Control (P2)

#### TASK-12 — Doctor Security Checks

**Source**: `docs/TODO-openclaw-adoption.md TASK-8`

**Prerequisite**: TASK-02 (exec approvals module needed for wildcard pattern check)

**Display consolidation**: `main.py:status()` (line 351–355) calls `get_status()` + `render_status_table()` directly — it does NOT call `_cmd_status()`. To avoid two diverging display sites, add `render_security_findings(findings: list[SecurityFinding]) -> None` in `status.py`; both `main.py` and `_commands.py` call this shared helper after their respective `render_status_table()` call.

**files:**
- `co_cli/status.py` — add `SecurityFinding` dataclass (severity, check_id, detail, remediation); add `check_security(_user_config_path=None, _project_config_path=None) -> list[SecurityFinding]` with 3 checks; add `render_security_findings(findings: list[SecurityFinding]) -> None` display helper
- `co_cli/_commands.py` — update `_cmd_status()`: call `check_security()` then `render_security_findings()`; empty findings = no output
- `co_cli/main.py` — update `status()` CLI command: call `check_security()` then `render_security_findings()`; same empty-list behavior
- `tests/test_status.py` — new file with 4 cases from TASK-8

**done_when:** `uv run pytest tests/test_status.py -v` passes. Both `chmod 644 ~/.config/co-cli/settings.json && uv run co status` (CLI) AND `/status` in REPL show WARN. Both `chmod 600` paths show nothing extra. Exec-approvals file with `pattern == "*"` shows `exec-approval-wildcard` finding via both callers.

---

#### TASK-13 — Skills Flags + Environment Gating

**Source**: `docs/TODO-skills-system.md Gap 4 + Gap 5` (combined — minimal touch to same function)

**Prerequisite**: TASK-05

**files:**
- `co_cli/_commands.py` — apply `user-invocable: false` filter in completer build; apply `requires` block gating in `_load_skills()` (bins, anyBins, env, os checks using `shutil.which`, `os.getenv`, `sys.platform`); log DEBUG on skip
- `tests/test_skills_loader.py` — add flag + gating test cases

**done_when:** Skill with `user-invocable: false` absent from `/` autocomplete (but dispatch still callable). Skill with `requires: bins: ["nonexistent-xyz"]` absent from loaded skill list. `uv run pytest tests/test_skills_loader.py -v -k "gating or flag"` passes.

---

#### TASK-14 — /doctor Skill

**Source**: `docs/TODO-skills-system.md Gap 6`

**Prerequisite**: TASK-05 (frontmatter parsing for `disable-model-invocation`); TASK-12 (security findings integration)

**Bundled skill path**: `.co-cli/skills/doctor.md` is project-scoped and unavailable to users running `co chat` outside a project. `doctor.md` must be a bundled asset shipped inside the package at `co_cli/bundled_skills/doctor.md`. `_load_skills()` merges bundled skills first, then project-local `.co-cli/skills/` skills (project-local same-name files override bundled ones).

**files:**
- `co_cli/tools/capabilities.py` — new file: `check_capabilities(ctx: RunContext[CoDeps]) -> dict[str, Any]`; reads live dep state (knowledge backend, effective reranker, Google present, Obsidian reachable, Brave key set, MCP servers configured); returns `display` + individual capability fields
- `co_cli/agent.py` — register `check_capabilities`, `requires_approval=False`
- `co_cli/bundled_skills/doctor.md` — new bundled skill file with `disable-model-invocation: true` frontmatter; prompt instructs agent to call `check_capabilities` and describe state in personality voice
- `co_cli/_commands.py` — update `_load_skills()`: load bundled skills from `Path(__file__).parent / "bundled_skills"` first, then merge project-local skills (project overrides bundled on name collision)

**done_when:** `/doctor` works from ANY directory (not just project dirs with `.co-cli/`). Produces capability summary in personality voice. `check_capabilities()` visible in `uv run co status` tool list. With `CO_KNOWLEDGE_RERANKER_PROVIDER=none`, output notes degraded search quality. Project-local `doctor.md` overrides bundled version.

---

### Deferred / Blocked

| Task | Source | Blocker | Decision |
|------|--------|---------|----------|
| /new slash command | TASK-9 | `_index_session_summary()` not implemented in `_history.py` | Defer — do not start |
| Subagent Phase A (research) | TODO-subagent-delegation.md | None — bg-exec prerequisite is "should", not "must" | Batch 5 independent track |
| Subagent Phase B (analysis) | Sub-B | Phase A | Batch 5, after Phase A |
| Subagent Phase C (budget) | Sub-C | Phase B | Batch 5 |
| Skills allowed-tools (Gap 8) | Gap 8 | Gap 1 + approval wiring design | P3, post-Batch 5 |
| Skills context: fork (Gap 10) | Gap 10 | Subagent Phase A | P3, post-Batch 5 |
| Skills shell preprocessing (Gap 9) | Gap 9 | Gap 2 | P3, low priority |
| MMR re-ranking (§8) | §8 | TASK-08 (decay) | P3 |
| Embedding provider (§9) | §9 | §8 + tag-junction table | P3, L-effort, separate eval |
| Cron scheduling (§13) | §13 | TASK-11 + bg-exec TODO | P3, tracked separately |
| Config includes (§14) | §14 | — | P3, team-use case only |

## Testing

Each batch runs its own suite plus full regression:

**Batch 1:**
```bash
uv run pytest tests/test_approval.py tests/test_exec_approvals.py tests/test_commands.py -v
```

**Batch 2:**
```bash
uv run pytest tests/test_skills_loader.py -v
```

**Batch 3:**
```bash
uv run pytest tests/test_memory_decay.py tests/test_agent.py tests/test_session.py -v
```

**Batch 4:**
```bash
uv run pytest tests/test_status.py tests/test_skills_loader.py tests/test_commands.py -v
```

**Full regression (end of every batch):**
```bash
uv run pytest -v
```

## Open Questions

All open questions from source TODOs that were answerable by code inspection are resolved above (marked "Not an open question" in context). No unresolved open questions remain before proceeding to Core Dev review.

## Final — Team Lead

Plan approved after 2 cycles. All blocking items resolved; all minor issues addressed.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev openclaw-skills-adoption-review`
