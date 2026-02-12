# TODO: Prompt System Redesign

Prompt composition with adaptation overlays and manifest diagnostics.

---

## Current State

The prompt system uses an aspect-driven architecture (~5.6 KiB total) with composable personalities and model counter-steering. Key modules:

- **Aspects:** 7 behavioral markdown files (`identity.md`, `intent.md`, `safety.md`, `reasoning.md`, `tool_use.md`, `response_style.md`, `workflow.md`) — always all loaded. The LLM reasons natively about what's relevant
- **Personalities:** composable character + style aspects with preset registry (`_registry.py`, `_composer.py`). Original role essays preserved in `roles/` (not loaded at runtime)
- **Model quirks:** `model_quirks.py` — counter-steering text, `normalize_model_name()`
- **Memory:** `knowledge.py` loads always-on context (background memory); `tools/memory.py` provides on-demand memory tools. Independent modules — no prompt structure dependency
- **Assembly:** `get_system_prompt(provider, personality, model_name)` in `prompts/__init__.py` — 5-part string concatenation

### Gaps

The current system works but has structural gaps:

1. **No test coverage** — all prompt tests were deleted during the aspect refactor; no replacements written
2. **Prompt/runtime policy divergence** — `aspects/approval.md` says "side-effectful tools require approval" but runtime auto-approves safe shell commands in sandbox mode; the model never learns the actual policy
3. **No assembly diagnostics** — no manifest or introspection for what parts were loaded or what the token budget looks like

### Current Assembly (4 parts, string concatenation)

```
get_system_prompt(provider, personality, model_name) → str

  1. aspects (all 7)         # behavioral markdown files, always loaded
  2. counter_steering        # model quirk text (optional)
  3. personality             # always full (character + style)
  4. memory                  # load_memory() in <system-reminder>
```

No base instructions layer — fundamental rules are scattered across aspect files.

Callers: `agent.py:94` (startup), `_commands.py:151` (`/model` switch). Both call `get_system_prompt()` directly.

### Target Assembly (5 parts, direct string building with manifest)

```
assemble_prompt(ctx: PromptContext) → (str, PromptManifest)

  1. Instructions       — base system rules from instructions.md (NEW, largely empty for MVP)
  2. Aspects (all 7)    — always loaded; safety aspect includes safe commands list (NEW)
  3. Counter-steering   — model quirk text from MODEL_QUIRKS (if applicable)
  4. Personality        — character+style, always full
  5. Memory             — <system-reminder> wrapped
```

`get_system_prompt()` is replaced by `assemble_prompt()`. Callers (`agent.py`, `_commands.py`) updated to use the new API directly.

### Architecture Diagram

```
  ┌────────────────────────────────────────┐
  │            assemble_prompt()           │
  │                                        │
  │  1. instructions.md (base rules)       │
  │  2. aspects/*.md    (all 7, always)    │
  │     └─ safety includes safe commands   │
  │  3. counter-steering (if model quirk)  │
  │  4. personality     (always full)      │
  │  5. memory          (<system-reminder>)│
  │                                        │
  │  → append in order, join              │
  └───────┬────────────────────────────────┘
          │
    ┌─────┴──────┐
    ▼            ▼
  prompt    PromptManifest
  (str)     (parts_loaded, total_chars,
             warnings)
```

### Aspect Inventory

7 behavioral aspects, each a focused markdown file in `aspects/`. Always all loaded — the LLM reasons natively about relevance.

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

**PromptManifest for diagnostics.** Every assembly returns a manifest with: `parts_loaded`, `total_chars`, and `warnings`. Available for debugging (`/prompt` slash command, log output) without parsing the prompt text.

**Base instructions layer.** `instructions.md` is loaded first, before all aspects. It holds fundamental system rules that apply unconditionally — the kind of rules that should never be scattered across aspect files. Largely empty for MVP; provides a stable extension point for future guardrails without touching aspect files. Every peer system has an equivalent (Claude Code's system prompt preamble, Codex's orchestrator template header, Gemini CLI's system prompt constants).

**Always-all aspects, no selection.** All 7 aspects are always loaded. No pre-turn classifier, no aspect selection, no tier-based removal. This is the converged pattern across every peer system (Claude Code, Codex, Gemini CLI) — all send the full behavioral spec and let the LLM reason natively about what's relevant. A pre-turn classifier adds latency, can conflict with the model's judgment, and no production system does it.

**Safe commands folded into safety aspect.** The safety aspect includes the live `shell_safe_commands` list (interpolated at load time). The model sees the actual auto-approved commands alongside the approval rules, closing the prompt/runtime policy gap. No separate runtime policy part needed.

**Aspect set redesigned from peer convergence.** The 7 aspects map to categories that 10+ peer systems independently converge on. `multi_turn.md` absorbed into identity (conversation awareness is foundational — no peer treats it as separate). `approval.md` expands to `safety` (adds credential protection, destructive action caution). `fact_verify.md` expands to `reasoning` (adds anti-sycophancy, professional objectivity). `tool_output.md` expands to `tool_use` (full tool policy, not just output formatting). `workflow` is new (Research → Plan → Execute → Verify, converged across Gemini/Codex/Claude Code).

**Model adaptation is counter-steering only.** `TIER_ASPECTS` and tier-dependent personality scaling are removed. `multi_turn.md` counter-steering stays for models that need explicit conversation awareness reminding (e.g., GLM-4.7).

**Clean replacement.** `get_system_prompt()` is replaced by `assemble_prompt(ctx: PromptContext) -> (str, PromptManifest)`. Callers in `agent.py` and `_commands.py` are updated to construct a `PromptContext` and call `assemble_prompt()` directly.

### What Changes vs What Doesn't

| Changed | Unchanged |
|---------|-----------|
| `co_cli/prompts/__init__.py` — `get_system_prompt()` replaced by `assemble_prompt()` | `co_cli/prompts/personalities/` — registry, composer, aspects, roles/ |
| `co_cli/prompts/model_quirks.py` — `TIER_ASPECTS` removed, `get_model_tier()` removed | `co_cli/knowledge.py` — `load_memory()` consumed as memory part |
| `co_cli/prompts/aspects/*.md` — 7 files renamed/merged/expanded per aspect inventory | |
| New: `co_cli/prompts/instructions.md` — base system rules (largely empty for MVP) | |
| `co_cli/agent.py` — updated to call `assemble_prompt()` | |
| `co_cli/_commands.py` — updated to call `assemble_prompt()` | |
| New: `_manifest.py`, `_context.py` | |
| New: `tests/test_prompt_contracts.py` | |

### Peer Patterns Informing This Design

| Pattern | Peer Evidence | co-cli Adoption |
|---------|--------------|-----------------|
| Base system instructions | Claude Code system prompt preamble, Codex orchestrator template header, Gemini CLI system prompt constants | `instructions.md` loaded first, before aspects |
| Full behavioral spec, always present | Claude Code, Codex, Gemini CLI — all send complete behavioral instructions every turn. No peer selects/removes aspects | All 7 aspects always loaded |
| Safe commands in prompt | Codex safe-command allowlists in prompt, Claude Code hook-based permission engine | Interpolated into safety aspect |
| Model-specific adaptation | Aider model warnings, Codex quirk database | Counter-steering only (tier removed) |
| Prompt budget enforcement | All peers have implicit or explicit size limits | Manifest `total_chars` + contract test assertions |
| Post-hoc tool policy | All three systems enforce safety after model proposes tool call (allow/ask/deny) | `requires_approval=True` + safe command allowlist |

---

## Part A: Prompt System Code

### A1. Pure dataclasses (no deps)

- [ ] `co_cli/prompts/_manifest.py` — `PromptManifest` dataclass
  - Fields: `parts_loaded` (list[str]), `total_chars` (int), `warnings` (list[str])
- [ ] `co_cli/prompts/_context.py` — `PromptContext` dataclass
  - Fields: `provider`, `model_name`, `personality` (optional)

### A2. Assembler in `__init__.py`

- [ ] `assemble_prompt(ctx: PromptContext) -> tuple[str, PromptManifest]` — direct string builder
- [ ] Remove `get_system_prompt()` — update `agent.py` and `_commands.py` to call `assemble_prompt()` directly

Assembly pseudocode:

```
assemble_prompt(ctx):
  parts = []
  part_names = []

  # 1. Base instructions — fundamental system rules
  instructions = _load_file("instructions.md")
  if instructions:
    parts.append(instructions)
    part_names.append("instructions")

  # 2. All 7 aspects — always loaded
  for name in ALL_ASPECTS:
    text = _load_aspect(name)
    if name == "safety":
      text += _format_safe_commands(safe_commands)  # interpolate live allowlist
    parts.append(text)
    part_names.append(name)

  # 3. Counter-steering
  cs = get_counter_steering(ctx.provider, ctx.model_name)
  if cs:
    parts.append(f"## Model-Specific Guidance\n{cs}")
    part_names.append("counter_steering")

  # 4. Personality — always full
  personality = compose_personality(ctx.personality)
  if personality:
    parts.append(personality)
    part_names.append("personality")

  # 5. Memory
  memory = load_memory()
  if memory:
    parts.append(f"<system-reminder>{memory}</system-reminder>")
    part_names.append("memory")

  prompt = "\n\n".join(parts)
  manifest = PromptManifest(
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
- [ ] `test_instructions_loaded_first` — prompt starts with content from `instructions.md`
- [ ] `test_all_aspects_always_present` — prompt contains content from all 7 aspect files
- [ ] `test_safety_includes_safe_commands` — safety section contains command names from settings
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

1. A1 → A2 (code, sequential — dataclasses then assembler)
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
