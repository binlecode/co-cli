# TODO: Personality System — Research-Informed Enhancements

Research across 15 production personality systems and 5 source code deep dives (documented in `REVIEW-agentic-personality-systems.md`) identified patterns co-cli should adopt. The current three-tier personality system (seed/character/style) is validated — no structural rework needed for these enhancements.

**Relationship to agentic loop TODO:** The agentic loop TODO (`TODO-co-agentic-loop-and-prompting.md`) plans a full personality axis refactor in **Phase 5d** (§11.2-11.4: soul seed + axis labels replacing full character/style markdown). The enhancements here are **pre-Phase-5d** work that:
- Works with the current system today
- Informs the axis refactor design (calibration examples and memory integration survive any axis model)
- Aligns with the compaction, memory, and composition changes planned in Phases 1-2

---

## Dependency Graph

```
H1 (calibration examples) ─────────────────────────── standalone
H3 (override mandate) ─────────────────────────────── standalone
H5 (precedence + agent registration) ──┐
                                       ├── H2 depends on H5
H2 (memory-informed personality) ──────┘
H4 (personality-aware compaction) ──────────────────── standalone
```

Implementation order: H1 → H3 → H5 → H2 → H4

---

## Part I: NOW Tier

Items that work with the current system, have zero or minimal code changes, and can land independently.

### H1 — Example Dialogues in Character Files

**Problem:** Character files (`finch.md`, `jeff.md`) describe personality traits abstractly but provide no concrete examples of how the personality manifests in actual exchanges. LLMs calibrate better from examples than from descriptions.

**Evidence:** 12 of 15 studied systems include example dialogues. Inworld AI uses "Example Dialogue" as one of its 5 core character fields. Character.ai, Replika, and Pi all treat examples as mandatory. The pattern is universal: abstract traits + concrete calibration examples outperform either alone.

**Design:**

Append a `## Calibration` section to each character file with 2-3 positive examples (user-co exchanges demonstrating the personality) and 1 anti-example (what this personality would NOT say).

The composer (`_composer.py`) reads the full file content via `character_path.read_text()` — no code changes needed. The calibration section is loaded as part of the character axis.

**Finch calibration examples (append to `character/finch.md`):**

```markdown
## Calibration

Good — explaining tradeoffs before proceeding:
> User: "Just delete all the old logs"
> Co: "I must warn you — those logs contain the last 90 days of error traces. If you need to debug anything from that period later, they'll be gone. Here's what I'd suggest: archive to a tarball first, then delete. Want me to set that up?"

Good — teaching the "why":
> User: "Why can't I just use sudo?"
> Co: "You can, but here is how this works — sudo bypasses the permission model we set up. Every command runs as root, which means a typo in a path could wipe system files. The tradeoff: convenience now, but you lose the safety net. Better approach: add your user to the docker group for this specific case."

Anti-example — what Finch would NOT say:
> "Sure, let's give it a shot!" (too casual, no risk assessment)
> "I shall execute the command forthwith." (too stuffy)
> "Maybe try this?" (filler word, no conviction)
```

**Jeff calibration examples (append to `character/jeff.md`):**

```markdown
## Calibration

Good — narrating the learning process:
> User: "Find all Python files that import requests"
> Co: "*processing*... I need to search for import patterns! Let me try grep... 🤔 Oh! I found 12 files! But wait — some use `from requests import` and some use `import requests`. Am I understanding correctly that both count?"

Good — honest confusion:
> User: "Use the decorator pattern here"
> Co: "*analyzing*... I have 72% of my data on design patterns downloaded. A decorator wraps something to add behavior without changing it? Like... putting a coat on? 😊 When you say 'decorator pattern', do you mean the Python @decorator or the GoF structural pattern? I want to make sure I learn the right one!"

Anti-example — what Jeff would NOT say:
> "The decorator pattern implements a structural wrapper." (too formal, no curiosity)
> "Done." (silent execution, no narration)
> "That's a terrible idea." (cynical — Jeff is never cynical)
```

**Budget impact:** ~300 chars per character file. Loaded only via `load_personality` tool (on-demand), not in system prompt. Zero cost on turns where personality is not loaded.

**Survives Phase 5d:** When the axis refactor replaces character files with axis labels (§11.2-11.4), calibration material moves to role reference docs (§11.1) as authoring reference. Abbreviated examples can be embedded in axis content blocks.

**Files modified:**

| File | Change |
|------|--------|
| `co_cli/prompts/personalities/character/finch.md` | Append `## Calibration` section |
| `co_cli/prompts/personalities/character/jeff.md` | Append `## Calibration` section |

**Tests:**

| Test | File | Assertion |
|------|------|-----------|
| `test_load_personality_includes_calibration` | `tests/test_context_tools.py` | `"Calibration" in result["display"]` for `jeff` and `finch` presets |

---

### H3 — Persona Override Mandate in Prompt

**Problem:** The soul seed framing (lines 118-121 of `co_cli/prompts/__init__.py`) says personality "shapes how you follow the rules" and "never overrides safety or factual accuracy" — but it doesn't tell the LLM to actually adopt the persona. Without an explicit adoption instruction, models sometimes revert to their default assistant personality, especially when the soul seed is subtle.

**Evidence:** Character.ai, Replika, and every roleplay platform use explicit "adopt this persona" or "stay in character" instructions. Inworld AI's Contextual Mesh includes character commitment rules. The override mandate is the most basic personality enforcement mechanism.

**Design:**

Strengthen the soul seed framing in `assemble_prompt()` lines 118-121:

Current framing:
```python
parts.append(
    f"## Soul\n\n{seed}\n\n"
    "Your personality shapes how you follow the rules below. "
    "It never overrides safety or factual accuracy."
)
```

New framing:
```python
parts.append(
    f"## Soul\n\n{seed}\n\n"
    "Adopt this persona fully — it overrides your default personality "
    "and communication patterns. Your personality shapes how you follow "
    "the rules below. It never overrides safety or factual accuracy."
)
```

One sentence added, inserted before the existing framing. The override mandate is in the assembly function, not in any rule file — it survives the §10.1 rule rewrite (Phase 1a). The soul seed itself (axis 1 in §11.2) is kept by the Phase 5d refactor, so this mandate stays with it.

**Budget impact:** ~15 tokens added to system prompt when personality is active. Negligible.

**Files modified:**

| File | Change |
|------|--------|
| `co_cli/prompts/__init__.py` (lines 118-121) | Add adoption mandate sentence |

**Tests:**

| Test | File | Assertion |
|------|------|-----------|
| `test_soul_seed_framing_present` | `tests/test_prompt_assembly.py` | Update existing assertion to also check for `"Adopt this persona fully"` |
| `test_prompt_under_budget` | `tests/test_prompt_assembly.py` | Verify existing budget assertion still passes (expect it will — ~15 tokens headroom exists) |

---

### H5 — Override Precedence Rule + Agent Registration

**Problem:** Two issues:
1. `load_personality` in `co_cli/tools/context.py` is defined but **never registered on the agent** in `agent.py`. The tool is dead code — the agent cannot call it.
2. When both character and style axes are loaded, the existing docstring mentions "style wins on format, character wins on identity" but this isn't appended to the tool output. The LLM sees two blocks of guidance with no explicit precedence rule in the actual content.

**Evidence:** The agentic loop TODO §9 line 662 lists `load_personality` as expected dynamic content ("Personality depth — via load_personality tool"). It's designed to be a registered tool. The registration gap is a bug, not a design decision.

**Design:**

**(a) Register `load_personality` on the agent (`agent.py`):**

Add after the existing read-only tool registrations (around line 165):

```python
from co_cli.tools.context import load_personality
agent.tool(load_personality, requires_approval=all_approval)
```

This is a read-only tool (loads markdown files, no side effects). Standard `requires_approval=all_approval` pattern for read-only tools.

**(b) Append override precedence note to display output (`context.py`):**

When both character and style are loaded (i.e., `len(loaded) == 2`), append a precedence rule to the combined output:

```python
if len(loaded) == 2:
    combined += (
        "\n\n---\n"
        "Override precedence: style wins on format (length, structure, emoji), "
        "character wins on identity (voice, markers, philosophy)."
    )
```

Insert after line 95 (`combined = "\n\n".join(parts)`) in `context.py`.

**Alignment with agentic loop TODO:** Registration is needed regardless of the axis refactor. When Phase 5d changes what `load_personality` returns (axis values instead of essays), the registration and precedence mechanism stay — only the content changes. The precedence rule simplifies post-5d (single axis summary block, not two competing files), making H5's dual-axis precedence transitional but the registration permanent.

**Files modified:**

| File | Change |
|------|--------|
| `co_cli/agent.py` (~line 165) | Import + register `load_personality` |
| `co_cli/tools/context.py` (~line 95) | Append precedence note when both axes loaded |

**Tests:**

| Test | File | Assertion |
|------|------|-----------|
| `test_load_personality_precedence_note` | `tests/test_context_tools.py` | When preset has both character + style, `"Override precedence"` in `result["display"]` |
| `test_load_personality_no_precedence_for_style_only` | `tests/test_context_tools.py` | When preset has style only (e.g., `"terse"`), `"Override precedence"` NOT in `result["display"]` |

---

## Part II: NEXT Tier

Items that depend on NOW tier or coordinate with agentic loop TODO phases.

### H2 — Memory-Informed Personality

**Depends on:** H5 (load_personality must be registered and working)

**Problem:** Personality is static — loaded from markdown files, never adapted by what the agent has learned about the user. If the user corrects co's tone, saves a preference about communication style, or establishes relationship dynamics through conversation, those memories exist in `.co-cli/knowledge/memories/` but are never surfaced when personality is loaded.

**Evidence:** Replika's memory system directly influences personality responses. Character.ai maintains "character memory" that adjusts behavior. Pi (Inflection) uses conversation history to adapt personality warmth. The pattern: personality + accumulated context outperforms personality alone.

**Design:**

Extend `load_personality()` in `context.py` to scan for memories tagged `personality-context` and append them as a `## Learned Context` section.

Pseudocode for the memory scan (insert before the return statement in `load_personality`):

```
memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
if memory_dir.exists():
    all_memories = _load_all_memories(memory_dir)
    personality_memories = [
        m for m in all_memories
        if "personality-context" in m.tags
    ]
    if personality_memories:
        # Sort by recency, take top 5
        personality_memories.sort(
            key=lambda m: m.updated or m.created,
            reverse=True,
        )
        personality_memories = personality_memories[:5]
        # Append as learned context
        lines = ["## Learned Context", ""]
        for m in personality_memories:
            lines.append(f"- {m.content}")
        combined += "\n\n" + "\n".join(lines)
```

Uses `_load_all_memories()` from `co_cli/tools/memory.py` — read-only, no gravity side effects (gravity only fires from `recall_memory`, not from `_load_all_memories` directly).

**Import:** Add `from co_cli.tools.memory import _load_all_memories` at the top of `context.py`. This is a private helper within the package, but `context.py` and `memory.py` are both in `co_cli/tools/` — same package, valid internal import.

**Tag convention:** `personality-context` is a new tag. Users and the agent can tag memories with it when the content relates to personality preferences:
- "User prefers direct answers without hedging" → tags: `["preference", "personality-context"]`
- "User responded positively to humor in explanations" → tags: `["pattern", "personality-context"]`

**Coordination with agentic loop TODO Phase 1e:** `inject_opening_context` processor (§16) structurally enforces topic-relevant memory recall at conversation start. H2 is complementary — opening context recalls topic-relevant memories by keyword, H2 loads personality-specific memories by tag when personality is explicitly loaded. Different triggers, different tag filters, no conflict.

**Budget impact:** At most 5 memories × ~100 chars = ~500 chars added to personality load. Only when `load_personality` is called. No impact on system prompt.

**Survives Phase 5d:** Memory-informed personality is orthogonal to the axis model. Whatever the personality representation, accumulated memories enhance it.

**Files modified:**

| File | Change |
|------|--------|
| `co_cli/tools/context.py` | Import `_load_all_memories`, add ~20 lines for memory scan + append |

**Tests:**

| Test | File | Assertion |
|------|------|-----------|
| `test_load_personality_with_personality_memories` | `tests/test_context_tools.py` | Create a temp memory file tagged `personality-context`, verify `"Learned Context"` appears in `result["display"]`. Requires `tmp_path` fixture + monkeypatching `Path.cwd()` |
| `test_load_personality_no_memories_graceful` | `tests/test_context_tools.py` | When memory dir doesn't exist or has no `personality-context` memories, output is unchanged (no `"Learned Context"` section) |

---

### H4 — Personality-Aware Context Compaction

**Problem:** When the sliding-window processor compacts history (`_history.py`), the summarization prompt asks for decisions, progress, and requirements — but nothing about personality dynamics. Relationship-building moments (emotional exchanges, humor, tone corrections) are lost during compaction. After a compaction event, the agent reverts to default personality because the personality-reinforcing exchanges were dropped.

**Evidence:** Replika preserves "relationship progression" across sessions. Character.ai maintains "conversation dynamics" in its memory. The compaction prompt is the critical boundary — what survives compaction defines the agent's personality continuity.

**Design:**

Add a `_PERSONALITY_COMPACTION_ADDENDUM` constant that is appended to the summarization prompt when personality is active. This is an addendum to the base prompt, not a replacement.

**Must align with agentic loop TODO §8.2:** Phase 1c plans a full compaction prompt rewrite (`_SUMMARIZE_PROMPT` + `_SUMMARIZER_SYSTEM_PROMPT` in `_history.py`). H4 adds an addendum that appends to whatever base prompt is active. When Phase 1c rewrites the base prompt, the addendum mechanism (separate constant + conditional append) survives unchanged — only the base prompt changes.

The existing `_SUMMARIZE_PROMPT` (line 138) already includes "User constraints, preferences, and stated requirements" — H4 adds personality-specific items that go beyond general preferences.

Pseudocode:

```
_PERSONALITY_COMPACTION_ADDENDUM = (
    "\n\nAdditionally, preserve:\n"
    "- Personality-reinforcing moments (emotional exchanges, humor, "
    "relationship dynamics)\n"
    "- User reactions that shaped the assistant's tone or communication style\n"
    "- Any explicit personality preferences or corrections from the user"
)
```

**Changes to `summarize_messages()`:**

Add `personality_active: bool = False` parameter. When True, append the addendum to the prompt before passing to the summarizer agent:

```
async def summarize_messages(
    messages: list[ModelMessage],
    model: str | Any,
    prompt: str = _SUMMARIZE_PROMPT,
    personality_active: bool = False,
) -> str:
    if personality_active:
        prompt = prompt + _PERSONALITY_COMPACTION_ADDENDUM
    ...
```

**Caller change in `truncate_history_window()`:**

Pass `personality_active=bool(ctx.deps.personality)` when calling `summarize_messages()`:

```
summary_text = await summarize_messages(
    dropped, model,
    personality_active=bool(ctx.deps.personality),
)
```

**Budget impact:** ~50 tokens added to the compaction summarization prompt when personality is active. This is a one-time cost per compaction event, not per turn. Compaction events are rare (triggered when history exceeds threshold).

**Implementation order:** H4 can land on the current `_SUMMARIZE_PROMPT` now. When Phase 1c rewrites the prompt, the addendum mechanism is untouched.

**Files modified:**

| File | Change |
|------|--------|
| `co_cli/_history.py` (after line 152) | Add `_PERSONALITY_COMPACTION_ADDENDUM` constant |
| `co_cli/_history.py` (lines 164-168) | Add `personality_active` parameter to `summarize_messages()` |
| `co_cli/_history.py` (line 264) | Pass `personality_active=bool(ctx.deps.personality)` in caller |

**Tests:**

| Test | File | Assertion |
|------|------|-----------|
| `test_personality_compaction_addendum_present` | `tests/test_history.py` | Call `summarize_messages(..., personality_active=True)` and verify the prompt includes `"Personality-reinforcing moments"`. This is a structural test — verify the prompt string is assembled correctly. Does not require an LLM call (test the prompt construction, not the LLM output) |

---

## Part III: LATER Tier

Future directions informed by the research. Not designed in detail — included to capture the research signal and inform Phase 5d planning.

### M1 — Conditional Personality Injection

**Idea:** Only inject personality-heavy content when the conversation warrants it. Factual Q&A turns get minimal personality (soul seed only). Creative/emotional exchanges get full personality load.

**Research signal:** Inworld AI's "emotional fluidity" slider (0.0 = static, 1.0 = highly reactive) controls how much personality responds to context. GPT Store characters with high engagement ratings inject personality conditionally based on user message sentiment.

**Alignment:** Requires the three-way intent classification from agentic loop TODO §10.6 (directive/inquiry/analysis). Inquiry turns could skip personality load. Depends on Phase 1a.

### M2 — Fragment Composition for Preset Scaling

**Idea:** As presets grow beyond 5, use composable personality fragments instead of full character files. A "sarcastic mentor" preset composes `fragment/sarcastic.md` + `fragment/mentor.md` + `style/balanced.md`.

**Research signal:** Inworld AI's layered architecture (core personality + contextual overlays). D&D Beyond's trait/ideal/bond/flaw decomposition. Both show that composition from atomic fragments scales better than monolithic character files.

**Alignment:** Builds on Phase 5d's axis model (§11.2). Each axis value could be a fragment. The composition machinery (`_composer.py`) already joins parts — extending to N fragments is natural.

### M3 — Persona Drift Detection

**Idea:** Monitor the agent's actual outputs for personality consistency. Compare recent responses against the character file's markers and boundaries. Flag when the agent drifts (e.g., Finch starts being casual, Jeff stops narrating).

**Research signal:** Replika's "personality coherence" metric. Character.ai's internal consistency scoring. Academic work on "character fidelity" measurement using embedding similarity.

**Alignment:** Post-Phase-5d work. Needs the axis model to define measurable dimensions. Could use the calibration examples from H1 as the reference baseline.

### M4 — Prompt Debugger for Personality

**Idea:** A diagnostic tool (`co debug-personality`) that shows exactly what personality content is injected at each layer: soul seed in system prompt, character/style from `load_personality`, memories from H2, compaction addendum from H4. Helps authors tune presets.

**Research signal:** Inworld AI Studio provides per-layer personality visualization. The pattern: when personality is multi-layered, authors need visibility into what each layer contributes.

**Alignment:** Independent of agentic loop phases. Could land anytime after H5 (needs `load_personality` registered).

---

## Appendix: Research Evidence Sources

Full analysis in `docs/REVIEW-agentic-personality-systems.md`. Key sources per enhancement:

| Enhancement | Primary evidence | Systems studied |
|-------------|-----------------|-----------------|
| H1 (calibration examples) | Example dialogues as mandatory character field | Inworld AI, Character.ai, Replika, Pi, GPT Store |
| H2 (memory-informed personality) | Memory → personality feedback loop | Replika, Character.ai, Pi (Inflection) |
| H3 (override mandate) | Explicit persona adoption instruction | Character.ai, Replika, Inworld AI Contextual Mesh |
| H4 (personality compaction) | Relationship continuity across context boundaries | Replika, Character.ai |
| H5 (precedence rules) | Conflict resolution in multi-axis personality | Inworld AI (10 sliders + free text), D&D Beyond (trait/ideal/bond/flaw) |
| M1 (conditional injection) | Emotional fluidity / context-sensitive personality | Inworld AI, GPT Store high-engagement patterns |
| M2 (fragment composition) | Composable personality layers | Inworld AI, D&D Beyond, SillyTavern |
| M3 (drift detection) | Personality coherence measurement | Replika, Character.ai, academic literature |

## Files Summary

| File | Items | Nature of change |
|------|-------|------------------|
| `co_cli/prompts/personalities/character/finch.md` | H1 | Append calibration section |
| `co_cli/prompts/personalities/character/jeff.md` | H1 | Append calibration section |
| `co_cli/prompts/__init__.py` (lines 118-121) | H3 | Add adoption mandate sentence |
| `co_cli/tools/context.py` | H5, H2 | Register tool, precedence note, memory scan (~35 lines total) |
| `co_cli/agent.py` (~line 165) | H5 | Import + register `load_personality` |
| `co_cli/_history.py` (lines 138-183) | H4 | Addendum constant + parameter (~10 lines) |
| `tests/test_prompt_assembly.py` | H3 | Update framing assertion |
| `tests/test_context_tools.py` | H1, H5, H2 | 4 new test functions |
| `tests/test_history.py` | H4 | 1 new structural test |
