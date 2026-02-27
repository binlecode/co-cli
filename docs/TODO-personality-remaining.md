# TODO: Personality Remaining Fixes

Remaining unimplemented fixes from the personality design review. Fix 2+3 shipped as
heavier seed + `load_task_strategy` tool (see `DESIGN-02-personality.md`).

---

## Fix 1 — User Model

**Why:** Co models the AI (soul seed, strategies) but not the user. Personality adapts
its style but has nothing to adapt to — the role plays into the void.

**Best practice:** Letta/MemGPT maintains a `human` block alongside `persona` — a
dedicated, always-updated profile of the user (expertise, preferences, domain). Character.AI
builds a persistent user profile that shapes every turn.

**Design:**

A dedicated user profile file with YAML frontmatter for structured fields, free-form body
for natural-language observations. Loaded per-turn as a `## User Context` block, injected
before `## Learned Context`.

```
.co-cli/personality/user-profile.md

---
expertise: intermediate          # novice | intermediate | senior | expert
domain: backend engineering      # primary work context
communication_style: direct      # direct | detailed | collaborative
---
Prefers bun over npm.
Working on Go microservices with Kubernetes.
Gets frustrated with over-explaining basics.
```

New function `_load_user_profile()` in `co_cli/tools/personality.py`:
- Reads `.co-cli/personality/user-profile.md`
- Returns `## User Context` block (frontmatter fields first, body follows)
- Returns empty string if file doesn't exist

New `add_user_context` @agent.system_prompt in `agent.py`:
- Calls `_load_user_profile()` when `ctx.deps.personality` is set

Signal detection save path:
- When signal is `preference`, `expertise`, or `communication_style`, target the user
  profile rather than general memory
- Signal analyzer returns `target: user_profile` in high-confidence signals
- Auto-save path calls `update_user_profile(field, value)` instead of `save_memory()`

---

## Fix 7 — Silent Failure for Missing Strategy Files

**Why:** Adding a new role with incomplete strategy files fails silently — the model
gets a personality missing some task-type guidance, with no warning.

**Design:**

`validate_personality_files(role)` in `_composer.py`:
- Checks seed exists at `souls/{role}/seed.md`
- Checks all 6 strategy files exist at `strategies/{role}/{task_type}.md`
- Returns list of warning strings (empty = all OK)

Called at config load time via `_validate_personality()` in `config.py`.

Warnings surface at session start. Missing strategy files degrade gracefully (tool skips
them silently) rather than blocking startup — but the user sees what's missing.

Files changed: `co_cli/prompts/personalities/_composer.py`, `co_cli/config.py`

---

## Fix 8 — Personality-Context Memory Tagging

**Dependency:** Fix 1 (user model) supersedes this once implemented. This is the
interim bridge until user profiles exist.

**Why:** No declared boundary between user-model signals and general-context signals.
A memory tagged `personality-context` is the only way the personality system learns
about the user, but the tagging discipline is implicit.

**Design:**

Add explicit classification to `co_cli/prompts/agents/signal_analyzer.md`:

Memory target classification:
- **user-profile** signals: domain expertise, explanation preference, toolchain preferences,
  explicit corrections about AI speaking style
- **personality-context** signals: emotional moments that changed the relationship dynamic,
  user reactions that shaped tone, explicit personality behavior preferences
- **general**: technical facts, decisions, project context, task history

Until Fix 1 ships, `user-profile` signals fall back to `personality-context` tagged
general memories.

Files changed: `co_cli/prompts/agents/signal_analyzer.md` (doc only)
