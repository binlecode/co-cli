# Personality System


## 1. What & How

The personality system defines co's character as a set of external markdown files assembled
into the agent's static instructions at session start. No personality state is baked into
model weights — everything is file-based, inspectable, and swappable via config.

Three personalities ship: `finch` (preparation-first mentor), `jeff` (warm collaborator),
`tars` (direct operator — default). Each personality lives in its own subdirectory under
`co_cli/personality/prompts/souls/{role}/`.

Personality enters the agent via the static prompt — set once at construction and immutable for the session. The model has no tool path to query personality content; canon is doctrine, not memory.

```
Session start
    ↓
build_orchestrator(ORCHESTRATOR_SPEC, deps)
    ↓
    iterates ORCHESTRATOR_SPEC.static_instruction_builders in order:
    [1] _static_instructions_provider   → soul seed, mindsets, behavioral rules, recency advisory
    [2] _toolset_guidance_provider      — tool-specific guidance (conditional on tool presence)
    [3] _category_awareness_provider    — deferred tool category hint (conditional)
    [4] _skill_manifest_provider        — <available_skills> for bundled skills (conditional)
    [5] _personality_critique_provider  — ## Review lens, last (conditional on personality + critique file)
    → joined and set as Agent.instructions (static, once per session)

Character canon (souls/{role}/memories/*.md) is indexed at bootstrap by `_sync_canon_store()`
for personality-system consumption only — it is never returned by any model-callable tool.
See §2 "Canon doctrine" below.
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

**Why the distinction matters:** distilled assets (mindsets, seed, examples, critique) prime
behavior on every turn — they belong in static priming. Canon (memories) is discrete by
nature: a scene either matches the moment or doesn't. Canon is therefore not statically
injected; it is loaded into the personality system's FTS index at bootstrap for non-model-
callable consumption (auto-injection mechanics live in this spec). The model never queries
canon — it surfaces only through the personality system. See §2 "Canon doctrine" below.

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

`build_static_instructions(config)` in `assembly.py` owns the stable-forever sections only:

```
section_1 = load_soul_seed(role)               # Required — placed first; identity anchor
section_2 = load_soul_mindsets(role)            # Optional — ## Mindsets block, all 6 files
section_3 = _collect_rule_files()               # Rules from context/rules/NN_rule_id.md (01–06)

return "\n\n".join(non_empty_sections)
```

`build_orchestrator()` then iterates `ORCHESTRATOR_SPEC.static_instruction_builders`, calling each closure with `deps`:

```
parts = []
for builder in ORCHESTRATOR_SPEC.static_instruction_builders:
    piece = builder(deps)                            # _static_instructions_provider,
                                                     # _toolset_guidance_provider,
                                                     # _category_awareness_provider,
                                                     # _skill_manifest_provider,
                                                     # _personality_critique_provider
    if piece:
        parts.append(piece)
static_instructions = "\n\n".join(parts)
```

Character canon (`memories/*.md`) is NOT included in the static prompt. It is indexed at
bootstrap into the shared FTS index under `source='canon'` for personality-system use
only — there is no model-callable read path. See §2.5 below.

**Placement rationale:** Soul seed is first because early context has the strongest influence
on the model's operating space. Review lens is last so it frames all operational guidance
as subject to self-review.

**Rule files** (`context/rules/`) are personality-independent universal policies. Files must
be numbered `01`–`06`, contiguous, and unique. Current rules: `01_identity.md`,
`02_safety.md`, `03_reasoning.md`, `04_tool_protocol.md`, `05_workflow.md`, `06_skill_protocol.md`.

### Personality Discovery and Validation

`_discover_valid_personalities()` scans `souls/` for subdirectories containing `seed.md`.
`VALID_PERSONALITIES` is the discovered list. Config validation rejects unknown names.

`validate_personality_files(role)` checks for the 6 required mindset files and returns
non-blocking warning strings. Startup prints any warnings but does not abort.

### 2.5 Canon doctrine

Canon scenes (`souls/{role}/memories/*.md`) are the source-material grounding for the active
character. They are read-only at runtime, package-shipped, and **not** part of the memory
surface — there is no canon search tool and no `canon_manage` tool. Canon is
identity; treating it as mutable would compromise the personality contract.

**Indexing.** At bootstrap, `_sync_canon_store(store, config, frontend)` calls
`MemoryStore.sync_dir(source='canon', directory=canon_dir, glob='*.md', no_chunk=True)` so
each scene becomes a single chunk. The `'canon'` source coexists with `'knowledge'` and
`'session'` in `chunks_fts` but is owned exclusively by the personality system. No
model-callable tool returns canon hits.

**Sourcing.** `canon_dir = souls_dir / config.personality / "memories"`. If `config.personality`
is unset, no canon is indexed.

**Body access.** Internal personality code reads canon bodies via
`MemoryStore.get_chunk_content('canon', path, 0)`. Scenes are small (<1KB) and returned
whole — chunking would fragment them.

**Removal from memory surface.** The previous design surfaced canon as `kind='canon'` in
the artifacts channel of the unified recall tool. That was the wrong tier — canon is doctrine, not
accumulated state. The canon priority pass was removed; canon is not queryable via any
model-callable tool — it is auto-injected by the personality system at agent construction.

---

## 3. Config

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `personality` | `CO_PERSONALITY` | `tars` | Active personality role; must be in `VALID_PERSONALITIES` (auto-discovered from `souls/`) |

Personality is validated at config load time (`_validate_personality_name` field validator)
and again at startup via `validate_personality_files()` which issues non-blocking warnings
for missing mindset files.

---

## 4. Public Interface

### Static prompt assembly

| Symbol | Source | Contract |
|---|---|---|
| `build_static_instructions(config) -> str` | `co_cli/context/assembly.py` | Returns soul seed + mindsets + numbered rules joined with `\n\n`; called once per session at agent construction |

### Soul asset loaders

| Symbol | Source | Contract |
|---|---|---|
| `load_soul_seed(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns the role's `seed.md` body; required for every personality |
| `load_soul_mindsets(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns joined `## Mindsets` block from `mindsets/*.md`; empty string when no mindsets |
| `load_soul_critique(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns optional `## Review lens` body; empty string when no `critique.md` |

### Personality discovery and validation

| Symbol | Source | Contract |
|---|---|---|
| `VALID_PERSONALITIES` | `co_cli/personality/prompts/validator.py` | Tuple of personality role names discovered from `souls/` (subdirs containing `seed.md`) |
| `validate_personality_files(role) -> list[str]` | `co_cli/personality/prompts/validator.py` | Returns non-blocking warning strings for missing mindset files |

### Canon indexing (bootstrap-only path)

| Symbol | Source | Contract |
|---|---|---|
| `_sync_canon_store(store, config, frontend)` | `co_cli/bootstrap/core.py` | Indexes `souls/{role}/memories/*.md` into `chunks_fts` under `source='canon'`; package-private — no model-callable read path |

---

## 5. Files

| File | Purpose |
|---|---|
| `co_cli/context/assembly.py` | `build_static_instructions()` — static prompt assembly (soul + mindsets + rules + recency advisory) |
| `co_cli/context/manifests/skill_manifest.py` | `render_skill_manifest()` — bundled skill `<available_skills>` block appended after tool guidance |
| `co_cli/personality/prompts/loader.py` | `load_soul_seed`, `load_soul_critique`, `load_soul_mindsets` |
| `co_cli/personality/prompts/souls/{role}/memories/*.md` | Canon scene files (package-shipped) |
| `co_cli/bootstrap/core.py:_sync_canon_store` | Bootstrap hook indexing canon under `source='canon'` (personality-load-only path) |
| `co_cli/memory/memory_store.py:sync_dir(no_chunk=True)` | Single-chunk-per-file indexing path used by canon |
| `co_cli/personality/prompts/validator.py` | `_discover_valid_personalities()`, `validate_personality_files()`, `VALID_PERSONALITIES` |
| `co_cli/personality/prompts/souls/` | Soul file trees: `finch/`, `jeff/`, `tars/` |
| `co_cli/context/rules/` | Universal behavioral rule files `01_identity.md` – `06_skill_protocol.md` |
| `co_cli/personality/_profiles/` | Human-readable character narrative docs (`finch.md`, `jeff.md`, `tars.md`) — not loaded into agent |
| `co_cli/config/core.py` | `personality` config field, `_validate_personality_name()`, startup validation call |
| `co_cli/agent/build.py` | `build_orchestrator()` — iterates `ORCHESTRATOR_SPEC.static_instruction_builders` and registers per-turn instruction callbacks |
| `co_cli/agent/orchestrator.py` | `ORCHESTRATOR_SPEC` and its 5 static-instruction builder closures |
| `co_cli/agent/_instructions.py` | `current_time_prompt()` — dynamic instruction returning current date/time; `safety_prompt()` — doom-loop and shell-error warnings |
