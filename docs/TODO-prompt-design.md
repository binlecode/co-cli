# TODO: Soul-First Prompt Redesign

Revise prompt system from tool-agent rules to personal companion rules. Introduce soul seed (always-on personality fingerprint) and revise 8 rules to 6.

---

## Part 1: Soul seed in personality registry ✅

- [x] 1a. Soul seed as markdown files in `personalities/seed/*.md` (decoupled from code)
- [x] 1a. Populate seed files for all 5 presets (finch, jeff, friendly, terse, inquisitive)
- [x] 1b. Add `get_soul_seed(name)` function in `_composer.py` — loads from `seed/{name}.md`
- [x] Flatten `personalities/aspects/` → move `character/`, `style/`, `seed/` up to `personalities/`

## Part 2: Inject soul seed into prompt assembly ✅

- [x] 2a. Add `personality: str | None = None` parameter to `assemble_prompt()` in `__init__.py`
- [x] 2a. Insert soul seed between instructions and rules with `## Soul` heading + framing text
- [x] 2a. Record `"soul_seed"` in manifest.parts_loaded
- [x] 2b. Update `instructions.md` — "personal companion for knowledge work"
- [x] 2c. Pass `personality=settings.personality` in `agent.py` call to `assemble_prompt()`
- [x] 2c. Pass `personality=settings.personality` in `_commands.py` `/model` switch

## Part 3: Revise rules (8 → 6) ✅

- [x] Delete `02_intent.md` (folded into identity)
- [x] Delete `07_response_style.md` (replaced by soul seed)
- [x] Rewrite `01_identity.md` — relationship, emotion, soft intent, multi-turn
- [x] Renumber `03_safety.md` → `02_safety.md` (content unchanged)
- [x] Renumber `04_reasoning.md` → `03_reasoning.md` (content unchanged)
- [x] Rename+edit `05_tool_use.md` → `04_tool_protocol.md` (add depth guidance)
- [x] Rewrite `06_context.md` → `05_context.md` (proactive memory, persist step)
- [x] Trim+renumber `08_workflow.md` → `06_workflow.md` (absorb "complete before yielding", add non-task note)
- [x] Verified against first principles: 6 traits, 5 pillars, safety boundary, no conflicts

## Part 4: Tests ✅

- [x] Update `test_prompt_starts_with_instructions` — "personal companion"
- [x] Update `test_prompt_contains_all_rules` — 6 rules, new IDs
- [x] Replace `test_prompt_has_no_personality` → `test_prompt_contains_soul_seed`
- [x] Add `test_prompt_soul_seed_absent_without_personality`
- [x] Add `test_soul_seed_framing_present`
- [x] Add `test_soul_seed_swaps_with_personality`
- [x] Add `test_deleted_rules_absent`
- [x] Update `test_manifest_parts_match` — new rule IDs + soul_seed
- [x] Add `test_all_presets_have_soul_seed`
- [x] Add `test_get_soul_seed_returns_string`
- [x] 17 prompt tests + 14 context tools tests = 31 passed

## Personality-Rule Interaction Model

| Rule | Can Personality Modify? |
|---|---|
| 01_identity | MODULATES — shapes how needs-understanding is expressed |
| 02_safety | NEVER — approval gates are absolute |
| 03_reasoning | NEVER — factual accuracy is absolute |
| 04_tool_protocol | MODULATES — depth guidance adapts per personality |
| 05_context | MODULATES — proactive recall frequency varies |
| 06_workflow | MODULATES — step explanation depth varies |

Soul seed framing: "Your personality shapes how you follow the rules below. It never overrides safety or factual accuracy."

## Files Changed

| File | Change |
|------|--------|
| `co_cli/prompts/personalities/_registry.py` | Docstring updated (soul seed loaded from file by convention) |
| `co_cli/prompts/personalities/_composer.py` | Add `get_soul_seed(name)`, update paths for flattened layout |
| `co_cli/prompts/personalities/seed/*.md` | NEW — 5 soul seed files |
| `co_cli/prompts/personalities/aspects/` | REMOVED — flattened to `character/`, `style/`, `seed/` at top level |
| `co_cli/prompts/__init__.py` | Add `personality` param, inject soul seed |
| `co_cli/prompts/instructions.md` | "personal companion for knowledge work" |
| `co_cli/prompts/rules/*.md` | 8 rules → 6 rules (atomic swap) |
| `co_cli/agent.py` | Pass `personality=settings.personality` |
| `co_cli/_commands.py` | Pass `personality=settings.personality` |
| `co_cli/tools/context.py` | Update paths for flattened personality layout |
| `tests/test_prompt_assembly.py` | Rewritten: 17 tests for 6 rules + soul seed |
