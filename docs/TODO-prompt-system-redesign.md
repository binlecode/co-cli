# TODO: Prompt System Redesign

Layered prompt composition: rules-fixed system prompt + on-demand context tools.

---

## Current State

The prompt system uses a **rules-fixed + on-demand context** architecture. The system prompt is minimal (instructions + behavioral rules + counter-steering). Aspects, personality, and memory are loaded on-demand by the agent via context tools.

Key modules:

- **System prompt assembly:** `assemble_prompt(provider, model_name)` in `prompts/__init__.py` — 3-part string concatenation returning `(str, PromptManifest)`
- **Instructions:** `prompts/instructions.md` — bootstrap identity (2-line preamble before rules)
- **Rules:** 7 numbered markdown files in `prompts/rules/` (01_identity through 07_workflow) — always loaded into system prompt
- **Aspects:** situational guidance markdown files in `prompts/aspects/` (debugging, planning, code_review) — loaded on-demand via `load_aspect()` tool
- **Personalities:** composable character x style with 5 presets (`_registry.py`, `_composer.py`). Loaded on-demand via `load_personality()` tool. Role essays in `roles/` (reference, not loaded by default)
- **Model quirks:** `model_quirks.py` — counter-steering text, `normalize_model_name()`, inference params
- **Context:** `knowledge.py:load_memory()` — global + project context.md, loaded at startup (not a tool)
- **Memory:** `recall_memory()`, `list_memories()`, `save_memory()` tools — persistent memory search
- **Manifest:** `_manifest.py` — `PromptManifest(parts_loaded, total_chars, warnings)` returned by assembler

### Current Assembly (3 parts, string concatenation)

```
assemble_prompt(provider, model_name) -> (str, PromptManifest)

  1. instructions.md     # bootstrap identity (2-line preamble)
  2. rules/*.md          # 7 behavioral rules (01-07), always loaded
  3. counter_steering    # model quirk text (optional)

Context (aspects, personality, knowledge) loaded on-demand via tools.
```

Callers: `agent.py:96` (startup), `_commands.py:152` (`/model` switch).

### Open Gaps

1. **Prompt/runtime policy divergence** — `03_safety.md` mentions the safe-command allowlist conceptually, but `assemble_prompt()` does not interpolate actual commands. The model knows the policy exists but not which commands are safe (→ A2)
2. **Personality loading is fragile** — `05_tool_use.md` directs "load personality character piece at session start" but there's no enforcement hook. Agent may skip it (→ A3)
3. **Test coverage incomplete** — assembly and context tools covered. Memory system (B1), memory loading (B2), personality registry (B3) still TODO

### Remaining Targets

1. **Safe commands interpolated into safety rule** — close prompt/runtime policy gap
2. **Personality loaded reliably at session start** — enforce via agent startup sequence, not just `05_tool_use.md` directive
3. **Manifest tracks on-demand loads** — extend PromptManifest to record which context tools were called during session

### Architecture Diagram

```
  +-- System Prompt (assembled once at startup) ---------+
  |                                                      |
  |  1. instructions.md    (bootstrap identity)          |
  |  2. rules/01..07.md    (7 behavioral rules)          |
  |  3. counter-steering   (if model quirk)              |
  |                                                      |
  +------------------------------------------------------+
                          |
                          v
  +-- On-Demand (LLM-initiated via context tools) -------+
  |                                                      |
  |  load_aspect(names)       -> situational guidance    |
  |  load_personality(pieces) -> character, style        |
  |  recall_memory(query)     -> persistent memories     |
  |                                                      |
  +------------------------------------------------------+
```

### Rules and Aspects

7 numbered rules (always in system prompt) + 3 situational aspects (on-demand via `load_aspect()`).

**Rules** define Co's baseline intelligence. Every word directly shapes how Co reasons, decides, and acts. Treat each rule file as a carefully engineered artifact — the highest-leverage content in the system.

| Rule | Concern | Content summary |
|------|---------|----------------|
| `01_identity` | Who Co is, values, conversation awareness | Role, local-first, approval-first, multi-turn context |
| `02_intent` | Inquiry vs Directive classification | Default to inquiry; action verbs trigger directive |
| `03_safety` | Side-effect approval, credential protection, destructive caution | Approval gates, safe commands allowlist, no credentials, confirm destructive ops |
| `04_reasoning` | Ground truth, fact verification, anti-sycophancy | Trust tools, verify deterministic facts, escalate contradictions |
| `05_tool_use` | Tool invocation, context tool catalog, result presentation | Context tools, display verbatim, error reporting, verify success |
| `06_response_style` | Terseness, formatting, completeness | High-signal, no filler, complete before yielding |
| `07_workflow` | Multi-step task process | Understand → Gather → Execute → Verify |

**Aspects** provide deeper guidance the LLM loads on-demand when the conversation warrants it:

| Aspect | Category | Content summary |
|--------|----------|----------------|
| `debugging` | Situational mode | Symptom-first, hypothesis-test loops, check the obvious, verify fix |
| `planning` | Situational mode | Decompose, identify dependencies, incremental execution, verify each step |
| `code_review` | Task-specific protocol | Full diff first, correctness, edge cases, security, must-fix vs style |

**Crafting principles** (apply to both rules and aspects):
- **Every sentence must earn its place.** No filler, no hedging, no redundancy. If a sentence doesn't change the model's behavior in a measurable way, delete it
- **Be specific, not aspirational.** "Trust tool output over prior knowledge when they conflict" changes behavior. "Be helpful and accurate" does not
- **Test against real conversations.** Run the same 10-20 diverse queries before and after each edit. Diff the outputs. If behavior didn't improve, the edit was noise
- **Iterate ruthlessly.** First drafts are always too long and too vague. Cut 30% on each pass

### Personality Modulation

Co's personality system is a key differentiator — few coding tools have one, and co-cli's is the most architecturally sophisticated among CLI tools.

**Current architecture:** 5 presets, each a combination of optional character (who) + required style (how):

| Preset | Character | Style | Voice |
|--------|-----------|-------|-------|
| finch | finch (pragmatic mentor) | balanced | Patient educator, explains "why", presents tradeoffs |
| jeff | jeff (eager robot) | warm | Curious, literal, celebrates learning, narrates thinking |
| friendly | -- | warm | Collaborative "we"/"let's", supportive, occasional emoji |
| terse | -- | terse | Ultra-minimal, fragments, 1-2 sentences, no filler |
| inquisitive | -- | educational | Explores options, clarifies ambiguity, asks questions |

**Peer comparison:**

| System | Mechanism | Presets | Decomposed? | Per-task? |
|--------|-----------|---------|-------------|-----------|
| **Codex** | Enum in config, template substitution | 3 (None/Friendly/Pragmatic) | No — single string | Manual `/personality` |
| **Claude Code** | Output styles (markdown files replacing prompt sections) | 3 + custom | No — monolithic blob | Manual `/output-style` |
| **ChatGPT** | Presets + granular sliders (tone, warmth, emoji, formatting) | 8 | Yes — separate dimensions | No — always-on |
| **Copilot** | Custom chat modes (`.chatmode.md`) | 3 + custom | No | Per-conversation |
| **co-cli** | Character x style composition, on-demand loading | 5 | Yes — character + style | No — always-on |

co-cli's character x style decomposition is unique among coding tools. Only ChatGPT has comparable granularity (consumer product, not CLI tool).

**Design decisions:**

**Character x style composition, not per-aspect decomposition.** Per-behavioral-aspect personality (e.g., `identity.finch.md`, `safety.finch.md`) was considered and rejected: O(aspects x presets) file explosion (7 x 5 = 35 files today), no peer does it, maintenance burden of updating all role variants when a rule changes. Character defines *who* (voice, values, markers). Style defines *how* (format, verbosity, emoji). These two dimensions cover personality completely without coupling to behavioral rules.

**On-demand loading, not pre-assembled.** Personality is loaded by the agent via `load_personality()` tool, not baked into the system prompt. This is unique among peers (Codex and Claude Code pre-assemble personality). Tradeoff: smaller system prompt and agent autonomy vs fragile loading (agent might skip). Mitigation: `05_tool_use.md` directs "At session start, load the personality character piece first." Future: enforce via agent startup hook.

**Always-on, never per-task selective.** The user chose their personality — they want that voice consistently. No peer auto-adapts personality by task. The 2 systems with switching (Codex, Claude Code) do it manually. LLMs naturally adapt verbosity within personality constraints without explicit per-turn selection.

**response_style rule vs personality style — complementary, not overlapping.** The `response_style` rule holds universal communication constraints that apply regardless of personality (no filler, complete before yielding, high-signal). Personality style files hold variable communication preferences (terse fragments vs warm collaboration vs educational exploration). The rule is the floor; personality is the modulation above it.

**Role essays as reference, not runtime.** `roles/*.md` contains comprehensive personality guides (Finch: 209 lines, Jeff: 204 lines) documenting intent, philosophy, and examples. These are reference artifacts for personality design, not loaded at runtime. Runtime personality is lean (character ~27 lines + style ~9 lines = ~36 lines) because every sentence must earn its place — same crafting principle as rules.

### Key Design Decisions

**PromptManifest for diagnostics.** Every assembly returns a manifest with: `parts_loaded`, `total_chars`, and `warnings`. Available for debugging (`/prompt` slash command, log output) without parsing the prompt text.

**Base instructions layer.** `instructions.md` is loaded first, before all rules. It holds bootstrap identity ("You are Co") and frames the numbered rules that follow. The context tool catalog and session-start directive live in `05_tool_use.md` (a rule, always present).

**On-demand context loading.** The system prompt contains only rules-fixed content (instructions + behavioral rules + counter-steering). Everything else — aspects, personality, memories — is loaded by the agent via context tools (`load_aspect`, `load_personality`, `recall_memory`). This keeps the system prompt small and gives the agent discretion about what context to load. Novel among peers: no other system lets the agent decide when/what to load.

**Model adaptation is counter-steering only.** Counter-steering stays for models that need explicit reminding (e.g., GLM-4.7 conversation awareness). No tier-dependent aspect selection or personality scaling.

**Rules from peer convergence, aspects are novel.** The 7 rules map to categories that 10+ peer systems independently converge on. The 3 situational aspects (`debugging`, `planning`, `code_review`) are co-cli's own addition — no peer has on-demand aspect loading.

### Peer Patterns

| Pattern | Peer Evidence | co-cli |
|---------|--------------|--------|
| Base system instructions | Claude Code preamble, Codex template header, Gemini CLI constants | `instructions.md` loaded first, before rules |
| Full behavioral spec, always present | Claude Code, Codex, Gemini CLI — all send complete instructions every turn | All 7 rules in system prompt. Aspects are supplementary depth |
| Safe commands in prompt | Codex safe-command allowlists in prompt, Claude Code hook-based permission engine | Target: interpolated into safety rule (A2) |
| Model-specific adaptation | Aider model warnings, Codex quirk database | Counter-steering only |
| Prompt budget enforcement | All peers have implicit or explicit size limits | Manifest `total_chars` + contract test assertions |
| Post-hoc tool policy | All three systems enforce safety after model proposes tool call | `requires_approval=True` + safe command allowlist |
| Personality as composed block | Codex: enum. Claude Code: output styles. ChatGPT: presets + sliders | Character x style composition, on-demand loading, always-on |

### Dynamic Injection Decision (2026-02-12, revised)

Decision: restore `load_aspect()` as an on-demand prompt piece loader. No `load_context` tool — Co does not discover or load project instructions via tool; instructions are always seeded in the system prompt.

**Rules vs aspects distinction:**
- **Rules** (7 files, always in system prompt): short imperative behavioral policy (~4-5 lines each). Universal — apply to every turn. The floor.
- **Aspects** (on-demand via `load_aspect()`): deeper situational guidance the LLM loads when the conversation warrants it. The depth above the floor.

The LLM decides when and what to load. No "whole context" tool exists — `load_aspect()` and `load_personality()` are independent, fine-grained prompt piece loaders.

**Three aspect categories:**

1. **Extended rule depth.** Rules are deliberately terse for prompt budget. Some situations need more. Example: `04_reasoning.md` says "Do not agree with incorrect user claims" in one line. A `reasoning` aspect carries the fuller protocol — how to present corrections, when to show evidence vs. ask clarifying questions, handling user pushback.

2. **Situational modes.** Not every turn needs the same guidance. A `debugging` aspect loads when the user reports a bug — systematic methodology, hypothesis-test loops, "check the obvious first." A `planning` aspect loads for complex tasks — decomposition, dependency ordering, checkpoint verification. Irrelevant most turns, high-value when relevant.

3. **Task-specific protocols.** Detailed step-by-step for specific operations: code review checklist, git workflow, security-sensitive operations, multi-file refactoring. The `07_workflow` rule says "Understand → Gather → Execute → Verify" in 4 lines. A `code_review` aspect carries the actual review protocol.

**What aspects are NOT:** personality (voice/style — handled by `load_personality()`), user preferences/facts (handled by `recall_memory()`), or project instructions (always in system prompt).

Peer comparison: no peer system has on-demand aspect loading — all peers bake everything into the system prompt. Co's approach trades smaller system prompts + LLM autonomy for the risk that the LLM might skip loading. Mitigation: `05_tool_use.md` directs proactive loading.

---

## Part A: Prompt System Code

### A2. Safe commands interpolation

- [ ] In `assemble_prompt()`, interpolate `shell_safe_commands` into the safety rule at assembly time
- [ ] The model sees which commands are auto-approved alongside the approval policy

### A3. Personality loading enforcement

- [ ] Add startup hook in agent initialization that loads personality character piece before first user turn
- [ ] Alternative: keep as `05_tool_use.md` directive but add manifest tracking to detect when personality was not loaded

### A4. Manifest enhancement

- [ ] Extend `PromptManifest` with `on_demand_loaded: list[str]` field
- [ ] Context tools (`load_aspect`, `load_personality`, `recall_memory`) update manifest when called

## Part B: Tests (First Principles Redesign)

**Philosophy:** All prior prompt/memory tests were deleted — they tested an obsolete monolithic prompt or implementation details that changed. New tests are designed from the contracts that matter: what each module promises, not how it works inside.

**No mocks, no stubs.** Per project policy: functional tests that exercise real code paths with real files on disk. Use `tmp_path` fixtures for filesystem isolation.

### B1. Memory System Tests — `tests/test_memory.py`

Tests the core memory lifecycle: file I/O, dedup, consolidation, decay, search.

**Storage contract — files are the API:**
- [ ] `test_save_creates_valid_file` — save_memory creates `{id:03d}-{slug}.md` with valid YAML frontmatter (id, created, tags, source, auto_category) and markdown body
- [ ] `test_load_roundtrip` — write a memory file by hand, `_load_all_memories()` returns a MemoryEntry with matching id, content, tags, created
- [ ] `test_load_skips_malformed` — directory with one valid + one invalid file (missing `id` in frontmatter) -> loads only the valid one, no crash
- [ ] `test_load_empty_dir` — `_load_all_memories()` on an empty dir returns `[]`
- [ ] `test_load_missing_dir` — `_load_all_memories()` on a nonexistent path returns `[]`

**Dedup contract — similar content is detected:**
- [ ] `test_dedup_exact_match` — identical content -> `_check_duplicate` returns `(True, entry, 100.0)`
- [ ] `test_dedup_word_reorder` — "I prefer TypeScript" vs "TypeScript I prefer" -> detected (token_sort_ratio handles reordering)
- [ ] `test_dedup_below_threshold` — "I prefer TypeScript" vs "I prefer Python" -> not detected (different enough)
- [ ] `test_dedup_empty_corpus` — no recent memories -> `(False, None, 0.0)`

**Consolidation contract — dedup triggers in-place update:**
- [ ] `test_consolidation_merges_tags` — existing memory with tags `[a, b]` + new memory with tags `[b, c]` -> result has tags `[a, b, c]`
- [ ] `test_consolidation_sets_updated` — consolidated memory has `updated` field in frontmatter
- [ ] `test_consolidation_replaces_content` — body text is replaced with new content

**Decay contract — overflow triggers cleanup:**
- [ ] `test_decay_triggered_at_limit` — set `memory_max_count=3`, save 4th memory -> one memory file is deleted
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
- [ ] `test_list_empty` — no memories -> count=0
- [ ] `test_list_shows_all` — save 3 memories -> list returns count=3 with correct IDs

**Helpers:**
- [ ] `test_slugify_basic` — "Hello World" -> "hello-world"
- [ ] `test_slugify_special_chars` — "cafe @#! thing" -> "caf-thing" (strips specials, truncates at 50)
- [ ] `test_detect_source_signal_tags` — tags containing "preference" -> "detected"
- [ ] `test_detect_source_no_signal` — tags like ["python"] -> "user-told"
- [ ] `test_detect_category` — first matching category tag is returned

### B2. Memory Loading Tests — `tests/test_memory_loading.py`

Tests the always-on context loading pipeline (`knowledge.py` — background memory in the 3-tier model): file discovery, frontmatter validation, size enforcement.

**Loading contract:**
- [ ] `test_no_files_returns_none` — neither global nor project context.md exists -> `None`
- [ ] `test_global_only` — only `~/.config/co-cli/knowledge/context.md` exists -> returns content under `### Global Context` heading
- [ ] `test_project_only` — only `.co-cli/knowledge/context.md` exists -> returns content under `### Project Context` heading
- [ ] `test_both_combined` — both exist -> combined output has both `### Global Context` and `### Project Context` sections
- [ ] `test_frontmatter_stripped` — context.md with valid frontmatter -> frontmatter not in output body

**Validation contract:**
- [ ] `test_context_frontmatter_valid` — `{version: 1, updated: "2026-02-11T00:00:00Z"}` passes validation
- [ ] `test_context_frontmatter_missing_version` — raises ValueError
- [ ] `test_context_frontmatter_missing_updated` — raises ValueError
- [ ] `test_memory_frontmatter_valid` — `{id: 1, created: "2026-02-11T00:00:00Z"}` passes
- [ ] `test_memory_frontmatter_missing_id` — raises ValueError
- [ ] `test_memory_frontmatter_bad_tags_type` — `tags: "not-a-list"` raises ValueError
- [ ] `test_malformed_yaml_returns_empty` — `---\n: :\n---\nbody` -> `parse_frontmatter` returns `({}, content)`

**Size enforcement:**
- [ ] `test_soft_limit_warns` — memory between 10-20 KiB -> warning to stderr, content not truncated
- [ ] `test_hard_limit_truncates` — memory over 20 KiB -> truncated to 20 KiB

### B3. Personality Tests — `tests/test_personality.py`

Tests the composable personality system: registry, on-demand loading, role essays.

**Registry contract — all presets are valid:**
- [ ] `test_all_presets_have_aspect_files` — every entry in PRESETS has a valid style file; presets with character!=None have a valid character file
- [ ] `test_valid_personalities_matches_presets` — `VALID_PERSONALITIES` list matches `PRESETS.keys()`

**On-demand loading contract** (mostly covered in `test_context_tools.py`)**:**
- [ ] `test_load_unknown_preset` — unknown role name returns error display, pieces_loaded=[]

**Role essays — baseline reference preserved:**
- [ ] `test_role_essays_exist` — `roles/{name}.md` exists for every preset name in PRESETS
- [ ] `test_role_essays_nonempty` — each role essay has > 100 chars of content

### B4. Prompt Assembly Tests — `tests/test_prompt_assembly.py`

Tests the assembled system prompt: part presence, ordering, budget, manifest.

Part presence, budget, and context tool tests covered in `test_prompt_assembly.py` (11 tests) and `test_context_tools.py` (14 tests). Remaining:

- [ ] `test_assemble_prompt_returns_manifest` — `assemble_prompt(provider, model_name)` returns `(str, PromptManifest)` tuple
- [ ] `test_prompt_nonempty` — all provider/model combos produce non-empty prompt

## Part C: Doc Consolidation

### C1. Consolidated review

- [ ] Write `docs/REVIEW-prompt-system-redesign-2026-02-11.md`
  - Merges content from 3 source docs (landscape, peer convergence, prompt construction)
  - Sections: executive verdict, landscape positioning, peer patterns, current implementation, gap resolutions, differentiators, sources

### C2. Design doc

- [ ] Write `docs/DESIGN-16-prompt-system.md` (4-section template)
  - What & How: prompt composition with diagram (rules-fixed + on-demand)
  - Core Logic: assembly algorithm, on-demand context tools, personality composition, manifest
  - Config: personality setting, model quirks
  - Files: all prompt system files

### C3. Delete source review docs

- [ ] Delete `docs/REVIEW-co-cli-prompt-construction-and-crafting-2026-02-11.md`
- [ ] Delete `docs/REVIEW-co-prompt-structure-converged-peer-systems-2026-02-10.md`
- [ ] Delete `docs/REVIEW-agentic-cli-landscape-2026.md`

## Execution Order

1. A2 + A3 + A4 (safe commands, personality enforcement, manifest — can parallelize)
2. B1 + B2 + remaining B3 (tests, can parallelize)
3. C1 + C2 (docs, can parallelize)
4. C3 (delete source review docs)
5. Run full test suite, verify `co status`

## Verification

```bash
uv run pytest tests/test_prompt_assembly.py -v
uv run pytest tests/test_memory.py -v
uv run pytest -v
uv run co status
uv run python -c "
from co_cli.prompts import assemble_prompt
prompt, manifest = assemble_prompt('gemini', model_name='gemini-2.0-flash')
print(f'Parts: {manifest.parts_loaded}')
print(f'Chars: {manifest.total_chars}')
print(f'Warnings: {manifest.warnings}')
"
```
