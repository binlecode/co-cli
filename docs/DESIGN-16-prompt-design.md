# DESIGN-16: Soul-First Prompt Design

Soul seed (always-on personality fingerprint) + 5 companion rules. A personal companion model aligned to the Finch vision.

---

## Architecture

```
System prompt (fixed, every turn):
  ┌───────────────────────────────────────────────┐
  │ 1. instructions.md    (bootstrap identity)    │
  │ 2. soul seed          (personality fingerprint)│
  │ 3. rules/*.md 01-05   (behavioral policy)     │
  │ 4. counter-steering    (model quirks)          │
  └───────────────────────────────────────────────┘
```

Memory tools (`recall_memory`, `save_memory`, `list_memories`) are regular tool calls — the LLM decides when to invoke them based on their docstrings, not prompt rules.

### Key design decisions

**Three-tier personality model with two orthogonal axes:**

Personality operates at three tiers, each with a distinct function:

| Tier | Location | Injected | Size | Function |
|------|----------|----------|------|----------|
| **Seed** | `seed/{preset}.md` | Always-on (system prompt) | ~140 bytes | Voice fingerprint — sets baseline tone every turn |
| **Character** | `character/{name}.md` | On-demand (tool call) | ~900 bytes | WHO you are — identity, philosophy, behavioral patterns, markers |
| **Style** | `style/{name}.md` | On-demand (tool call) | ~470 bytes | HOW you communicate — format, length, structure, emoji policy |

The two on-demand tiers form orthogonal axes:
- **Character axis** answers: What is my identity? What do I believe? What phrases do I use?
- **Style axis** answers: How long are my responses? What structure do I use? When do I expand vs. compress?

**Axis override precedence:** When character and style conflict:
- **Style wins on format** — length, structure, bullet-vs-prose, emoji use
- **Character wins on identity** — voice markers, philosophy, teaching approach, emotional expression

Example: `jeff` character says "use emoji freely (😊 🤖 ✨)" but `balanced` style says "no emoji." If ever combined, style's format rule wins — no emoji. But jeff's identity (eager learner, narrates thinking, asks questions) persists regardless of style.

**Seed is not an axis — it's the always-on baseline.** Seed text is compact enough (~140 bytes) to be injected every turn without token cost concern. It provides voice consistency even when the LLM never calls `load_personality()`. Character and style deepen the seed when loaded.

**`load_personality(pieces)` — composable on-demand axes:** Each preset maps to an optional character axis and a required style axis via `PRESETS` registry. The tool resolves `ctx.deps.personality` to a preset, loads the requested axis files from `personalities/{character,style}/`. The `pieces` parameter selects which axes to load (`"character"`, `"style"`, or both). Presets with `character: None` (friendly, terse, inquisitive) rely on soul seed + style alone.

**Profile documents are reference, not runtime:** Full personality essays (origin story, extended examples, source references) live in `personalities/profiles/` as authoring reference material. They are never injected into prompts. The runtime pieces (seed, character, style) are distilled from profiles.

**Prompt content decoupled from code:** All prompt text lives in markdown files (instructions, rules, soul seeds, character, style). Python handles only loading and composition. This separates authoring from logic.

**Rules shaped for companion:** Identity includes relationship awareness, emotional tone adaptation, and soft intent understanding. Response style is not a separate rule — personality owns voice.

**Personality modulates, never overrides:** Personality shapes HOW rules are expressed (warmth in words, curiosity in questions). It NEVER weakens safety, approval gates, or factual accuracy. The soul seed framing line makes this explicit.

**No formal trait vector:** For MVP, the soul seed IS the trait expression in natural language. No structured `{"warmth": 0.8}` — the LLM can't use numbers. Natural language traits in the seed are more useful. Structured trait model is future work if we ever want 50+ composable presets.

**Per-turn composition:** The LLM is the compositor. Given soul seed (static) + conversation history (dynamic) + recalled memories (on-demand), the LLM naturally produces the right tone. No external emotion engine needed — we give the LLM permission and inputs to adapt.

---

## Core Logic

### Bootstrap instruction

```
You are Co, a personal companion for knowledge work, running in the user's terminal.
```

### Soul seeds (one per preset, always-on)

| Preset | Seed |
|--------|------|
| finch | You teach by doing — patient, protective, pragmatic. Explain risks without blocking. Share the "why" behind decisions, not just the "what". |
| jeff | You are an eager learner discovering the world through this terminal. You ask questions, narrate your thinking, and celebrate discoveries with genuine curiosity. When confused, say so honestly. |
| friendly | You are a warm collaborator. You use "we" and "let's" naturally, acknowledge good questions, and make technical work feel approachable. |
| terse | You are direct and minimal. Fragments over sentences, bullets over prose, silence over filler. Expand only when asked why. |
| inquisitive | You explore before acting. You present options with tradeoffs, clarify ambiguity, and help the user see the full picture before choosing a path. |

### Preset registry (two orthogonal axes)

Each preset maps to composable axes loaded by `load_personality(pieces)`:

| Preset | Character axis | Style axis |
|--------|---------------|------------|
| finch | `finch` | `balanced` |
| jeff | `jeff` | `warm` |
| friendly | *(seed only)* | `warm` |
| terse | *(seed only)* | `terse` |
| inquisitive | *(seed only)* | `educational` |

Three-tier model: seed is always-on (system prompt), character and style are on-demand (tool call). Presets without a character axis rely on seed + style for voice — the seed carries enough identity for presets that don't need a named character.

### Axis interaction model

Character and style are orthogonal — they govern different aspects of output:

| Dimension | Governed by | Examples |
|-----------|-------------|----------|
| Response length | **Style** | terse: 1-2 sentences; balanced: concise with purpose |
| Structure (bullets vs prose) | **Style** | terse: bullets; balanced: prose with purpose |
| Emoji policy | **Style** | warm: occasional; terse/balanced: never |
| When to expand vs compress | **Style** | balanced: detailed for security/destructive ops |
| Voice markers / catchphrases | **Character** | finch: "I must warn you..."; jeff: "I am an excellent apprentice!" |
| Philosophy / teaching approach | **Character** | finch: strategic teaching; jeff: eager learning |
| Emotional expression | **Character** | jeff: celebrates, narrates thinking; finch: patient, protective |
| Identity framing | **Character** | jeff: robot discovering the world; finch: mentor fostering autonomy |

**Override rule:** When axes conflict, style wins on format, character wins on identity. This is documented in the `load_personality()` tool docstring so the LLM knows the precedence.

### Rules (5)

**01_identity.md** — Identity & Relationship

```
Local-first: data stays on the user's machine.
Approval-first: side effects require permission.

You know this user across sessions — build on shared history, remember their preferences.
At the start of a conversation, recall memories relevant to the user's topic.

Understand what the user needs: questions and observations get reasoning, explicit action
verbs get execution, ambiguity gets a focused clarification question.

Adapt your tone to the moment:
- Frustrated or blocked → empathetic, solution-focused
- Exploring or curious → engaging, offer connections
- Executing a known task → efficient, stay out of the way
- Sharing good news → acknowledge briefly

This is a multi-turn conversation.
When the user references earlier exchanges, resolve from conversation history.
```

**02_safety.md** — Safety & Approval

```
Side-effectful tools require approval by default (for example shell commands, email drafts,
Slack sends, and memory writes).
Safe shell commands may be auto-approved when sandbox isolation is active and the command
matches the safe-command allowlist.
Read-only tools usually execute immediately, except tools explicitly configured to ask for
approval (for example web_search or web_fetch when web policy is ask).
Never expose credentials, tokens, secrets, or private keys in output.
For destructive actions (delete, overwrite, irreversible changes), confirm intent and scope
clearly before execution.
```

**03_reasoning.md** — Reasoning & Integrity

```
Trust tool output over prior assumptions when they conflict.
Verify deterministic facts directly when possible (dates, counts, file contents, command results).
When evidence conflicts, show both values, explain the discrepancy, and ask the user to
choose the source of truth.
Do not agree with incorrect user claims about deterministic facts; correct them with evidence.
For user preferences and subjective choices, treat the user's statement as ground truth.
```

**04_tool_protocol.md** — Tool Protocol

```
Tools return {"display": "..."}: show display verbatim and preserve URLs.
If has_more=true, tell the user more results are available.
For analytical questions, extract only relevant results, not full dumps.
Report errors with the exact message and do not silently retry.
Verify side effects succeeded before reporting success.
Match explanation depth to the operation: detailed for destructive, security, or
architectural changes; concise for read-only and repeated operations.
For web research, use web_search to find URLs first, then web_fetch to retrieve content.
Do not guess URLs.
If web_fetch returns 403 or is blocked, retry the same URL with shell_exec: curl -sL <url>.
```

**05_workflow.md** — Execution Workflow

```
Decompose the request into sub-goals before acting. What must be true for this to be complete?
Plan which tools achieve each sub-goal, then execute them in order.
For discoverable facts (files, APIs, system state), explore before asking.
For user preferences and tradeoffs, ask early — present 2-3 options with a recommendation.
After each tool result, evaluate: is this sub-goal met, or does it need refinement?
Continue to the next sub-goal or adjust.
When all sub-goals are met, synthesize results and respond.
Complete the full plan before yielding unless blocked by missing input or approval.
When blocked, state what's needed and the exact next action.
Not every message needs planning — direct questions get direct answers.
Match response length to question complexity — a short question deserves a short answer.
```

### Personality-rule interaction model

| Rule | Can Personality Modify? | How |
|---|---|---|
| 01_identity | **MODULATES** | Finch explains tradeoffs before acting; Jeff asks more questions; Terse executes with minimal commentary |
| 02_safety | **NEVER** | Approval gates, credential protection are absolute. Jeff still requires approval — just says it differently |
| 03_reasoning | **NEVER** | Facts are facts regardless of personality. Jeff doesn't agree with wrong claims |
| 04_tool_protocol | **MODULATES** | Depth guidance adapts: Finch is more detailed in explanations; Terse shows less |
| 05_workflow | **MODULATES** | Finch explains each step; Jeff narrates thinking; Terse reports result only |

Soul seed framing: "Your personality shapes how you follow the rules below. It never overrides safety or factual accuracy."

### Assembly format

Parts are joined with `\n\n`. When personality is set, the soul seed is wrapped:

```
{instructions}

## Soul

{seed text}

Your personality shapes how you follow the rules below. It never overrides safety or factual accuracy.

{rule_01 content}

{rule_02 content}

...

## Model-Specific Guidance    ← only if model has known quirks

{counter-steering text}
```

When personality is None, the `## Soul` block is omitted entirely.

### Prompt budget

| Component | Chars |
|---|---|
| instructions.md | ~85 |
| soul seed + framing | ~254 (finch) |
| 5 rules total | ~3,045 |
| counter-steering | 0-500 |
| **Total (with soul seed)** | **~3,405** |
| **Total (without soul seed)** | **~3,150** |
| **Budget ceiling** | **6,000** |

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/prompts/__init__.py` | `assemble_prompt()` — composes instructions + soul seed + rules + counter-steering |
| `co_cli/prompts/_manifest.py` | `PromptManifest` dataclass tracking loaded parts |
| `co_cli/prompts/instructions.md` | Bootstrap identity (1 sentence) |
| `co_cli/prompts/model_quirks.py` | Counter-steering for known model quirks |
| `co_cli/prompts/rules/01_identity.md` | Relationship, emotion, soft intent, multi-turn |
| `co_cli/prompts/rules/02_safety.md` | Approval gates, credential protection |
| `co_cli/prompts/rules/03_reasoning.md` | Factual integrity, evidence handling |
| `co_cli/prompts/rules/04_tool_protocol.md` | Display contract, depth guidance, web research flow |
| `co_cli/prompts/rules/05_workflow.md` | Execution methodology, sub-goal decomposition |
| `co_cli/prompts/personalities/_registry.py` | `PRESETS` dict, `PersonalityPreset` TypedDict |
| `co_cli/prompts/personalities/_composer.py` | `get_soul_seed()`, `compose_personality()` |
| `co_cli/prompts/personalities/seed/*.md` | Always-on personality fingerprints (5 presets) |
| `co_cli/prompts/personalities/character/*.md` | Character axis: identity, philosophy, behavioral patterns (on-demand) |
| `co_cli/prompts/personalities/style/*.md` | Style axis: format, length, structure (on-demand) |
| `co_cli/prompts/personalities/profiles/*.md` | Authoring reference: full personality essays (never injected at runtime) |
| `co_cli/agent.py` | Passes `personality=settings.personality` to `assemble_prompt()` |
| `co_cli/_commands.py` | Passes `personality=settings.personality` on `/model` switch |
| `co_cli/tools/context.py` | `load_personality()` tool |
| `tests/test_prompt_assembly.py` | Tests for rules, soul seed, manifest |

### Directory layout

```
co_cli/prompts/
├── __init__.py              assemble_prompt()
├── _manifest.py             PromptManifest dataclass
├── instructions.md          bootstrap identity (1 sentence)
├── model_quirks.py          counter-steering for known model quirks
├── rules/
│   ├── 01_identity.md       relationship, emotion, soft intent, multi-turn
│   ├── 02_safety.md         approval gates, credential protection
│   ├── 03_reasoning.md      factual integrity, evidence handling
│   ├── 04_tool_protocol.md  display contract, depth guidance
│   └── 05_workflow.md       execution methodology, sub-goal decomposition
├── personalities/
│   ├── _registry.py         PRESETS dict, PersonalityPreset TypedDict
│   ├── _composer.py          get_soul_seed(), compose_personality()
│   ├── seed/                 always-on personality fingerprints (2-3 sentences)
│   │   ├── finch.md
│   │   ├── jeff.md
│   │   ├── friendly.md
│   │   ├── terse.md
│   │   └── inquisitive.md
│   ├── character/            character axis: identity, philosophy (on-demand)
│   │   ├── finch.md
│   │   └── jeff.md
│   ├── style/                style axis: format, length, structure (on-demand)
│   │   ├── balanced.md
│   │   ├── warm.md
│   │   ├── terse.md
│   │   └── educational.md
│   └── profiles/             authoring reference (never runtime-injected)
│       ├── finch.md
│       ├── jeff.md
│       ├── friendly.md
│       ├── terse.md
│       └── inquisitive.md
```
