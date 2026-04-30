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
`co_cli/personality/prompts/souls/{role}/`.

Personality enters the agent via one path:

1. **Static** — `build_static_instructions()` assembles the full static prompt at agent
   construction, including personality-context knowledge artifacts. This is set once as
   `Agent(instructions=...)` and does not change within a session.

Personality-context memories (knowledge artifacts tagged `personality-context`) are loaded
once at agent construction by `load_personality_memories()` in `personality/prompts/loader.py`
and injected into the static system prompt. This keeps the cache-stable prefix intact across
all turns. Runtime edits to personality-context artifacts require a session restart to take effect.

```
Session start
    ↓
build_static_instructions(config)
    ↓
    [1] soul seed               — identity anchor, constraints, never-list
    [2] mindsets                — 6 task-type behavioral guides
    [3] personality memories    — top-5 "personality-context" artifacts from ~/.co-cli/knowledge/
    [4] behavioral rules        — 5 universal rule files (01–05)
    [5] review lens             — self-assessment frame
    → set as Agent.instructions (static, once per session)

Character memories (souls/{role}/memories/*.md) are NOT injected here.
They are surfaced on demand via the canon channel in `memory_search`.
```

---

## 2. Core Logic

### Asset Taxonomy: Canon vs Distillation

Soul assets fall on a spectrum from canon (source-material truth) to author distillation
(interpretive guidance derived from canon). All five asset types are system-owned, read-only,
and package-shipped, but they differ in *what they are* and *how they should be injected*.

| Asset | Relation to canon | Nature |
|---|---|---|
| Memories (`memories/*.md`) | **Canon** — directly source-grounded scenes, observations, dialogue | Observational ("character did X in scene Y") |
| Mindsets (`mindsets/{task_type}.md`) | **Distillation of canon** — interpretation abstracted into task-typed prescriptions | Prescriptive ("when coding, be terse and load-bearing") |
| Seed (`seed.md`) | **Synthesis of canon** — distilled identity declaration | Declarative ("you are X, you do Y, never Z") |
| Examples (`examples.md`) | **Pattern extraction from canon** — how the character speaks/responds | Few-shot |
| Critique (`critique.md`) | **Interpretive lens** — evaluative frame, often more authorial than source | Reflective |

**Why the distinction matters for retrieval:** distilled assets (mindsets, seed, examples,
critique) prime behavior on every turn — they belong in static priming. Canon (memories) is
discrete by nature: a scene either matches the moment or doesn't. Static injection of canon
pays full token cost whether it lands or not, while leaving the model to extrapolate from
unmatched scenes. Canon is therefore better served by on-demand recall — searched when the
moment invokes it — while distilled assets stay always-on. See `memory.md` for the canon
recall channel design.

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
section_2 = load_soul_mindsets(role)            # Optional — ## Mindsets block, all 6 files
section_3 = load_personality_memories()         # top-5 personality-context artifacts
section_4 = _collect_rule_files()               # Rules from context/rules/NN_rule_id.md (01–05)
section_5 = load_soul_critique(role)            # Optional — ## Review lens, placed last

return "\n\n".join(non_empty_sections)
```

Character memories (`memories/*.md`) are NOT included here — they are served on demand via
the canon channel in `memory_search` when the user or model queries about the character's
background, scenes, or source material.

**Placement rationale:** Soul seed is first because early context has the strongest influence
on the model's operating space. Review lens is last so it frames all prior content as
subject to self-review.

**Rule files** (`context/rules/`) are personality-independent universal policies. Files must
be numbered `01`–`05`, contiguous, and unique. Current rules: `01_identity.md`,
`02_safety.md`, `03_reasoning.md`, `04_tool_protocol.md`, `05_workflow.md`.

### Static Personality-Context Injection

`load_personality_memories()` in `personality/prompts/loader.py`:

```
if _personality_cache is not None:
    return _personality_cache                      # cached — no disk scan
memories = load_knowledge_artifacts(KNOWLEDGE_DIR, tags=["personality-context"])
sorted by recency (updated_at or created_at)
take top 5
_personality_cache = "## Learned Context\n\n" + bullet list of bodies
return _personality_cache
```

The cache is process-scoped (module-level `_personality_cache`). The function is called
once at agent construction inside `build_static_instructions()`; result is injected into
the static system prompt. The memory extraction pipeline (in [memory.md](memory.md))
is responsible for tagging relevant observations as `personality-context`.

### Personality Discovery and Validation

`_discover_valid_personalities()` scans `souls/` for subdirectories containing `seed.md`.
`VALID_PERSONALITIES` is the discovered list. Config validation rejects unknown names.

`validate_personality_files(role)` checks for the 6 required mindset files and returns
non-blocking warning strings. Startup prints any warnings but does not abort.

---

## 3. Config

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `personality` | `CO_PERSONALITY` | `tars` | Active personality role; must be in `VALID_PERSONALITIES` (auto-discovered from `souls/`) |

Personality is validated at config load time (`_validate_personality_name` field validator)
and again at startup via `validate_personality_files()` which issues non-blocking warnings
for missing mindset files.

---

## 4. Files

| File | Purpose |
|---|---|
| `co_cli/context/assembly.py` | `build_static_instructions()` — static prompt assembly (soul + personality memories + rules) |
| `co_cli/personality/prompts/loader.py` | `load_soul_seed`, `load_soul_critique`, `load_soul_mindsets`, `load_personality_memories` |
| `co_cli/personality/prompts/validator.py` | `_discover_valid_personalities()`, `validate_personality_files()`, `VALID_PERSONALITIES` |
| `co_cli/personality/prompts/souls/` | Soul file trees: `finch/`, `jeff/`, `tars/` |
| `co_cli/context/rules/` | Universal behavioral rule files `01_identity.md` – `05_workflow.md` |
| `co_cli/personality/_profiles/` | Human-readable character narrative docs (`finch.md`, `jeff.md`, `tars.md`) — not loaded into agent |
| `co_cli/config/core.py` | `personality` config field, `_validate_personality_name()`, startup validation call |
| `co_cli/agent/core.py` | `build_agent()` — calls `build_static_instructions()` and registers instruction callbacks |
| `co_cli/agent/_instructions.py` | `date_prompt()` — dynamic instruction returning today's date |
