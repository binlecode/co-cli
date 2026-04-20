# RESEARCH: fork-cc Skills

Scan basis:

- bundled skill loader in `skills/bundled/index.ts`
- bundled skill registry in `skills/bundledSkills.ts`
- concrete skill implementations in `skills/bundled/*.ts`

## 1. Source-of-Truth Runtime Model

`fork-cc` registers bundled skills programmatically at startup through `initBundledSkills()`. A skill counts as implemented in this checkout only if:

- the loader references it, and
- the concrete source file exists in `skills/bundled/`

Important source observation:

- `index.ts` references `dream`, `hunter`, and `runSkillGenerator` behind feature flags, but the corresponding source files are **not present** in this checkout.
- they are therefore not counted below as implemented bundled skills in this source tree

## 2. Complete Implemented Skill Inventory

Implemented bundled skills found in source: **14**

| Skill | Runtime gate | Functionality | Core implementation | Prompt design | Tool integration |
|------|--------------|---------------|---------------------|---------------|------------------|
| `batch` | always registered | Large-scale parallel repo changes across isolated worktrees/agents | Workflow wrapper over git/worktree orchestration | Forces decomposition and PR-oriented execution | agent/team/worktree, shell, file, task tools |
| `claude-api` | `BUILDING_CLAUDE_APPS` feature | Build apps with Claude API / Anthropic SDK | Domain guidance skill with bundled docs | Strong trigger rules around Anthropic SDK usage | `Read`, `Grep`, `Glob`, `WebFetch` |
| `claude-in-chrome` | auto-enabled when Chrome setup is available | Browser automation inside Chrome | Tool-steering workflow | Sequential action planning and observation | browser/MCP browser tools, screenshots, page inspection |
| `debug` | always registered | Diagnose current Claude Code session via debug logs | Investigation workflow | Evidence-first log reading and fault explanation | `Read`, `Grep`, `Glob` |
| `keybindings-help` | enabled by keybinding-customization feature check | Customize `~/.claude/keybindings.json` | Config-editing workflow | Read-before-write and safe merge guidance | primarily `Read`; downstream file-edit flow |
| `loop` | `AGENT_TRIGGERS` feature | Re-run a prompt or command on a recurring interval | Scheduling wrapper | Encodes cadence and repetition workflow | scheduling/cron tools, task control |
| `lorem-ipsum` | `USER_TYPE=ant` | Generate filler text for long-context testing | Prompt-only utility skill | Minimal formatting/size constraints | no meaningful tool dependence |
| `remember` | always registered | Review memory entries and promote durable ones | Memory-curation workflow | Durable-vs-ephemeral distinction and promotion rules | file/memory inspection and write flow |
| `schedule` | `AGENT_TRIGGERS_REMOTE` feature | Schedule remote cloud agents on cron | Remote-agent workflow wrapper | Strong create/list/update/run sequencing with environment checks | remote trigger tool, ask-user, repo/env/connectors checks |
| `simplify` | always registered | Review changed code for quality/reuse/efficiency and fix issues | Review-and-edit workflow | Critique first, then targeted cleanup | read/search/edit and shell/test flow |
| `skillify` | `USER_TYPE=ant` | Turn a repeatable session process into a reusable skill | Prompt + artifact-generation workflow | Captures repeatable workflows into `SKILL.md` format | `Read`, `Write`, `Edit`, `Glob`, `Grep`, `AskUserQuestion`, scoped `Bash(mkdir:*)` |
| `stuck` | always registered | Investigate frozen or slow sessions | Runtime diagnosis workflow | Focus on process/log/environment causes before intervention | shell, logs, file read/search |
| `update-config` | always registered | Modify harness `settings.json` / hooks / permissions | Config-management workflow | Strong read-before-write, merge, and hook-verification discipline | primarily `Read`; downstream config/file edit flow |
| `verify` | always registered | Verify implementation against specs/expectations | Verification workflow | Explicit pass/fail reasoning and evidence collection | read/search, shell/test, optional agent verification |

## 3. Structural Read

### A. The dominant pattern is "workflow wrapper over a rich control plane"

The most important `fork-cc` skill trait is not prose quality. It is the combination of:

- a dispatch trigger
- a workflow contract
- access to a rich control-plane tool surface

### B. `fork-cc` skills rely heavily on orchestration/control tools

Relative to `co-cli`, the key difference is how often these skills depend on:

- agents and teams
- worktrees
- browser automation
- cron/remote triggers
- config-editing primitives

### C. Implication for `co-cli`

When researching `fork-cc` skills, document:

1. exact implemented skill inventory from loader + source files
2. runtime gate or visibility condition
3. functionality
4. core implementation path
5. prompt discipline
6. tool integration assumptions
