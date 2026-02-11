# TODO: Prompt System Redesign

Layered prompt composition with scoped instructions, adaptation overlays, and manifest diagnostics.

---

## Current State

The prompt system uses an aspect-driven architecture (~5.6 KiB total) with tier-based selection, composable personalities, and model counter-steering. Key modules:

- **Aspects:** 7 behavioral markdown files (`identity.md`, `inquiry.md`, `fact_verify.md`, `multi_turn.md`, `response_style.md`, `approval.md`, `tool_output.md`) selected by model tier (1/2/3)
- **Personalities:** composable character + style aspects with preset registry (`_registry.py`, `_composer.py`). Original role essays preserved in `roles/` (not loaded at runtime)
- **Model quirks:** `model_quirks.py` — tier classification, counter-steering text, `normalize_model_name()`
- **Memory/knowledge:** `knowledge.py` loads always-on context; `tools/memory.py` provides on-demand memory tools. Independent modules — no prompt structure dependency
- **Assembly:** `get_system_prompt(provider, personality, model_name)` in `prompts/__init__.py` — 5-layer string concatenation

### Gaps

The current system works but has structural gaps:

1. **No test coverage** — all prompt tests were deleted during the aspect refactor; no replacements written
2. **No instruction layering** — only one project-level file (`.co-cli/instructions.md`) is loaded; no global scope
3. **Prompt/runtime policy divergence** — `aspects/approval.md` says "side-effectful tools require approval" but runtime auto-approves safe shell commands in sandbox mode; the model never learns the actual policy
4. **No assembly diagnostics** — no manifest or introspection for what layers were loaded, which instruction files contributed, or what the token budget looks like

### Current Assembly (5 layers, string concatenation)

```
get_system_prompt(provider, personality, model_name) → str

  1. aspects[tier]           # tier-selected markdown files (identity, inquiry, ...)
  2. counter_steering        # model quirk text (optional)
  3. personality             # tier-dependent: skip / style-only / full
  4. knowledge               # load_internal_knowledge() in <system-reminder>
  5. project instructions    # .co-cli/instructions.md (single file)
```

Callers: `agent.py:94` (startup), `_commands.py:151` (`/model` switch). Both call `get_system_prompt()` directly.

### Target Assembly (7 layers, typed composition with manifest)

```
assemble_prompt(ctx: PromptContext) → (str, PromptManifest)

  Priority 10: Core aspects       — tier-selected from aspects/ (unchanged)
  Priority 20: Mode overlay       — chat/task/code placeholder (returns None for MVP)
  Priority 30: Counter-steering   — model quirk text from MODEL_QUIRKS (unchanged)
  Priority 40: Personality        — character+style, tier-dependent (unchanged)
  Priority 60: Knowledge          — <system-reminder> wrapped (unchanged)
  Priority 70: Scoped instructions — global → project (NEW)
  Priority 90: Runtime policy     — generated from live safe_commands list (NEW)
```

`get_system_prompt()` is replaced by `assemble_prompt()`. Callers (`agent.py`, `_commands.py`) updated to use the new API directly.

### Architecture Diagram

```
                    PromptContext
                   (provider, model, personality, mode, cwd)
                         │
                         ▼
              ┌─────────────────────┐
              │   assemble_prompt() │
              └────────┬────────────┘
                       │
        ┌──────────────┼──────────────────────┐
        ▼              ▼                      ▼
  Core Layers    Adaptation Layers      Scoped Layers
  ─────────────  ──────────────────    ──────────────
  aspects/*.md   mode overlay (p20)    instructions (p70)
  (p10)          counter-steer (p30)   runtime policy (p90)
  personality    knowledge (p60)
  (p40)
        │              │                      │
        └──────────────┼──────────────────────┘
                       ▼
              ┌─────────────────────┐
              │  _compose_layers()  │
              │  sort by priority   │
              │  join with headings │
              └────────┬────────────┘
                       │
               ┌───────┴───────┐
               ▼               ▼
         prompt: str    PromptManifest
                       (layers, files, chars,
                        tokens, warnings)
```

### Key Design Decisions

**PromptLayer as frozen dataclass, not dict.** Every layer has typed fields: `name`, `content`, `section_heading`, `scope` (enum-like string), `priority` (int), `tag` (optional wrapper like `<system-reminder>`). Layers are immutable after creation — composition never mutates.

**Priority-based ordering, not insertion order.** Layers are sorted by `priority` before joining. This lets callers add layers in any order and get deterministic output. Gaps between priority numbers (10, 20, 30...) leave room for future insertion without renumbering.

**PromptManifest for diagnostics.** Every assembly returns a manifest with: provider, model, tier, personality, mode, layers loaded (by name), instruction files discovered, quirk flags, total chars, estimated tokens (chars/4 heuristic), and warnings. The manifest is available for debugging (`/prompt` slash command, log output) without parsing the prompt text.

**Instruction discovery: two scopes.** Instruction files are discovered at two scopes:
- Global: `~/.config/co-cli/instructions.md`
- Project: `{project_root}/.co-cli/instructions.md` (already exists today)

Each file becomes a separate PromptLayer at priority 70, ordered global → project (project has higher effective precedence via recency). `_find_project_root()` walks up from `cwd` looking for `.co-cli/` or `.git/` markers.

**Runtime policy overlay fixes prompt/runtime divergence.** `get_runtime_policy_overlay(safe_commands)` generates a policy layer from the live `shell_safe_commands` list. The model sees the actual auto-approved commands, not a generic "side-effectful tools require approval" statement. This closes the gap where the model doesn't know `ls`, `cat`, `git status` etc. are auto-approved in sandbox mode.

**Mode overlay is a placeholder.** `get_mode_overlay("chat", tier)` returns `None` for MVP. The hook exists for future modes that need different behavioral emphasis without changing the assembly pipeline. Planned modes include:
- **learn** — knowledge curation behavior: research topics using existing tools (`web_search`, `web_fetch`), evaluate source quality, check for duplicates via `recall_memory`, propose structured `save_memory` calls with proper tags/categorization. No separate agent — the main agent with a learn-mode prompt overlay and existing tools is sufficient.
- **task/code/review** — future behavioral emphasis changes.

**Clean replacement.** `get_system_prompt()` is replaced by `assemble_prompt(ctx: PromptContext) -> (str, PromptManifest)`. Callers in `agent.py` and `_commands.py` are updated to construct a `PromptContext` and call `assemble_prompt()` directly.

### What Changes vs What Doesn't

| Changed | Unchanged |
|---------|-----------|
| `co_cli/prompts/__init__.py` — `get_system_prompt()` replaced by `assemble_prompt()` | `co_cli/prompts/model_quirks.py` — consumed as-is |
| `co_cli/agent.py` — updated to call `assemble_prompt()` | `co_cli/prompts/personalities/` — registry, composer, aspects, roles/ |
| `co_cli/_commands.py` — updated to call `assemble_prompt()` | `co_cli/prompts/aspects/*.md` — 7 aspect files |
| New: `_layers.py`, `_manifest.py`, `_context.py` | `co_cli/knowledge.py` — `load_internal_knowledge()` consumed as layer |
| New: `_instructions.py`, `_adaptations.py` | |
| New: `tests/test_prompt_contracts.py`, `tests/test_instructions.py` | |

### Peer Patterns Informing This Design

| Pattern | Peer Evidence | co-cli Adoption |
|---------|--------------|-----------------|
| Hierarchical instruction files | Codex `AGENTS.md` traversal, Gemini folder-level `GEMINI.md`, Claude Code `CLAUDE.md` scopes | 2-scope discovery: global → project |
| Typed prompt composition | Claude Code `PromptConfig` + policy fragments, Codex `PermissionMode` | `PromptLayer` + `PromptManifest` |
| Runtime policy injection | Codex safe-command allowlists in prompt, Claude Code hook-based permission engine | `get_runtime_policy_overlay(safe_commands)` |
| Model-specific adaptation | Aider model warnings, Codex quirk database | Existing tier + counter-steering (unchanged) |
| Prompt budget enforcement | All peers have implicit or explicit size limits | Manifest `total_chars` + contract test assertions |

---

## Part A: Prompt System Code

### A1. Pure dataclasses (no deps)

- [ ] `co_cli/prompts/_layers.py` — `PromptLayer` frozen dataclass
  - Fields: `name`, `content`, `section_heading`, `scope` (core|mode|quirk|personality|knowledge|instructions|policy), `priority` (int), `tag` (optional wrapper tag)
- [ ] `co_cli/prompts/_manifest.py` — `PromptManifest` dataclass
  - Fields: `provider`, `model`, `tier`, `personality`, `mode`, `layers_loaded` (list[str]), `instruction_files` (list[str]), `quirk_flags` (dict), `total_chars` (int), `estimated_tokens` (int), `warnings` (list[str])
- [ ] `co_cli/prompts/_context.py` — `PromptContext` dataclass
  - Fields: `provider`, `model_name`, `personality` (optional), `mode` (default "chat"), `cwd` (default Path.cwd())

### A2. Instruction discovery

- [ ] `co_cli/prompts/_instructions.py`
  - `discover_instruction_files(cwd) -> list[tuple[str, Path]]` — scoped discovery (global, project)
  - `_find_project_root(start) -> Path` — walk up to `.co-cli/` or `.git/` marker

### A3. Adaptation overlays

- [ ] `co_cli/prompts/_adaptations.py`
  - `get_mode_overlay(mode, tier) -> PromptLayer | None` — placeholder (returns None for "chat")
  - `get_runtime_policy_overlay(safe_commands) -> PromptLayer` — generates approval policy text from live settings

### A4. Assembler in `__init__.py`

- [ ] `assemble_prompt(ctx: PromptContext) -> tuple[str, PromptManifest]` — new layered assembler
- [ ] `_compose_layers(layers: list[PromptLayer]) -> str` — sort by priority, join with headings
- [ ] `_build_personality_layer(personality, tier) -> PromptLayer | None` — tier-aware personality
- [ ] Remove `get_system_prompt()` — update `agent.py` and `_commands.py` to call `assemble_prompt()` directly

Assembly pseudocode:

```
assemble_prompt(ctx):
  layers = []

  # Core aspects (priority 10) — one layer per aspect
  tier = get_model_tier(ctx.provider, ctx.model_name)
  for name in TIER_ASPECTS[tier]:
    layers.append(PromptLayer(name=name, content=_load_aspect(name),
                              scope="core", priority=10))

  # Mode overlay (priority 20) — placeholder
  mode_layer = get_mode_overlay(ctx.mode, tier)
  if mode_layer: layers.append(mode_layer)

  # Counter-steering (priority 30)
  if ctx.model_name:
    cs = get_counter_steering(ctx.provider, ctx.model_name)
    if cs:
      layers.append(PromptLayer(name="counter_steering", content=cs,
                                heading="Model-Specific Guidance",
                                scope="quirk", priority=30))

  # Personality (priority 40)
  personality_layer = _build_personality_layer(ctx.personality, tier)
  if personality_layer: layers.append(personality_layer)

  # Knowledge (priority 60)
  knowledge = load_internal_knowledge()
  if knowledge:
    layers.append(PromptLayer(name="knowledge", content=knowledge,
                              scope="knowledge", priority=60,
                              tag="system-reminder"))

  # Scoped instructions (priority 70)
  for scope_name, path in discover_instruction_files(ctx.cwd):
    content = path.read_text(encoding="utf-8").strip()
    if content:
      layers.append(PromptLayer(name=f"instructions_{scope_name}",
                                content=content,
                                heading=f"{scope_name} Instructions",
                                scope="instructions", priority=70))

  # Runtime policy (priority 90) — safe commands from settings
  # Note: safe_commands read from settings at assembly time
  policy_layer = get_runtime_policy_overlay(safe_commands)
  layers.append(policy_layer)

  # Compose and build manifest
  prompt = _compose_layers(layers)
  manifest = PromptManifest(
    provider=ctx.provider, model=ctx.model_name, tier=tier,
    personality=ctx.personality, mode=ctx.mode,
    layers_loaded=[l.name for l in sorted(layers, key=lambda l: l.priority)],
    instruction_files=[str(p) for _, p in discover_instruction_files(ctx.cwd)],
    quirk_flags=get_quirk_flags(ctx.provider, ctx.model_name or ""),
    total_chars=len(prompt),
    estimated_tokens=len(prompt) // 4,
    warnings=[...]
  )
  return prompt, manifest
```

## Part B: Tests (First Principles Redesign)

**Philosophy:** All prior prompt/memory/knowledge tests were deleted — they tested an obsolete monolithic prompt or implementation details that changed. New tests are designed from the contracts that matter: what each module promises, not how it works inside.

**No mocks, no stubs.** Per project policy: functional tests that exercise real code paths with real files on disk. Use `tmp_path` fixtures for filesystem isolation.

### B1. Memory System Tests — `tests/test_memory.py`

Tests the core memory lifecycle: file I/O, dedup, consolidation, decay, search.

**Storage contract — files are the API:**
- [ ] `test_save_creates_valid_file` — save_memory creates `{id:03d}-{slug}.md` with valid YAML frontmatter (id, created, tags, source, auto_category) and markdown body
- [ ] `test_load_roundtrip` — write a memory file by hand, `_load_all_memories()` returns a MemoryEntry with matching id, content, tags, created
- [ ] `test_load_skips_malformed` — directory with one valid + one invalid file (missing `id` in frontmatter) → loads only the valid one, no crash
- [ ] `test_load_empty_dir` — `_load_all_memories()` on an empty dir returns `[]`
- [ ] `test_load_missing_dir` — `_load_all_memories()` on a nonexistent path returns `[]`

**Dedup contract — similar content is detected:**
- [ ] `test_dedup_exact_match` — identical content → `_check_duplicate` returns `(True, entry, 100.0)`
- [ ] `test_dedup_word_reorder` — "I prefer TypeScript" vs "TypeScript I prefer" → detected (token_sort_ratio handles reordering)
- [ ] `test_dedup_below_threshold` — "I prefer TypeScript" vs "I prefer Python" → not detected (different enough)
- [ ] `test_dedup_empty_corpus` — no recent memories → `(False, None, 0.0)`

**Consolidation contract — dedup triggers in-place update:**
- [ ] `test_consolidation_merges_tags` — existing memory with tags `[a, b]` + new memory with tags `[b, c]` → result has tags `[a, b, c]`
- [ ] `test_consolidation_sets_updated` — consolidated memory has `updated` field in frontmatter
- [ ] `test_consolidation_replaces_content` — body text is replaced with new content

**Decay contract — overflow triggers cleanup:**
- [ ] `test_decay_triggered_at_limit` — set `memory_max_count=3`, save 4th memory → one memory file is deleted
- [ ] `test_decay_protects_flagged` — memory with `decay_protected: true` is never deleted even when oldest
- [ ] `test_decay_summarize_creates_consolidated` — summarize strategy deletes originals and creates one `_consolidated` tagged memory
- [ ] `test_decay_cut_deletes_only` — cut strategy deletes originals, creates nothing new

**Search contract — recall finds what was saved:**
- [ ] `test_recall_by_content` — save memory containing "pytest", recall("pytest") finds it
- [ ] `test_recall_by_tag` — save memory with tag "preference", recall("preference") finds it
- [ ] `test_recall_case_insensitive` — save "TypeScript", recall("typescript") finds it
- [ ] `test_recall_recency_order` — save 3 memories at different times, recall returns newest first
- [ ] `test_recall_max_results` — save 10 memories, recall with max_results=3 returns exactly 3
- [ ] `test_recall_no_match` — recall("nonexistent") returns count=0

**List contract — inventory of all memories:**
- [ ] `test_list_empty` — no memories → count=0
- [ ] `test_list_shows_all` — save 3 memories → list returns count=3 with correct IDs

**Helpers:**
- [ ] `test_slugify_basic` — "Hello World" → "hello-world"
- [ ] `test_slugify_special_chars` — "café @#! thing" → "caf-thing" (strips specials, truncates at 50)
- [ ] `test_detect_source_signal_tags` — tags containing "preference" → "detected"
- [ ] `test_detect_source_no_signal` — tags like ["python"] → "user-told"
- [ ] `test_detect_category` — first matching category tag is returned

### B2. Knowledge Loading Tests — `tests/test_knowledge.py`

Tests the always-on context loading pipeline: file discovery, frontmatter validation, size enforcement.

**Loading contract:**
- [ ] `test_no_files_returns_none` — neither global nor project context.md exists → `None`
- [ ] `test_global_only` — only `~/.config/co-cli/knowledge/context.md` exists → returns content under `### Global Context` heading
- [ ] `test_project_only` — only `.co-cli/knowledge/context.md` exists → returns content under `### Project Context` heading
- [ ] `test_both_combined` — both exist → combined output has both `### Global Context` and `### Project Context` sections
- [ ] `test_frontmatter_stripped` — context.md with valid frontmatter → frontmatter not in output body

**Validation contract:**
- [ ] `test_context_frontmatter_valid` — `{version: 1, updated: "2026-02-11T00:00:00Z"}` passes validation
- [ ] `test_context_frontmatter_missing_version` — raises ValueError
- [ ] `test_context_frontmatter_missing_updated` — raises ValueError
- [ ] `test_memory_frontmatter_valid` — `{id: 1, created: "2026-02-11T00:00:00Z"}` passes
- [ ] `test_memory_frontmatter_missing_id` — raises ValueError
- [ ] `test_memory_frontmatter_bad_tags_type` — `tags: "not-a-list"` raises ValueError
- [ ] `test_malformed_yaml_returns_empty` — `---\n: :\n---\nbody` → `parse_frontmatter` returns `({}, content)`

**Size enforcement:**
- [ ] `test_soft_limit_warns` — knowledge between 10–20 KiB → warning to stderr, content not truncated
- [ ] `test_hard_limit_truncates` — knowledge over 20 KiB → truncated to 20 KiB

### B3. Personality Tests — `tests/test_personality.py`

Tests the composable personality system: registry, composition, tier behavior, role essays.

**Registry contract — all presets are valid:**
- [ ] `test_all_presets_have_aspect_files` — every entry in PRESETS has a valid style file; presets with character≠None have a valid character file
- [ ] `test_valid_personalities_matches_presets` — `VALID_PERSONALITIES` list matches `PRESETS.keys()`

**Composition contract:**
- [ ] `test_compose_character_plus_style` — "finch" preset returns text containing both character and style content
- [ ] `test_compose_no_character` — "terse" preset (character=None) returns only style content
- [ ] `test_compose_style_only` — `compose_style_only("finch")` returns style content only, no character text
- [ ] `test_compose_unknown_preset` — raises KeyError
- [ ] `test_compose_missing_aspect_file` — rigged preset pointing to nonexistent file → raises FileNotFoundError

**Tier behavior in assembler:**
- [ ] `test_tier1_skips_personality` — tier 1 model + personality="finch" → prompt does not contain personality text
- [ ] `test_tier2_style_only` — tier 2 model + personality="finch" → prompt contains style text but not character text
- [ ] `test_tier3_full_personality` — tier 3 model + personality="finch" → prompt contains both character and style text

**Role essays — baseline reference preserved:**
- [ ] `test_role_essays_exist` — `roles/{name}.md` exists for every preset name in PRESETS
- [ ] `test_role_essays_nonempty` — each role essay has > 100 chars of content

### B4. Prompt Assembly Tests — `tests/test_prompt_assembly.py`

Tests the assembled prompt output: layer presence, ordering, budget.

**Layer presence contract:**
- [ ] `test_tier1_minimal_aspects` — tier 1 prompt contains identity + multi_turn aspects only
- [ ] `test_tier3_all_aspects` — tier 3 prompt contains all 7 aspect names' content
- [ ] `test_counter_steering_present` — known quirk model (glm-4.7-flash) → prompt contains "Model-Specific Guidance"
- [ ] `test_counter_steering_absent` — default model with no quirks → prompt does not contain "Model-Specific Guidance"
- [ ] `test_knowledge_wrapped_in_system_reminder` — when knowledge exists, prompt contains `<system-reminder>` tags around it
- [ ] `test_project_instructions_appended` — when `.co-cli/instructions.md` exists, its content appears in prompt

**Budget contract:**
- [ ] `test_prompt_under_budget` — all provider/model combos produce prompt < 8K chars (iterates known models)
- [ ] `test_prompt_nonempty` — all provider/model combos produce non-empty prompt

**Post-redesign (after Part A):**
- [ ] `test_assemble_prompt_returns_manifest` — `assemble_prompt(ctx)` returns `(str, PromptManifest)` tuple
- [ ] `test_manifest_layers_match_output` — manifest `layers_loaded` list matches what's actually in the prompt string
- [ ] `test_manifest_char_count_accurate` — manifest `total_chars` == `len(prompt)`
- [ ] `test_runtime_policy_includes_safe_commands` — policy layer text contains command names from settings
- [ ] `test_scoped_instructions_global_and_project` — both instruction scopes appear in manifest `instruction_files`

### B5. Instruction Discovery Tests — `tests/test_instructions.py`

Tests the scoped instruction file discovery (Part A2 — write after `_instructions.py` exists).

- [ ] `test_no_instruction_files` — empty tmp dir → empty list
- [ ] `test_global_only` — only `~/.config/co-cli/instructions.md` → `[("global", path)]`
- [ ] `test_project_only` — only `{root}/.co-cli/instructions.md` → `[("project", path)]`
- [ ] `test_both_scopes` — both files exist → list has 2 entries in global→project order
- [ ] `test_project_root_detection_git` — `.git/` marker identifies project root
- [ ] `test_project_root_detection_co_cli` — `.co-cli/` marker identifies project root

## Part C: Doc Consolidation

### C1. Consolidated review

- [ ] Write `docs/REVIEW-prompt-system-redesign-2026-02-11.md`
  - Merges content from 3 source docs (landscape, peer convergence, prompt construction)
  - Sections: executive verdict, landscape positioning, peer patterns, current implementation, gap resolutions, differentiators, sources

### C2. Design doc

- [ ] Write `docs/DESIGN-16-prompt-system.md` (4-section template)
  - What & How: layered prompt composition with diagram
  - Core Logic: layer types, assembly algorithm, instruction discovery, adaptation, manifest
  - Config: personality setting (no new config — instructions are discovered)
  - Files: all prompt system files

### C3. Delete source review docs

- [ ] Delete `docs/REVIEW-co-cli-prompt-construction-and-crafting-2026-02-11.md`
- [ ] Delete `docs/REVIEW-co-prompt-structure-converged-peer-systems-2026-02-10.md`
- [ ] Delete `docs/REVIEW-landscape-similar-projects.md`

## Execution Order

1. A1 → A2 → A3 → A4 (code, sequential — each builds on prior)
2. B1 + B2 + B3 + B4 + B5 (tests, can parallelize after Part A)
3. C1 + C2 (docs, can parallelize)
4. C3 (delete source review docs)
5. Run full test suite, verify `co status`

## Verification

```bash
uv run pytest tests/test_prompt_contracts.py tests/test_instructions.py -v
uv run pytest -v
uv run co status
uv run python -c "
from co_cli.prompts import assemble_prompt
from co_cli.prompts._context import PromptContext
ctx = PromptContext(provider='gemini', model_name='gemini-2.0-flash', personality='finch')
prompt, manifest = assemble_prompt(ctx)
print(f'Layers: {manifest.layers_loaded}')
print(f'Chars: {manifest.total_chars}, ~Tokens: {manifest.estimated_tokens}')
print(f'Instructions: {manifest.instruction_files}')
print(f'Warnings: {manifest.warnings}')
"
```
