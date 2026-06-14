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
    [1] _base_instructions_provider   → soul seed, mindsets, behavioral rules, recency advisory
    [2] _toolset_guidance_provider      — tool-specific guidance (conditional on tool presence)
    [3] _personality_critique_provider  — ## Review lens, last (conditional on personality + critique file)
    → joined and set as Agent.instructions (static, once per session)

    then registers ORCHESTRATOR_SPEC.per_turn_instructions via agent.instructions():
    [safety_prompt, current_time_prompt, deferred_tool_awareness_prompt, skill_manifest_prompt]
    → each emitted as InstructionPart(dynamic=True), evaluated fresh per request

Character canon (souls/{role}/canon/*.md) is indexed at bootstrap by `_sync_canon_store()`
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
| Canon scenes (`canon/*.md`) | **Canon** — directly source-grounded scenes, observations, dialogue | Observational ("character did X in scene Y") |
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
  curation.md      # optional — retention lens for the dream daemon (not the orchestrator)
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

`curation.md` is a third injection path, distinct from both static priming and canon: it is
not part of the orchestrator's static prompt and never reaches an interactive turn. The dream
daemon's domain reviewers append it to their review instructions (`load_soul_curation`), so the
active character's retention judgment — what counts as durable signal, how aggressively to
merge — scopes memory and skill curation. It is deliberately voice-free: the dreamer has no
audience, so the lens carries threshold and disposition, not tone. Absent file or disabled
personality falls back to the bare review prompt.

### Static Prompt Assembly

`build_base_instructions(config)` in `assembly.py` owns the stable-forever sections only:

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
    piece = builder(deps)                            # _base_instructions_provider,
                                                     # _toolset_guidance_provider,
                                                     # _personality_critique_provider
    if piece:
        parts.append(piece)
static_instructions = "\n\n".join(parts)
```

The `<available_skills>` manifest and deferred-tool awareness are NOT in this static
block; they are emitted by per-turn `agent.instructions()` callbacks
(`skill_manifest_prompt`, `deferred_tool_awareness_prompt`) so that `skill_catalog` /
`tool_catalog` mutations do not invalidate the cached prefix. See [prompt-assembly.md](prompt-assembly.md) §2.2–2.3.

Character canon (`canon/*.md`) is NOT included in the static prompt. It is indexed at
bootstrap into the shared FTS index under `source='canon'` for personality-system use
only — there is no model-callable read path. See §2.5 below.

The curation lens (`curation.md`) is NOT included in the static prompt either. It is loaded
only by the dream daemon's reviewers (`load_soul_curation`), never by `build_orchestrator`,
so it shapes background curation but never the interactive agent's behavior. See the Soul
File Layout note above.

**Placement rationale:** Soul seed is first because early context has the strongest influence
on the model's operating space. Review lens is last so it frames all operational guidance
as subject to self-review.

**Rule files** (`context/rules/`) are personality-independent universal policies. Files must
be numbered contiguously starting at `01` and unique. Current rules: `01_identity.md`,
`02_safety.md`, `03_reasoning.md`, `04_tool_protocol.md`, `05_workflow.md`, `06_skill_protocol.md`,
`07_memory_protocol.md`.

### Personality Discovery and Validation

`_discover_valid_personalities()` scans `souls/` for subdirectories containing `seed.md`.
`VALID_PERSONALITIES` is the discovered list. Config validation rejects unknown names.

`validate_personality_files(role)` checks for the 6 required mindset files and returns
non-blocking warning strings. Startup prints any warnings but does not abort.

**Disabled mode.** `personality` is `str | None`; `None` disables the personality layer.
Set it via JSON `null` in `settings.json` or `CO_PERSONALITY=none` (the env-transport spelling
of `None`, normalized by the field validator). When disabled, `build_base_instructions` skips
the seed and mindsets, the critique provider returns nothing, and canon is not synced —
yielding a rules-only neutral agent. The behavioral rules (`context/rules/`, including
`02_safety` and the tool protocol) load unconditionally, so disabling personality never
disables guardrails. Like role selection, this is session-static: resolved once at config load,
applied on restart — there is no runtime switch.

### 2.5 Canon doctrine

Canon scenes (`souls/{role}/canon/*.md`) are the source-material grounding for the active
character. They are read-only at runtime, package-shipped, and **not** part of the memory
surface — there is no canon search tool and no `canon_manage` tool. Canon is
identity; treating it as mutable would compromise the personality contract.

**Indexing.** At bootstrap, `_sync_canon_store(index_store, config, on_status)`
(`bootstrap/core.py`) delegates to `_sync_canon_dir`, which globs `canon/*.md` and upserts each
file as a **single chunk** (`index=0`) under `source='canon'`, `kind='canon'` via
`index_store.transaction()`, skipping unchanged files with a content-hash `needs_reindex` guard.
Canon has its own bespoke indexer — it does **not** go through `MemoryStore.sync_dir`. The
`'canon'` source coexists with `'knowledge'` and `'session'` in `chunks_fts` but is owned
exclusively by the personality system. No model-callable tool returns canon hits.

**Sourcing.** `canon_dir = souls_dir / config.personality / "canon"`. If `config.personality`
is unset or disabled, no canon is indexed.

**No model surface (by design).** Canon is system-owned doctrine, not an agent-facing tier: the
memory write tool rejects `kind='canon'` (`tools/memory/manage.py`) and agent recall searches only
the memory source (`memory/store.py`), so the model can neither write nor read canon. A previous
design surfaced canon as `kind='canon'` in the unified recall tool's artifacts channel; that was
removed as the wrong tier (canon is doctrine, not accumulated state). The model never queries
canon — per §1 it is **not statically injected** but surfaced by the personality system on
relevance ("a scene either matches the moment or doesn't").

**Implementation status.** Built: the bootstrap indexer (`_sync_canon_dir`). Not yet built: the
personality-system search + inject step — no code queries `source='canon'` and
`IndexStore.get_chunk_content` (the read primitive, `index/store.py`) has no caller. So canon is
currently indexed but not yet surfaced; the relevance-selected injection the design calls for is
pending. Scenes are each <1KB and indexed whole, so the future injector can return complete scenes.

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
| `build_base_instructions(config) -> str` | `co_cli/context/assembly.py` | Returns soul seed + mindsets + numbered rules joined with `\n\n`; called once per session at agent construction |

### Soul asset loaders

| Symbol | Source | Contract |
|---|---|---|
| `load_soul_seed(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns the role's `seed.md` body; required for every personality |
| `load_soul_mindsets(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns joined `## Mindsets` block from `mindsets/*.md`; empty string when no mindsets |
| `load_soul_critique(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns optional `## Review lens` body; empty string when no `critique.md` |
| `load_soul_curation(role) -> str` | `co_cli/personality/prompts/loader.py` | Returns optional `## Curation Lens` body for the dream daemon; empty string when no `curation.md` |

### Personality discovery and validation

| Symbol | Source | Contract |
|---|---|---|
| `VALID_PERSONALITIES` | `co_cli/personality/prompts/validator.py` | Tuple of personality role names discovered from `souls/` (subdirs containing `seed.md`) |
| `validate_personality_files(role) -> list[str]` | `co_cli/personality/prompts/validator.py` | Returns non-blocking warning strings for missing mindset files |

### Canon indexing (bootstrap-only path)

| Symbol | Source | Contract |
|---|---|---|
| `_sync_canon_store(index_store, config, on_status)` | `co_cli/bootstrap/core.py` | Indexes `souls/{role}/canon/*.md` into `chunks_fts` under `source='canon'`; package-private — no model-callable read path |

---

## 5. Files

| File | Purpose |
|---|---|
| `co_cli/context/assembly.py` | `build_base_instructions()` — static prompt assembly (soul + mindsets + rules + recency advisory) |
| `co_cli/context/manifests/skill_manifest.py` | `render_skill_manifest()` — `<available_skills>` block; emitted per-turn via `skill_manifest_prompt` |
| `co_cli/personality/prompts/loader.py` | `load_soul_seed`, `load_soul_critique`, `load_soul_mindsets` |
| `co_cli/personality/prompts/souls/{role}/canon/*.md` | Canon scene files (package-shipped) |
| `co_cli/bootstrap/core.py:_sync_canon_store` | Bootstrap hook indexing canon under `source='canon'` (personality-load-only path) |
| `co_cli/bootstrap/core.py:_sync_canon_dir` | Single-chunk-per-file canon indexing helper (one `Chunk` per file, `index=0`) called by `_sync_canon_store` |
| `co_cli/personality/prompts/validator.py` | `_discover_valid_personalities()`, `validate_personality_files()`, `VALID_PERSONALITIES` |
| `co_cli/personality/prompts/souls/` | Soul file trees: `finch/`, `jeff/`, `tars/` |
| `co_cli/context/rules/` | Universal behavioral rule files `01_identity.md` – `07_memory_protocol.md` |
| `co_cli/personality/_profiles/` | Human-readable character narrative docs (`finch.md`, `jeff.md`, `tars.md`) — not loaded into agent |
| `co_cli/config/core.py` | `personality` config field, `_validate_personality_name()`, startup validation call |
| `co_cli/agent/build.py` | `build_orchestrator()` — iterates `ORCHESTRATOR_SPEC.static_instruction_builders` and registers per-turn instruction callbacks |
| `co_cli/agent/orchestrator.py` | `ORCHESTRATOR_SPEC` and its 3 static-instruction builder closures + 4 per-turn instruction callbacks |
| `co_cli/agent/_instructions.py` | Per-turn callbacks: `safety_prompt`, `current_time_prompt`, `deferred_tool_awareness_prompt`, `skill_manifest_prompt` |
