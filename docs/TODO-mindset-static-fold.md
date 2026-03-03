# TODO: Fold Mindset Content into Static Seed

Task type: `refactor`

---

## 1. Context

The personality system has two subsystems: a static soul assembly (run once at agent creation) and a per-turn classification mechanism called `MindsetDeclaration`. The classification mechanism was introduced to load task-specific behavioral guidance ("mindsets") matched to the first user turn. It fires an extra `agent.run()` call before Turn 1, classifies the input as one or more of 6 task types, and injects the selected mindset files into every subsequent system prompt via `inject_active_mindset`.

**Relevant source files:**
- `co_cli/prompts/personalities/_composer.py` — `load_soul_seed`, `load_soul_examples`, `load_soul_critique`, `load_character_memories`, `validate_personality_files`
- `co_cli/tools/personality.py` — `MindsetDeclaration`, `_apply_mindset`, `MINDSET_TYPES`, `_load_personality_memories`
- `co_cli/agent.py:253-258` — `inject_active_mindset` `@agent.system_prompt` function
- `co_cli/_orchestrate.py:478-487` — pre-turn classification block in `run_turn()`
- `co_cli/deps.py:62-65` — `mindset_loaded`, `active_mindset_types`, `active_mindset_content`
- `evals/_common.py:413-415` — judge deps override strips `active_mindset_content` and `active_mindset_types`
- `tests/test_prompt_assembly.py:180-231` — tests for mindset file existence and validation warnings
- `docs/DESIGN-personality.md` — primary doc covering this mechanism
- `docs/DESIGN-core.md:193` — CoDeps table references the 3 mindset fields
- `docs/DESIGN-prompt-design.md:127` — references active mindset per-turn layer

**Measured content size:**
Sampling mindset files for `finch` role: ~320–400 chars each × 6 task types = **~2,100 chars** per role total. All 18 files (6 × 3 roles) — only the active role's 6 are loaded per session.

**Current budget test thresholds** (must be updated):
- `test_static_prompt_under_budget`: `< 6500` chars — prompt without soul seed, unchanged
- `test_total_prompt_under_budget`: `< 8000` chars — prompt with soul seed; **will exceed after fold** (~+2,100 chars)

---

## 2. Problem & Outcome

**Problem:**

1. **Extra LLM call at Turn 1.** `agent.run(output_type=MindsetDeclaration)` fires before the first user response. First-turn latency is the most perceptible CLI cost. The payoff: selecting 1–2 of 6 static files, each ~5 bullets, ~350 chars total.
2. **Session-lock staleness.** Classified once from the Turn-1 message, the mindset never updates. Conversations naturally shift task type (`exploration` → `technical` → `debugging`). The classification is accurate for one turn and increasingly stale after.
3. **High machinery-to-content ratio.** One Pydantic model (`MindsetDeclaration`), one function (`_apply_mindset`), 3 `CoDeps` fields, 1 `@agent.system_prompt` function, and a pre-turn block in `run_turn()` — to deliver ~350 chars of static text once per session.

**Outcome:**

All 6 mindset files for the active role are loaded as part of the static soul block at agent creation (zero extra LLM calls, always-current task coverage). The classification mechanism and its supporting code are deleted.

Behavioral outcome is preserved or improved: the model always sees all task-type guidance from Turn 1, regardless of how the conversation evolves.

---

## 3. Scope

**In scope:**
- Add `load_soul_mindsets(role)` to `_composer.py`
- Update `get_agent()` to call `load_soul_mindsets` and fold result into soul block
- Delete `MindsetDeclaration`, `_apply_mindset`, `MINDSET_TYPES` from `co_cli/tools/personality.py`
- Delete `inject_active_mindset` from `agent.py`
- Delete the pre-turn classification block from `run_turn()` in `_orchestrate.py`
- Remove `mindset_loaded`, `active_mindset_types`, `active_mindset_content` from `CoDeps`
- Clean up `evals/_common.py` judge-deps override
- Update budget threshold in `test_prompt_assembly.py`
- Update `DESIGN-personality.md` and `DESIGN-core.md`

**Out of scope:**
- Mindset file content (no changes to `mindsets/{role}/*.md`)
- Critique mechanism (`souls/{role}/critique.md`, `inject_personality_critique`, `personality_critique` in `CoDeps`) — keep as-is
- Character base memories — keep as-is
- Soul examples — keep as-is
- Per-turn mindset re-classification (deferred; revisit if task-shift use cases justify it)

---

## 4. High-Level Design

### Static prompt layout after change

```
souls/{role}/seed.md          ← identity + Core + Never
## Character                  ← base memories (existing)
## Mindsets                   ← all 6 task type files (NEW — static, always present)
rules/01..05_*.md
## Response patterns          ← examples (existing)
quirks/{provider}/{model}.md
```

`## Mindsets` is assembled by `load_soul_mindsets(role)`: reads all 6 files in `REQUIRED_MINDSET_TASK_TYPES` order (each already has its own `## TypeName` subheader), joins with `\n\n`, wraps in a `## Mindsets\n\n` outer header.

### Wire-in in `get_agent()`

```python
# after load_character_memories:
soul_mindsets = load_soul_mindsets(personality)
if soul_mindsets:
    soul_seed = soul_seed + "\n\n" + soul_mindsets
```

Consistent with how `load_character_memories` is currently folded into `soul_seed`.

### `_composer.py` additions

`load_soul_mindsets(role)` — reads `mindsets/{role}/{task_type}.md` for all 6 types in `REQUIRED_MINDSET_TASK_TYPES` order. Skips missing files silently (consistent with existing degraded-but-functional policy). Returns `"## Mindsets\n\n" + joined content`, or `""` if none found.

### `validate_personality_files()` — no change needed

Already validates all 6 mindset files per role. The validation contract is unchanged.

### `personality.py` after change

Only `_load_personality_memories()` remains. `MindsetDeclaration`, `_apply_mindset`, `MINDSET_TYPES`, and `_MINDSETS_DIR` are deleted. The module docstring is updated.

---

## 5. Implementation Plan

### TASK-1 — Add `load_soul_mindsets()` to `_composer.py`

**files:**
- `co_cli/prompts/personalities/_composer.py`

**done_when:** `load_soul_mindsets("finch")` returns a string starting with `"## Mindsets"` that includes all 6 task-type subheaders (`## Technical`, `## Exploration`, `## Debugging`, `## Teaching`, `## Emotional`, `## Memory`), and returns `""` for an unknown role without raising.

---

### TASK-2 — Wire `load_soul_mindsets` into `get_agent()` soul block

**files:**
- `co_cli/agent.py`

**done_when:** When `personality="finch"`, the static system prompt assembled by `get_agent()` contains the string `"## Mindsets"` before the first behavioral rule content. Verify by calling `assemble_prompt` directly with `soul_seed` that includes mindsets — the section appears.

---

### TASK-3 — Delete classification machinery

Remove in one atomic change to avoid a broken intermediate state:
- `MindsetDeclaration`, `_apply_mindset`, `MINDSET_TYPES`, `_MINDSETS_DIR` from `co_cli/tools/personality.py`
- `inject_active_mindset` function and its registration from `co_cli/agent.py`
- Pre-turn classification block (`_orchestrate.py:478-487`) and the `from co_cli.tools.personality import MindsetDeclaration, _apply_mindset` import

**files:**
- `co_cli/tools/personality.py`
- `co_cli/agent.py`
- `co_cli/_orchestrate.py`

**done_when:** `grep -r "MindsetDeclaration\|_apply_mindset\|inject_active_mindset\|mindset_loaded" co_cli/` returns no matches.

---

### TASK-4 — Remove 3 mindset fields from `CoDeps`

**files:**
- `co_cli/deps.py`

**done_when:** `CoDeps` has no fields named `mindset_loaded`, `active_mindset_types`, or `active_mindset_content`. `grep "active_mindset\|mindset_loaded" co_cli/deps.py` returns no matches.

---

### TASK-5 — Clean up `evals/_common.py` judge-deps override

Remove the two lines that explicitly clear `active_mindset_content=""` and `active_mindset_types=[]` from the `dataclass_replace` call in the judge-deps builder.

**files:**
- `evals/_common.py`

**done_when:** `grep "active_mindset" evals/_common.py` returns no matches. The eval still imports and uses `deps` cleanly without referencing deleted fields.

---

### TASK-6 — Update tests and budget threshold

- Update module docstring in `test_prompt_assembly.py` (remove MindsetDeclaration reference)
- Update `test_total_prompt_under_budget` threshold: measure actual max across all roles after fold and set threshold to `actual_max + 20%` headroom (expected ~10,500 based on ~2,100 chars mindset + ~8,000 existing)
- Keep `test_all_roles_have_mindset_files`, `test_mindset_files_have_content`, `test_validate_personality_files_warns_on_missing_mindset` — they still validate file presence and content, which remains required for static loading

**files:**
- `tests/test_prompt_assembly.py`

**done_when:** `uv run pytest tests/test_prompt_assembly.py` passes with no failures.

---

### TASK-7 — Update `DESIGN-personality.md` and `DESIGN-core.md`

- `DESIGN-personality.md`: remove MindsetDeclaration classification sections (§2c pre-turn classification block, §2h "Structural delivery for identity; orchestrator delivery for mindset" design decision, all references to `inject_active_mindset`, `active_mindset_content`, `active_mindset_types`, `mindset_loaded`). Update prompt layer map to show `## Mindsets` in the static block. Update session state table (remove 3 fields). Update files table. Update budget table (mindsets move from per-turn to static). Update "Adding a new role" steps.
- `DESIGN-core.md:193`: update the Personality row in the CoDeps table to remove the 3 deleted fields.

**files:**
- `docs/DESIGN-personality.md`
- `docs/DESIGN-core.md`

**done_when:** Neither doc contains the strings `MindsetDeclaration`, `inject_active_mindset`, `active_mindset_content`, `active_mindset_types`, or `mindset_loaded`. The prompt layer map in DESIGN-02 shows `## Mindsets` in the static block section.

---

## 6. Testing

**Existing tests that must pass after all tasks complete:**
- `uv run pytest tests/test_prompt_assembly.py` — full suite including mindset file existence, content, validation warnings, and updated budget threshold
- `uv run pytest tests/test_agent.py` — no mindset-related changes but validates agent construction still works
- `uv run pytest` — full suite (no new failures introduced)

**Personality behavior eval (smoke check):**
- `uv run python evals/eval_personality_behavior.py` — behavior should be preserved or improved (model now sees all task guidance from Turn 1)

**No new tests required** — this is a deletion+fold refactor. The existing budget test updated in TASK-6 serves as the primary regression gate for the static assembly change.

---

## 7. Open Questions

None. All implementation decisions are answerable from existing source:

- **Where to fold**: `get_agent()` soul block construction (consistent with `load_character_memories` pattern)
- **Section header**: `## Mindsets` wrapper with each file's existing `## TypeName` subheader — already established by the file format
- **Budget threshold**: measure after TASK-2, update in TASK-6
- **Missing files**: skip silently, consistent with existing `load_character_memories` and `_apply_mindset` degraded-but-functional policy

---

# Audit Log

## Cycle C1 — Team Lead

Submitting for Core Dev review.
