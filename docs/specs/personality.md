# Personality System

## Product Intent

**Goal:** Define how co-cli constructs and maintains a consistent, configurable character across sessions.
**Functional areas:**
- Soul file format and directory layout (`souls/{role}/`)
- Static prompt assembly — six-section ordered construction
- Per-turn personality-context memory injection
- Personality discovery, validation, and configuration

**Non-goals:**
- Model fine-tuning or weight modification (all personality is external, prompt-space only)
- Automatic personality selection based on context
- Cross-personality memory sharing

**Success criteria:** Personality is fully reconstructed from files each session; soul seed is placed first in static instructions; per-turn personality-context memories inject top-5 recent entries.
**Status:** Stable

---

## 1. What & How

The personality system defines co's character as a set of external markdown files assembled
into the agent's static instructions at session start. No personality state is baked into
model weights — everything is file-based, inspectable, and swappable via config.

Three personalities ship: `finch` (preparation-first mentor), `jeff` (warm collaborator),
`tars` (direct operator — default). Each personality lives in its own subdirectory under
`co_cli/prompts/personalities/souls/{role}/`.

Personality enters the agent via two paths:

1. **Static** — `build_static_instructions()` assembles a six-section prompt at agent
   construction. This is set once as `Agent(instructions=...)` and does not change
   within a session.

2. **Per-turn** — `add_personality_memories()` is registered as an `@agent.instructions`
   callback. It fires fresh on every turn, injecting the top-5 most recent memories tagged
   `personality-context` from `~/.co-cli/knowledge/`. This lets learned context about the user
   accumulate over sessions and shape personality expression without modifying the soul files.

```
Session start
    ↓
build_static_instructions(config)
    ↓
    [1] soul seed          — identity anchor, constraints, never-list
    [2] character memories — planted narrative backstory
    [3] mindsets           — 6 task-type behavioral guides
    [4] behavioral rules   — 5 universal rule files (01–05)
    [5] soul examples      — concrete trigger→response patterns
    [6] review lens        — self-assessment frame
    → set as Agent.instructions (static, once per session)

Each turn
    ↓
add_personality_memories()   — @agent.instructions callback
    → loads top-5 "personality-context" memories from ~/.co-cli/knowledge/
    → injected as ## Learned Context block
```

---

## 2. Core Logic

### Soul File Layout

Each personality in `souls/{role}/` contains:

```
souls/{role}/
  seed.md          # required — identity anchor
  examples.md      # optional — trigger→response patterns
  critique.md      # optional — self-assessment lens
  memories/        # optional — *.md narrative backstory files
  mindsets/        # strongly expected — task-type behavior files:
    technical.md
    exploration.md
    debugging.md
    teaching.md
    emotional.md
    memory.md
```

All files use YAML frontmatter + markdown body. Character memory files support frontmatter
parsed by `parse_frontmatter()`. `_profiles/{role}.md` files document character narrative
for human reference — they are not loaded into the agent.

### Static Prompt Assembly

`build_static_instructions(config)` in `_assembly.py`:

```
section_1 = load_soul_seed(role)               # Required — placed first; identity anchor
section_2 = load_character_memories(role)       # Optional — ## Character block, memories/*.md
section_3 = load_soul_mindsets(role)            # Optional — ## Mindsets block, all 6 files
section_4 = _collect_rule_files()               # Rules from prompts/rules/NN_rule_id.md (01–05)
section_5 = load_soul_examples(role)            # Optional — trigger→response patterns
section_6 = load_soul_critique(role)            # Optional — ## Review lens, placed last

return "\n\n".join(non_empty_sections)
```

**Placement rationale:** Soul seed is first because early context has the strongest influence
on the model's operating space. Review lens is last so it frames all prior content as
subject to self-review.

**Rule files** (`prompts/rules/`) are personality-independent universal policies. Files must
be numbered `01`–`05`, contiguous, and unique. Current rules: `01_identity.md`,
`02_safety.md`, `03_reasoning.md`, `04_tool_protocol.md`, `05_workflow.md`.

### Per-Turn Personality-Context Injection

`_load_personality_memories()` in `personalities/_injector.py`:

```
memories = load_knowledge_artifacts(knowledge_dir, tags=["personality-context"])
sorted by recency (updated_at or created_at)
take top 5
return "## Learned Context\n\n" + bullet list of bodies
```

This layer allows user-specific working-style observations to accumulate across sessions
without editing soul files. The memory extraction pipeline (in context.md) is responsible
for tagging relevant observations as `personality-context`.

### Personality Discovery and Validation

`_discover_valid_personalities()` scans `souls/` for subdirectories containing `seed.md`.
`VALID_PERSONALITIES` is the discovered list. Config validation rejects unknown names.

`validate_personality_files(role)` checks for the 6 required mindset files and returns
non-blocking warning strings. Startup prints any warnings but does not abort.

---

## 3. Config

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `personality` | `CO_CLI_PERSONALITY` | `tars` | Active personality role; must be in `VALID_PERSONALITIES` (auto-discovered from `souls/`) |

Personality is validated at config load time (`_validate_personality_name` field validator)
and again at startup via `validate_personality_files()` which issues non-blocking warnings
for missing mindset files.

---

## 4. Files

| File | Purpose |
|---|---|
| `co_cli/prompts/_assembly.py` | `build_static_instructions()` — six-section static prompt assembly |
| `co_cli/prompts/personalities/_loader.py` | `load_soul_seed`, `load_soul_examples`, `load_soul_critique`, `load_character_memories`, `load_soul_mindsets` |
| `co_cli/prompts/personalities/_injector.py` | `_load_personality_memories()` — per-turn personality-context injection |
| `co_cli/prompts/personalities/_validator.py` | `_discover_valid_personalities()`, `validate_personality_files()`, `VALID_PERSONALITIES` |
| `co_cli/prompts/personalities/souls/` | Soul file trees: `finch/`, `jeff/`, `tars/` |
| `co_cli/prompts/rules/` | Universal behavioral rule files `01_identity.md` – `05_workflow.md` |
| `co_cli/_profiles/` | Human-readable character narrative docs (`finch.md`, `jeff.md`, `tars.md`) — not loaded into agent |
| `co_cli/config/_core.py` | `personality` config field, `_validate_personality_name()`, startup validation call |
| `co_cli/agent/_core.py` | `build_agent()` — calls `build_static_instructions()` and registers instruction callbacks |
| `co_cli/agent/_instructions.py` | `add_personality_memories()` dynamic instruction callback |
