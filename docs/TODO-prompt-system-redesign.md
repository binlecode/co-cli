# TODO: Prompt System Redesign

Prompt composition with adaptation overlays and manifest diagnostics.

---

## Current State

The prompt system uses an aspect-driven architecture (~5.6 KiB total) with adaptive aspect selection, composable personalities, and model counter-steering. Key modules:

- **Aspects:** 7 behavioral markdown files (`identity.md`, `intent.md`, `safety.md`, `reasoning.md`, `tool_use.md`, `response_style.md`, `workflow.md`) selected per turn by reasoning LLM call (direct multi-select from query + conversation history)
- **Personalities:** composable character + style aspects with preset registry (`_registry.py`, `_composer.py`). Original role essays preserved in `roles/` (not loaded at runtime)
- **Model quirks:** `model_quirks.py` — counter-steering text, `normalize_model_name()`
- **Memory:** `knowledge.py` loads always-on context (background memory); `tools/memory.py` provides on-demand memory tools. Independent modules — no prompt structure dependency
- **Assembly:** `get_system_prompt(provider, personality, model_name)` in `prompts/__init__.py` — 5-part string concatenation

### Gaps

The current system works but has structural gaps:

1. **No test coverage** — all prompt tests were deleted during the aspect refactor; no replacements written
2. **Prompt/runtime policy divergence** — `aspects/approval.md` says "side-effectful tools require approval" but runtime auto-approves safe shell commands in sandbox mode; the model never learns the actual policy
3. **No assembly diagnostics** — no manifest or introspection for what parts were loaded or what the token budget looks like

### Current Assembly (4 parts, string concatenation with per-turn aspect selection)

```
get_system_prompt(provider, personality, model_name) → str

  Pre-assembly: select_aspects(query, history, model)  # reasoning LLM multi-selects relevant aspects
  1. aspects[selected]       # adaptively selected markdown files (identity, intent, ...)
  2. counter_steering        # model quirk text (optional)
  3. personality             # always full (character + style)
  4. memory                  # load_memory() in <system-reminder>
```

Callers: `agent.py:94` (startup), `_commands.py:151` (`/model` switch). Both call `get_system_prompt()` directly.

### Target Assembly (5 parts, direct string building with manifest)

```
assemble_prompt(ctx: PromptContext) → (str, PromptManifest)

  1. Core aspects       — adaptively selected from aspects/ (per-turn LLM multi-select)
  2. Counter-steering   — model quirk text from MODEL_QUIRKS (if applicable)
  3. Personality        — character+style, always full
  4. Memory             — <system-reminder> wrapped
  5. Runtime policy     — generated from live safe_commands list (NEW)
```

`get_system_prompt()` is replaced by `assemble_prompt()`. Callers (`agent.py`, `_commands.py`) updated to use the new API directly.

### Architecture Diagram

```
  User query + history
         │
         ▼
  ┌────────────────┐
  │ select_aspects │  reasoning LLM → picks from 7 aspects
  └───────┬────────┘
          │ e.g. [identity, intent, safety, tool_use]
          ▼
  ┌────────────────────────────────────────┐
  │            assemble_prompt()           │
  │                                        │
  │  1. aspects/*.md    (selected only)    │
  │  2. counter-steering (if model quirk)  │
  │  3. personality     (always full)      │
  │  4. memory          (<system-reminder>)│
  │  5. runtime policy  (safe commands)    │
  │                                        │
  │  → append in order, join with headings │
  └───────┬────────────────────────────────┘
          │
    ┌─────┴──────┐
    ▼            ▼
  prompt    PromptManifest
  (str)     (aspects_selected, parts_loaded,
             total_chars, warnings)
```

### Aspect Inventory

7 behavioral aspects, each a focused markdown file in `aspects/`. Selected per turn by reasoning LLM call.

| Aspect | Concern | Peer convergence | Content summary |
|--------|---------|------------------|----------------|
| **identity** | Who Co is, values, conversation awareness | Universal: Identity & Role | Role, local-first, approval-first, multi-turn context |
| **intent** | Inquiry vs Directive classification | Gemini, Codex, Aider | Default to inquiry; action verbs trigger directive |
| **safety** | Side-effect approval, credential protection, destructive caution | Universal: Safety & Approval | Approval gates, no credentials, explain destructive ops, scope limits |
| **reasoning** | Ground truth, fact verification, anti-sycophancy | Near-universal: Honesty | Trust tools, verify math, escalate contradictions, objectivity |
| **tool_use** | Tool invocation, result presentation, errors | Universal: Tool Use Policy | When to use tools, show display verbatim, error reporting, verify success |
| **response_style** | Terseness, formatting, completeness | Universal: Tone & Style | High-signal, no filler, complete before yielding |
| **workflow** | Multi-step task process | Gemini, Codex, Claude Code | Research → Plan → Execute → Verify |

### Key Design Decisions

**PromptManifest for diagnostics.** Every assembly returns a manifest with: `aspects_selected`, `parts_loaded`, `total_chars`, and `warnings`. Input params (provider, model, personality) stay in `PromptContext` — no echo-back. Available for debugging (`/prompt` slash command, log output) without parsing the prompt text.

**Runtime policy overlay fixes prompt/runtime divergence.** `get_runtime_policy_overlay(safe_commands)` generates policy text from the live `shell_safe_commands` list. The model sees the actual auto-approved commands, not a generic "side-effectful tools require approval" statement. This closes the gap where the model doesn't know `ls`, `cat`, `git status` etc. are auto-approved in sandbox mode.

**Direct adaptive aspect selection.** A reasoning LLM call examines each user query (with full conversation history) and multi-selects which of the 7 aspects are relevant for this turn. There is no intermediate "mode" abstraction — the model judges directly which behavioral instructions apply. This replaces tier-based aspect removal which had no peer backing and created safety gaps (tier 1 models never saw approval rules). The selector uses the same reasoning model, not cheap heuristics, because accurate alignment with user intent is critical. User can override but autonomous selection should make this rarely necessary.

**Aspect set redesigned from peer convergence.** The 7 aspects map to categories that 10+ peer systems independently converge on: identity (universal), intent classification (Gemini/Codex/Aider), safety (universal), reasoning/honesty (near-universal), tool use policy (universal), response style (universal), workflow (Gemini/Codex/Claude Code). `multi_turn.md` is absorbed into identity (conversation awareness is foundational, not a selectable behavior — no peer system treats it as a separate aspect). `approval.md` expands to `safety` (peer systems include credential protection, destructive action caution, not just approval gates). `fact_verify.md` expands to `reasoning` (peer category is broader: anti-sycophancy, professional objectivity). `tool_output.md` expands to `tool_use` (peer category is full tool policy, not just output formatting). `workflow` is new (converged across Gemini, Codex, Claude Code — Research → Plan → Execute → Verify).

**No tier-based aspect or personality selection.** `TIER_ASPECTS` and tier-dependent personality scaling are removed. Model-specific adaptation is handled exclusively by counter-steering. `multi_turn.md` counter-steering stays for models that need explicit conversation awareness reminding (e.g., GLM-4.7).

**Clean replacement.** `get_system_prompt()` is replaced by `assemble_prompt(ctx: PromptContext) -> (str, PromptManifest)`. Callers in `agent.py` and `_commands.py` are updated to construct a `PromptContext` and call `assemble_prompt()` directly.

### What Changes vs What Doesn't

| Changed | Unchanged |
|---------|-----------|
| `co_cli/prompts/__init__.py` — `get_system_prompt()` replaced by `assemble_prompt()` | `co_cli/prompts/personalities/` — registry, composer, aspects, roles/ |
| `co_cli/prompts/model_quirks.py` — `TIER_ASPECTS` removed, `get_model_tier()` removed | `co_cli/knowledge.py` — `load_memory()` consumed as memory part |
| `co_cli/prompts/aspects/*.md` — 7 files renamed/merged/expanded per aspect inventory | |
| `co_cli/agent.py` — updated to call `assemble_prompt()` | |
| `co_cli/_commands.py` — updated to call `assemble_prompt()` | |
| New: `_manifest.py`, `_context.py` | |
| New: `_adaptations.py` | |
| New: `tests/test_prompt_contracts.py` | |

### Peer Patterns Informing This Design

| Pattern | Peer Evidence | co-cli Adoption |
|---------|--------------|-----------------|
| Structured prompt composition | Claude Code `PromptConfig` + policy fragments, Codex `PermissionMode` | `assemble_prompt()` + `PromptManifest` |
| Runtime policy injection | Codex safe-command allowlists in prompt, Claude Code hook-based permission engine | `get_runtime_policy_overlay(safe_commands)` |
| Model-specific adaptation | Aider model warnings, Codex quirk database | Counter-steering only (tier removed) |
| Direct adaptive aspect selection | No peer uses tier-based removal. All send full behavioral spec. Adaptation is additive (counter-steering), not subtractive | Per-turn reasoning LLM multi-selects from 7 peer-converged aspects |
| Prompt budget enforcement | All peers have implicit or explicit size limits | Manifest `total_chars` + contract test assertions |
| 3-tier context model | See `TODO-3-tier-context-model.md` for full peer evidence | Memory (p60) + Knowledge (deferred) |

---

## Part A: Prompt System Code

### A1. Pure dataclasses (no deps)

- [ ] `co_cli/prompts/_manifest.py` — `PromptManifest` dataclass
  - Fields: `aspects_selected` (list[str]), `parts_loaded` (list[str]), `total_chars` (int), `warnings` (list[str])
- [ ] `co_cli/prompts/_context.py` — `PromptContext` dataclass
  - Fields: `provider`, `model_name`, `personality` (optional), `aspects` (list[str], default all 7)

### A2. Adaptation overlays

- [ ] `co_cli/prompts/_adaptations.py`
  - `select_aspects(query, history, model) -> list[str]` — reasoning LLM call, multi-selects from ALL_ASPECTS
  - `get_runtime_policy_overlay(safe_commands) -> str` — generates approval policy text from live settings

### A3. Assembler in `__init__.py`

- [ ] `assemble_prompt(ctx: PromptContext) -> tuple[str, PromptManifest]` — direct string builder
- [ ] Remove `get_system_prompt()` — update `agent.py` and `_commands.py` to call `assemble_prompt()` directly

Assembly pseudocode:

```
assemble_prompt(ctx):
  parts = []
  part_names = []

  # 1. Aspects — selected by reasoning LLM call
  ALL_ASPECTS = ["identity", "intent", "safety", "reasoning",
                 "tool_use", "response_style", "workflow"]
  aspects = ctx.aspects if ctx.aspects else ALL_ASPECTS
  for name in aspects:
    parts.append(_load_aspect(name))
    part_names.append(name)

  # 2. Counter-steering
  cs = get_counter_steering(ctx.provider, ctx.model_name)
  if cs:
    parts.append(f"## Model-Specific Guidance\n{cs}")
    part_names.append("counter_steering")

  # 3. Personality — always full
  personality = compose_personality(ctx.personality)
  if personality:
    parts.append(personality)
    part_names.append("personality")

  # 4. Memory
  memory = load_memory()
  if memory:
    parts.append(f"<system-reminder>{memory}</system-reminder>")
    part_names.append("memory")

  # 5. Runtime policy — safe commands from settings
  policy = get_runtime_policy_overlay(safe_commands)
  if policy:
    parts.append(policy)
    part_names.append("runtime_policy")

  prompt = "\n\n".join(parts)
  manifest = PromptManifest(
    aspects_selected=aspects,
    parts_loaded=part_names,
    total_chars=len(prompt),
    warnings=[...]
  )
  return prompt, manifest
```

## Part B: Tests (First Principles Redesign)

**Philosophy:** All prior prompt/memory tests were deleted — they tested an obsolete monolithic prompt or implementation details that changed. New tests are designed from the contracts that matter: what each module promises, not how it works inside.

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

### B2. Memory Loading Tests — `tests/test_memory_loading.py`

Tests the always-on context loading pipeline (`knowledge.py` — background memory in the 3-tier model): file discovery, frontmatter validation, size enforcement.

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
- [ ] `test_soft_limit_warns` — memory between 10–20 KiB → warning to stderr, content not truncated
- [ ] `test_hard_limit_truncates` — memory over 20 KiB → truncated to 20 KiB

### B3. Personality Tests — `tests/test_personality.py`

Tests the composable personality system: registry, composition, always-full behavior, role essays.

**Registry contract — all presets are valid:**
- [ ] `test_all_presets_have_aspect_files` — every entry in PRESETS has a valid style file; presets with character≠None have a valid character file
- [ ] `test_valid_personalities_matches_presets` — `VALID_PERSONALITIES` list matches `PRESETS.keys()`

**Composition contract:**
- [ ] `test_compose_character_plus_style` — "finch" preset returns text containing both character and style content
- [ ] `test_compose_no_character` — "terse" preset (character=None) returns only style content
- [ ] `test_compose_style_only` — `compose_style_only("finch")` returns style content only, no character text
- [ ] `test_compose_unknown_preset` — raises KeyError
- [ ] `test_compose_missing_aspect_file` — rigged preset pointing to nonexistent file → raises FileNotFoundError

**Always-full personality:**
- [ ] `test_personality_always_loaded` — any model + personality="finch" → prompt contains both character and style text

**Role essays — baseline reference preserved:**
- [ ] `test_role_essays_exist` — `roles/{name}.md` exists for every preset name in PRESETS
- [ ] `test_role_essays_nonempty` — each role essay has > 100 chars of content

### B4. Prompt Assembly Tests — `tests/test_prompt_assembly.py`

Tests the assembled prompt output: part presence, ordering, budget.

**Part presence contract:**
- [ ] `test_all_aspects_selected` — all 7 in ctx.aspects → all present in prompt
- [ ] `test_subset_aspects_selected` — subset in ctx.aspects → only those in prompt
- [ ] `test_empty_aspects_fallback` — empty ctx.aspects → fallback to all 7 (safety net)
- [ ] `test_counter_steering_present` — known quirk model (glm-4.7-flash) → prompt contains "Model-Specific Guidance"
- [ ] `test_counter_steering_absent` — default model with no quirks → prompt does not contain "Model-Specific Guidance"
- [ ] `test_memory_wrapped_in_system_reminder` — when memory exists, prompt contains `<system-reminder>` tags around it
**Budget contract:**
- [ ] `test_prompt_under_budget` — all provider/model combos produce prompt < 8K chars (iterates known models)
- [ ] `test_prompt_nonempty` — all provider/model combos produce non-empty prompt

**Post-redesign (after Part A):**
- [ ] `test_assemble_prompt_returns_manifest` — `assemble_prompt(ctx)` returns `(str, PromptManifest)` tuple
- [ ] `test_manifest_parts_match_output` — manifest `parts_loaded` list matches what's actually in the prompt string
- [ ] `test_manifest_char_count_accurate` — manifest `total_chars` == `len(prompt)`
- [ ] `test_runtime_policy_includes_safe_commands` — policy text contains command names from settings

## Part C: Doc Consolidation

### C1. Consolidated review

- [ ] Write `docs/REVIEW-prompt-system-redesign-2026-02-11.md`
  - Merges content from 3 source docs (landscape, peer convergence, prompt construction)
  - Sections: executive verdict, landscape positioning, peer patterns, current implementation, gap resolutions, differentiators, sources

### C2. Design doc

- [ ] Write `docs/DESIGN-16-prompt-system.md` (4-section template)
  - What & How: prompt composition with diagram
  - Core Logic: assembly algorithm, adaptation, manifest
  - Config: personality setting
  - Files: all prompt system files

### C3. Delete source review docs

- [ ] Delete `docs/REVIEW-co-cli-prompt-construction-and-crafting-2026-02-11.md`
- [ ] Delete `docs/REVIEW-co-prompt-structure-converged-peer-systems-2026-02-10.md`
- [ ] Delete `docs/REVIEW-landscape-similar-projects.md`

## Execution Order

1. A1 → A2 → A3 (code, sequential — each builds on prior)
2. B1 + B2 + B3 + B4 (tests, can parallelize after Part A)
3. C1 + C2 (docs, can parallelize)
4. C3 (delete source review docs)
5. Run full test suite, verify `co status`

## Verification

```bash
uv run pytest tests/test_prompt_contracts.py -v
uv run pytest -v
uv run co status
uv run python -c "
from co_cli.prompts import assemble_prompt
from co_cli.prompts._context import PromptContext
ctx = PromptContext(provider='gemini', model_name='gemini-2.0-flash', personality='finch')
prompt, manifest = assemble_prompt(ctx)
print(f'Parts: {manifest.parts_loaded}')
print(f'Chars: {manifest.total_chars}')
print(f'Warnings: {manifest.warnings}')
"
```
