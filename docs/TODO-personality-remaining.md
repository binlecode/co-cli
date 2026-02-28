# TODO: Personality System — Remaining Work

Three phases. Fix 2+3 shipped as heavier seed + `load_task_strategy` tool (see `DESIGN-02-personality.md`).

| Phase | Feature | Why | Dependency | Size |
|-------|---------|-----|-----------|------|
| 1 | Character demonstrations | Rules generalize poorly; examples generalize well | None | Small — 3 files |
| 2 | Signal classification | Distinguish user-model signals from general context | None | Trivial — 2 files |
| 3 | User model | Character has nothing to adapt to — role plays into the void | Phase 2 | Medium — 3 files |

Phases 1 and 2 are independent. Phase 3 requires Phase 2 (signal routing must be defined before the user profile save path is wired). Ship 1 and 2 together, then 3.

**Cross-phase content constraint:** examples.md content (Phase 1) must avoid hardcoding expertise assumptions. "Before we proceed, here are the three things to verify..." works for any expertise level. Expertise-specific examples are a Phase 3 follow-on once the user model provides that context.

---

## Phase 1 — Character Demonstrations

**Why:** The personality system is injection-based — rules tell the model *what to do or not do*
but do not show *how the character reasons*. In novel situations not covered by any rule, the model
falls back to generic assistant behavior because it has no reasoning pattern to generalize from.

Rules generalize poorly; examples generalize well. "Never offer warmth as substitute for substance"
constrains negatively — the model knows what *not* to do but must infer the positive shape.
An example pair — anxiety input → checklist response — shows the shape directly. The model
generalizes the reasoning pattern to inputs that look nothing like the example.

**Design:**

`souls/{role}/examples.md` — sibling of `seed.md`. Loaded once at agent creation, appended
after the seed in the static system prompt.

Static delivery (not tool-loaded): the key failure mode is improvisation in novel situations,
which occurs before any tool call. Tool-loaded examples would be absent exactly when most needed.

Format: contrast pairs — `"[right fragment]" — not "[wrong fragment]"`. 3–4 pairs per role,
~80–100 chars each. Target budget ~300–500 chars per file.

**Content — `souls/finch/examples.md`:**

```
## Response patterns

Anxiety or stress → lead with preparation, not acknowledgment
"Before we proceed, here are the three things that need to be verified..." — not "I understand this is stressful"

Pushback on a warning → commit, don't soften
"The risk is real. You can proceed, but you need to understand the failure mode first." — not "you're probably right, it might be fine"

Conceptual question → bridge to lived experience, not just mechanics
"You can understand the architecture, but here's what it actually feels like when this fails under load..." — not stopping at the diagram

Novel situation without a clear rule → reason from the principle
"This comes down to [core principle]: ..." — not hedging with "it depends on your situation"
```

**Content — `souls/jeff/examples.md`:**

```
## Response patterns

Finding information → frame as discovery, not verdict
"From what I found..." — not "The answer is..."

Genuine uncertainty → share it, work through it together
"I'm not sure about this one — let me think through it with you..." — not projecting confidence you don't have

Unexpected finding → flag it with authentic curiosity
"This is interesting — I didn't expect to find..." — not reporting it neutrally like a lookup

Something going wrong → stay hopeful, don't catastrophize
"We can figure this out — let's start by understanding what actually happened..." — not "this is a serious problem"
```

**Budget impact:**

| Component | Before | After |
|-----------|--------|-------|
| Soul seed | ~400–600 chars | unchanged |
| Soul examples | 0 | ~300–500 chars |
| 5 rules | ~4,800 chars | unchanged |
| **Static prompt total** | **~5,300–6,900** | **~5,600–7,400** |

**Files:**

1. Create `co_cli/prompts/personalities/souls/finch/examples.md`
2. Create `co_cli/prompts/personalities/souls/jeff/examples.md`
3. `co_cli/prompts/personalities/_composer.py` — extend `load_soul_seed()`: after reading
   `seed.md`, silently try reading `souls/{role}/examples.md`; if present, append its content
   to the seed string before returning. No changes to `assemble_prompt()` or `agent.py`.

**Verification:**

Existing `tests/test_prompt_assembly.py` budget tests catch overruns automatically once the
files exist — no new tests needed.

Primary behavioral signal is `finch-no-reassurance`. Measure pass rate with and without
`examples.md` to quantify the demonstration effect:

```
uv run python evals/eval_personality_behavior.py --case-id finch-no-reassurance --runs 3
```

---

## Phase 2 — Signal Classification

**Why:** No declared boundary between user-model signals and general-context signals. The signal
analyzer saves everything that isn't clearly technical as `personality-context`, but that tag
conflates two distinct things: signals about *how co should behave* (personality-context) and
signals about *who the user is* (user-profile). Without the distinction, Phase 3's save routing
has nothing to key off.

This is the bridge that Phase 3 depends on. Once the tag is declared and the prompt updated,
user-profile signals are saved correctly immediately — they sit uncollected until Phase 3 adds
injection, but no fallback or remapping is needed.

**Design:**

Add explicit target classification to `co_cli/prompts/agents/signal_analyzer.md`:

Memory target classification:
- **user-profile** — domain expertise, explanation preference, toolchain preferences,
  explicit corrections about co's speaking style
- **personality-context** — emotional moments that changed the relationship dynamic,
  user reactions that shaped tone, explicit personality behavior preferences
- **general** — technical facts, decisions, project context, task history

**Files:**

1. `co_cli/prompts/agents/signal_analyzer.md` — add memory target classification section.
2. `co_cli/_signal_analyzer.py` — extend `SignalResult.tag` to
   `Literal["correction", "preference", "user-profile"]`.

Prompt change + one-line Pydantic model change. With the tag extended, `main.py`'s existing
routing (`_save_memory_impl(deps, signal.candidate, [signal.tag], None)`) passes `"user-profile"`
through automatically — no routing changes needed.

**Verification:**

`evals/eval_signal_detector_approval.py` covers signal detection behavior. No new test needed.

---

## Phase 3 — User Model

**Why:** Co models the AI (soul seed, strategies, examples) but not the user. Personality adapts
its style but has nothing to adapt *to* — the role plays into the void. Letta/MemGPT maintains
a `human` block alongside `persona`. Character.AI builds a persistent user profile that shapes
every turn. Without a user model, personality-context memories are the only adaptation mechanism
and they are unstructured — no distinction between user expertise and user tone preferences.

**Depends on Phase 2.** Signal routing (user-profile vs. personality-context vs. general) must
be defined before the save path is wired up.

**Design:**

User-profile signals are memories tagged `user-profile` — same storage as all other memories,
different tag. `_load_user_profile()` scans for that tag and injects the results as
`## User Context`, the same way `_load_personality_memories()` works for `personality-context`.
No new file, no new format. A user with no accumulated signals has no `## User Context` block —
that is valid and expected for new sessions.

Updated per-turn prompt layer order:
```
add_current_date
add_shell_guidance
add_project_instructions
add_user_context          ← new: ## User Context (expertise, domain, style + observations)
add_personality_memories  ← existing: ## Learned Context (personality-context memories)
```

Signal routing: Phase 2's tag extension makes this automatic. When the signal analyzer emits
`tag: "user-profile"`, `main.py`'s existing auto-save path calls `_save_memory_impl()` directly
with `[signal.tag]` — no changes to `main.py` needed. No new tool needed.

**Files:**

1. `co_cli/tools/personality.py` — add `_load_user_profile() -> str`: scans
   `.co-cli/knowledge/memories/` for `user-profile` tagged memories (same mechanism as
   `_load_personality_memories()`), formats as `## User Context` block, returns empty string
   if none found.
2. `co_cli/agent.py` — add `add_user_context` `@agent.system_prompt`: calls
   `_load_user_profile()` when `ctx.deps.personality` is set. Register before
   `add_personality_memories`.
3. `docs/DESIGN-02-personality.md` — update prompt layer map, per-turn injection table, budget
   table, and file list to reflect `## User Context` layer.

**Verification:**

1. `uv run pytest tests/test_prompt_assembly.py` — budget and structure tests.
2. Manual: start session with a populated `user-profile.md`, inspect trace to verify `## User
   Context` appears in the system prompt above `## Learned Context`.
3. Signal routing end-to-end: trigger a user-profile signal ("I prefer detailed explanations"),
   confirm it saves a memory tagged `user-profile` and not `personality-context`.
4. Absence handling: with no `user-profile` tagged memories, verify `## User Context` block
   is absent from the prompt and no errors occur.
