# FIX: Mindset Cartridge Selection + Per-Turn Critique

## Context

`load_task_strategy` was registered as an agent tool and directed via `04_tool_protocol.md`. Eval
confirms it never fires — qwen3 thinking models bypass behavioral rules, going directly to
task-answering tools. The inner monologue never touches the rule; it resolves "what tool answers
this question?" independently.

Two structural problems, not a wording problem:

1. **Selection is optional** — model compliance for prerequisite tool calls is unreliable for
   local/instruct-only models. Letta V1 made the same observation: `InitToolRule` (forced first
   tool) was their solution before modern Claude/GPT compliance made it unnecessary. qwen3 is not
   Claude.
2. **No active persistence** — even if strategy were loaded, nothing keeps the soul's interpretive
   frame active while the model reasons through subsequent tool results.

---

## Design

Two mechanisms. They are **fully independent** — different sources, different lifecycles, no shared
state.

### Mechanism 1 — Forced mindset declaration (pre-turn phase)

`load_task_strategy` is removed entirely from the agent's tool list.

Mindset selection happens as a **mandatory pre-turn classification step** in `_orchestrate.py`,
before the main response run. The orchestrator calls:

```python
agent.run(user_message, output_type=MindsetDeclaration, message_history=[])
```

`MindsetDeclaration` is a Pydantic model with a single field:

```python
class MindsetDeclaration(BaseModel):
    task_types: Annotated[
        list[Literal["technical", "exploration", "debugging", "teaching", "emotional", "memory"]],
        Field(min_length=1),
    ]
```

**Why `output_type` enforces the pick**: when `output_type` is a Pydantic model, pydantic-ai
registers a special final-result tool. The model must call it — text-only responses are rejected.
The `min_length=1` constraint means an empty list fails pydantic validation, triggering a retry
with a validation error message. At least one cartridge type is always selected.

**Why `prepare_tools` alone is insufficient**: `prepare_tools` restricts which tools appear in the
schema, but `output_type=[str, DeferredToolRequests]` still lets the model reply with plain text,
bypassing all tools. `output_type=MindsetDeclaration` removes that escape — the only valid response
is the structured classification.

**Loading is system-internal**: the model returns task type tokens; the orchestrator calls
`_apply_mindset(deps, task_types)` which reads the strategy files and sets
`deps.active_mindset_content`. The model never sees file paths or reads files. It classifies; the
system loads.

**One extra LLM call per session**: lightweight — small context (`message_history=[]`), tiny output
(JSON list of strings). The soul seed is in the system prompt so the model classifies in character.
Runs only when `deps.mindset_loaded == False`.

**Timing**: `_apply_mindset` runs before the main `run_turn()` call. `deps.active_mindset_content`
is set before the main run starts. `inject_active_mindset` fires at main run start and already sees
the content — `## Active mindset:` is in the system prompt for Turn 1's main response.

### Mechanism 2 — Always-on soul critique (standalone)

A **role-specific interpretive frame** injected into the system prompt on every model call,
regardless of task type, regardless of whether mindset selection has occurred. It is not derived
from mindset content and has no coupling to Mechanism 1.

Source: `souls/{role}/critique.md` — a short paragraph describing how the soul reviews and reasons
about any information it encounters. Loaded once at session start (same pattern as soul seed),
stored as `CoDeps.personality_critique`. Injected by `inject_personality_critique` whenever the
field is non-empty.

The sole condition is whether the soul has a critique file — not `mindset_loaded`, not any
Mechanism 1 state. It fires independently: from Turn 0 onward if loaded, across all tool-result
reasoning steps, regardless of what task type was selected or whether any mindset was applied.

**No extra LLM call**: `@agent.system_prompt` functions are pure Python — called once at
`agent.run()` start to build the system prompt string. Zero overhead beyond string concatenation.

**How it reaches tool-result reasoning**: the system prompt is re-attached on every API call within
a run, including calls made after tool results (step 2, 3, ...). For qwen3 thinking models, the
thinking process reads the full context — system prompt, prior messages, tool results — in sequence.
The critique section is in the system prompt on every step, coloring how the model interprets every
tool result it processes.

---

## Implementation

### Step 1 — Add fields to `CoDeps` (`co_cli/deps.py`)

Under `# Personality / role`:

```python
# Mechanism 1 — per-session mindset selection
mindset_loaded: bool = False
active_mindset_types: list[str] = field(default_factory=list)
active_mindset_content: str = ""

# Mechanism 2 — always-on soul critique (loaded from souls/{role}/critique.md)
personality_critique: str = ""
```

### Step 2 — Add `load_soul_critique` to `_composer.py` (`co_cli/prompts/personalities/_composer.py`)

```python
def load_soul_critique(role: str) -> str:
    """Read souls/{role}/critique.md if it exists, return empty string otherwise."""
    path = _SOULS_DIR / role / "critique.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""
```

### Step 3 — Load critique in `main.py`

Alongside the existing `load_soul_seed(personality)` call, add:

```python
personality_critique = load_soul_critique(settings.personality)
```

Pass to `CoDeps`:

```python
deps = CoDeps(..., personality_critique=personality_critique)
```

### Step 4 — Write `souls/{role}/critique.md` (2 new files)

One file per role. Short, always-applicable interpretive stance — not task-type-specific.

`souls/finch/critique.md`:
```
When reviewing any result: Does the structure hold? Name what matters, cut what doesn't.
State tradeoffs before conclusions. If something is missing, name the gap.
```

`souls/jeff/critique.md`:
```
When reviewing any result: Does this feel like a discovery or a report? Stay present
with what you found. Name what surprised you. What does this open up?
```

### Step 5 — Add `MindsetDeclaration` and `_apply_mindset` to `co_cli/tools/personality.py`

```python
from typing import Annotated, Literal
from pydantic import BaseModel, Field

MINDSET_TYPES = Literal["technical", "exploration", "debugging", "teaching", "emotional", "memory"]

class MindsetDeclaration(BaseModel):
    task_types: Annotated[list[MINDSET_TYPES], Field(min_length=1)]
```

`_apply_mindset(deps: CoDeps, task_types: list[str]) -> None` — plain function, not a tool:
- Reads `strategies/{role}/{task_type}.md` for each type (no frontmatter, raw content)
- Sets `deps.active_mindset_content = "\n\n".join(parts)`
- Sets `deps.active_mindset_types = loaded_types`
- Sets `deps.mindset_loaded = True`

Remove `load_task_strategy` tool: the `@agent.tool()` decorator and function are gone.

### Step 6 — Add pre-turn classification phase to `_orchestrate.py`

In `run_turn()`, before the main streaming call:

```python
if deps.personality and not deps.mindset_loaded:
    mindset_result = await agent.run(
        user_message,
        output_type=MindsetDeclaration,
        message_history=[],
    )
    _apply_mindset(deps, mindset_result.output.task_types)
```

Import `MindsetDeclaration` and `_apply_mindset` from `co_cli.tools.personality`.

### Step 7 — Add two `@agent.system_prompt` functions to `agent.py`

After `add_personality_memories`, add both — independently:

```python
@agent.system_prompt
def inject_active_mindset(ctx: RunContext[CoDeps]) -> str:
    """Mechanism 1: task-specific mindset content, set by pre-turn classification."""
    if not ctx.deps.active_mindset_content:
        return ""
    types = ", ".join(ctx.deps.active_mindset_types)
    return f"\n## Active mindset: {types}\n\n{ctx.deps.active_mindset_content}"


@agent.system_prompt
def inject_personality_critique(ctx: RunContext[CoDeps]) -> str:
    """Mechanism 2: always-on soul critique, loaded from souls/{role}/critique.md."""
    if not ctx.deps.personality_critique:
        return ""
    return f"\n## Review lens\n\n{ctx.deps.personality_critique}"
```

Remove `load_task_strategy` registration from `agent.py`.

### Step 8 — Remove `## Personality strategy` from `04_tool_protocol.md`

Redundant — enforcement is now structural. Recover ~280 chars.

### Step 9 — Update `DESIGN-02-personality.md`

- **Section 2c**: replace `load_task_strategy` with the two-mechanism design; describe
  `MindsetDeclaration`, `_apply_mindset`, `load_soul_critique`, and both `@agent.system_prompt`
  functions; note the two mechanisms are fully independent
- **Prompt layer map**: replace "On-demand tool context / load_task_strategy" with
  "Orchestrator pre-turn: mindset classification (Mechanism 1)"; add both `inject_active_mindset`
  and `inject_personality_critique` to the per-turn layers
- **Rule 04 row**: remove strategy directive note
- **Files table**: remove `load_task_strategy`; add `souls/{role}/critique.md`,
  `_apply_mindset`, `MindsetDeclaration`, `load_soul_critique`
- **Budget table**: add "Active mindset content ~200–600 chars per-turn (Mechanism 1)" and
  "Soul critique ~100–150 chars per-turn (Mechanism 2)"

---

## Files Touched

| File | Action |
|------|--------|
| `co_cli/deps.py` | Add `mindset_loaded`, `active_mindset_types`, `active_mindset_content`, `personality_critique` |
| `co_cli/prompts/personalities/_composer.py` | Add `load_soul_critique(role)` |
| `co_cli/prompts/personalities/souls/finch/critique.md` | New file — Finch's review lens |
| `co_cli/prompts/personalities/souls/jeff/critique.md` | New file — Jeff's review lens |
| `co_cli/tools/personality.py` | Add `MindsetDeclaration`, `_apply_mindset`; remove `load_task_strategy` |
| `co_cli/_orchestrate.py` | Add pre-turn classification block in `run_turn()` |
| `co_cli/agent.py` | Add `inject_active_mindset` + `inject_personality_critique`; remove `load_task_strategy` registration |
| `main.py` | Load `personality_critique` via `load_soul_critique()`; pass to `CoDeps` |
| `co_cli/prompts/rules/04_tool_protocol.md` | Remove `## Personality strategy` section |
| `docs/DESIGN-02-personality.md` | Update section 2c, prompt layer map, rule table, files table, budget table |

---

## Verification

```bash
# 1. All tests pass
uv run pytest tests/ -v

# 2. Eval — confirm pre-turn classification fires, both injections active in Turn 1
uv run python evals/eval_personality_behavior.py --case-id finch-db-tradeoffs
uv run python evals/eval_personality_behavior.py --case-id jeff-codebase-structure
```

Expected in traces:
- Turn 1: **two model calls** — pre-turn classification (returns `MindsetDeclaration`) then main
  response
- Turn 1 system prompt: `## Active mindset: {types}` present (Mechanism 1, set before main run)
- Turn 1 system prompt: `## Review lens` present (Mechanism 2, loaded at session start,
  independent of mindset)
- Turn 2+: both sections still present; no classification call (already loaded)
- All heuristic checks still PASS
