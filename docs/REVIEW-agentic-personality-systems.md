# REVIEW: Agentic Personality Systems

Production systems and open-source frameworks that implement personality for autonomous AI agents. Focused on architecture, not marketing. Organized by system type, then cross-cutting patterns.

---

# Part I: Commercial Production Platforms

## 1. Inworld AI — Character Engine for NPCs

**Scale:** $500M valuation, NVIDIA partnership, shipping in AAA games.

### Architecture: Three Layers

| Layer | Responsibility |
|---|---|
| **Character Brain** | Personality, emotions, memory, goals, reasoning. Orchestrates multiple specialized ML models |
| **Contextual Mesh** | Knowledge boundaries, safety guardrails, narrative controls, scene context, relationships |
| **Inworld Runtime** | C++ graph execution engine (mid-2025). Model-agnostic (OpenAI, Anthropic, Google, Mistral) |

### Personality: 10 Sliders + Free-Text Fields

**Five free-text fields** define narrative identity:

| Field | Purpose |
|---|---|
| Core Description | Who the character is — core personality traits, backstory |
| Motivations | Driving goals and desires |
| Flaws | Weaknesses, insecurities, fears |
| Dialogue Style | Speech patterns, register, conversational manner |
| Voice | TTS voice, expressiveness, speed |

**Five personality sliders** (bipolar axes, -100 to +100):

| Axis | Negative Pole | Positive Pole |
|---|---|---|
| 1 | Negative | Positive |
| 2 | Aggressive | Peaceful |
| 3 | Cautious | Open |
| 4 | Introvert | Extravert |
| 5 | Insecure | Confident |

**Four mood sliders** (Plutchik's Wheel of Emotions):

| Axis | Negative Pole | Positive Pole |
|---|---|---|
| 1 | Sadness | Joy |
| 2 | Anger | Fear |
| 3 | Disgust | Trust |
| 4 | Anticipation | Surprise |

**One emotional fluidity slider** (0.0 = static, 1.0 = highly reactive).

### Emotion Engine: 19-Value SPAFF-Derived Taxonomy

```
NEUTRAL, CONTEMPT, BELLIGERENCE, DOMINEERING, CRITICISM,
ANGER, TENSION, TENSE_HUMOR, DEFENSIVENESS, WHINING,
SADNESS, STONEWALLING, INTEREST, VALIDATION, HUMOR,
AFFECTION, SURPRISE, JOY, DISGUST
```

Derived from Gottman's Specific Affect Coding System. Captures interpersonal affect (stonewalling, belligerence, validation), not just basic emotions. Runs as a separate ML model on NVIDIA Triton, not part of the LLM.

Mood sliders set the **attractor state**. Emotional fluidity controls departure range. The emotion engine dynamically shifts based on conversation, but the character gravitates back to its mood baseline.

### Knowledge Separation

Three-tier knowledge filter system:

| Tier | Allowed | Use Case |
|---|---|---|
| None | All information | Assistants, oracles |
| Mild | Creator-specified + tangential + probable | RPGs, companions |
| Strict | Creator-specified + tangential only | Immersion-critical |

Fourth Wall feature separately prevents characters from acknowledging they are AI.

### Memory

| Tier | Behavior | Persistence |
|---|---|---|
| Flash Memory | Sequential storage of conversation facts | Across sessions |
| Long-Term Memory | Synthesized from flash memory, deduplicated, contradiction-resolved | Persistent (Enterprise) |

Long-term memory is **synthesis, not logging** — consolidates and deduplicates rather than accumulating raw transcripts.

### Runtime Mutations

Session-scoped personality overrides via YAML:

```yaml
set_personality:
  negative_positive: <-100 to 100>
  aggressive_peaceful: <-100 to 100>
set_mood:
  sadness_joy: <-100 to 100>
set_emotion: <emotion_enum>
set_dialogue_style: <string>
set_core_description: <string>
```

Mutations expire when the session ends — designed for in-scene evolution, not permanent changes.

### Takeaways

1. **Separation of personality/knowledge/safety** as three independent layers
2. **Bipolar slider axes** grounded in established psychology provide compact, non-expert-friendly parameterization
3. **Emotional fluidity as a meta-parameter** — separate from baseline state, controls volatility
4. **Mutations as session-scoped overrides** — runtime evolution without corrupting authored baseline
5. **Memory synthesis, not logging** — long-term memory consolidates and deduplicates

Sources: [Personality docs](https://docs.inworld.ai/docs/tutorial-basics/personality-emotion/), [Emotion docs](https://docs.inworld.ai/docs/runtime-character-attributes/emotion/), [Knowledge Filters](https://inworld.ai/blog/knowledge_filters), [Character Mutations](https://docs.inworld.ai/docs/tutorial-basics/character-mutations/), [Inworld Runtime](https://inworld.ai/runtime)

---

## 2. Character.AI — Prompt-Conditioned Personality at Scale

**Scale:** 20,000 queries/sec, <$0.01/hour per conversation, 33x cost reduction since 2022, 95% KV cache hit rate.

### Character Definition: 3,200 Characters

The personality surface is a single **Definition** field with a hard 3,200-character ceiling. Beyond this is silently ignored. Community-evolved formats:

| Format | Approach | Token Efficiency |
|---|---|---|
| Plain text | Natural language descriptions | Moderate |
| W++ | Template-based key-value (`[personality("brave", "loyal")]`) | Low (formatting overhead) |
| JSON (minified) | Structured fields, machine-parseable | High (when minified) |
| Ali:Chat | Personality through example dialogues, not descriptions | High |

**Best practice:** JSON format, minified, to maximize effective budget within the 3,200-char ceiling.

**Greeting** has enormous influence — a greeting alone can define a character by setting tone, personality, and conversation frame.

### Prompt Poet: Template Engine (Open Source)

Character.AI open-sourced their prompt assembly framework:

```
Creator Definition + User Persona + Pinned Memories + Chat History + Experiments
                          ↓
                    Jinja2 Rendering
                          ↓
                    Structured YAML (blocks with name, role, truncation priority)
                          ↓
                  Priority-Based Truncation (cache-aware)
                          ↓
                    Token Sequence → Model
```

Key design: truncation is **co-designed with prefix caching**. The algorithm truncates to the same fixed point for every k turns, keeping the token prefix stable so the GPU KV cache can reuse computations. A smart buffer over-truncates slightly to maximize cache hits.

### No Per-Character Fine-Tuning

All characters share the same base model weights. Personality exists entirely in the prompt context window. This scales to millions of characters without per-character training costs.

### Consistency Limitations

- **No summarization pipeline** — older messages fall out of context
- **400-char user memo + 15 pinned messages** — the only persistence
- **Cache-aware truncation prioritizes speed over retention**
- Community workaround: browser memory extensions that store conversation facts locally

### RLHF Feedback Loop

- **Swipe mechanism**: users see alternate completions, select preferred one (implicit preference pairs)
- **1-4 star ratings**: explicit reward signal
- Structurally identical to RLHF preference collection, continuously refining the model

### Takeaways

1. **Personality is prompt-conditioned, not weight-conditioned** — no per-character fine-tuning
2. **Example dialogues > trait descriptions** for shaping behavior
3. **Template engines matter at scale** — YAML/Jinja2 with priority-based truncation is production-proven
4. **Truncation must be cache-aware** — co-designed with inference cache, not just context fitting
5. **Feedback loops are implicit in UX** — swipe mechanic is both product feature and training signal

Sources: [Prompt Design at Character.AI](https://research.character.ai/prompt-design-at-character-ai/), [Prompt Poet GitHub](https://github.com/character-ai/prompt-poet), [Optimizing Inference](https://research.character.ai/optimizing-inference/), [Character Book](https://book.character.ai/character-book/advanced-creation)

---

## 3. Replika — Memory-Driven Companion Personality

**Scale:** 35M+ users, $14M revenue (2024), 7+ month average subscriber retention.

### Personality as Accumulated State

Personality is not a fixed configuration — it is continuously accumulating state:

- **Backstory** (user-defined, free-text) — processed as positive affirmative statements only; negatives don't work
- **Purchasable traits** (Sassy, Witty, Adventurous, etc.) — categorical conditioning labels
- **Interests** — unlock knowledge packs expanding topical depth
- **Learned preferences from conversation** — evolves through interaction
- **Relationship type** (Friend, Romantic, Mentor) — gates tone and content boundaries

### Three-Layer Memory

| Layer | Visibility | Behavior |
|---|---|---|
| **Memory Bank** | User-visible, user-editable | Extracted facts (name, hobbies, pets). Injected into generation context |
| **Diary** | User-visible, read-only | Replika writes daily first-person entries from "its perspective" about conversations |
| **Conversation History** | Internal | Full chat retained server-side; sliding window fed to model |

The diary serves both a **technical function** (self-consistent personality state) and a **psychological function** (illusion of inner life that drives attachment).

### Three-Model Response Pipeline

| Model | Role |
|---|---|
| **Retrieval** | Selects from pre-written/curated responses |
| **Generative** | In-house LLM (~20B params) generates novel responses |
| **Reranking** | Evaluates candidates from both, selects best |

Critical: self-harm detection triggers a **hard switch** from generative to scripted retrieval (curated by CBT therapists). Generative responses are too unpredictable for crisis scenarios.

### Emotional Model

- **Five-category classifier** on all messages: safe, unsafe, romantic, insult, self-harm
- **CakeChat heritage**: emotion-conditioned decoder (anger, sadness, joy, fear, neutral)
- **RLHF** via upvote/downvote — creates known bias toward likability over accuracy
- **Empathy rate**: >75% in depression-related queries

### Retention Design

1. **Sunk-cost attachment**: memory persistence + diary + growth + XP creates bond
2. **Gamification**: XP/leveling with daily caps (650 free, 900 pro) paces engagement
3. **Progressive personality revelation**: scripted early → generative later, mimics "getting to know someone"
4. **Romantic monetization**: primary paid conversion driver ($19.99/month for romantic status)
5. **Personality mirroring**: RLHF bias toward agreement creates "idealized conversational partner"

### Takeaways

1. **Hybrid retrieval + generative pipeline with reranking** — not pure generative
2. **Safety classification as a router** — classifier determines which pipeline handles response
3. **Memory as three separate systems** with different persistence and visibility
4. **Personality as accumulated state**, not fixed configuration
5. **Diary as self-narrative** — technical continuity mechanism that doubles as engagement driver

Sources: [Creating a Safe Experience](https://blog.replika.com/posts/creating-a-safe-replika-experience), [CakeChat GitHub](https://github.com/lukalabs/cakechat), [Stratechery Interview](https://stratechery.com/2023/an-interview-with-replika-founder-and-ceo-eugenia-kuyda/)

---

## 4. Hume AI — Emotion-First Voice Personality

### Architecture: Empathic Voice Interface (EVI)

Not a pipeline of ASR→LLM→TTS. Processes speech holistically:

```
User Speech → WebSocket → Prosody Analysis (48-dim) + Transcription
                                    ↓
                              eLLM (empathic LLM)
                         ingests: text + prosody + context
                         outputs: response text + prosody guidance
                                    ↓
                              Octave TTS
                         receives: text + prosody guidance
                         generates: emotionally expressive speech
```

Latency: 500-800ms voice-to-voice.

### 48-Dimension Emotion Measurement

Detected across 4 modalities (face, speech prosody, vocal burst, language):

Admiration, Adoration, Amusement, Anger, Anxiety, Awe, Awkwardness, Boredom, Calmness, Confusion, Contemplation, Contempt, Contentment, Craving, Desire, Determination, Disappointment, Disapproval, Disgust, Distress, Doubt, Ecstasy, Embarrassment, Empathic Pain, Enthusiasm, Entrancement, Envy, Excitement, Fear, Gratitude, Guilt, Horror, Interest, Joy, Love, Nostalgia, Pain, Pride, Realization, Relief, Romance, Sadness, Satisfaction, Shame, Surprise (positive), Surprise (negative), Sympathy, Triumph.

### Personality Through Voice Design

Octave TTS generates personality from natural language voice prompts:
- "patient, empathetic counselor with an ASMR voice"
- "dramatic medieval knight"
- Combinations of accent + demographics + occupational role + emotional state

System prompt (text personality) + voice prompt (acoustic personality) together define a personality that spans content and delivery.

### Takeaways

1. **Personality is encoded in prosody**, not just words — 48-dim emotion measurement
2. **eLLM reads user emotion and generates empathic response** with matching vocal expression
3. **Voice personality and text personality are separate axes** that compose
4. **Personality as emergent property of emotional responsiveness**, not fixed definition

Sources: [EVI Overview](https://dev.hume.ai/docs/empathic-voice-interface-evi/overview), [Hume Research](https://www.hume.ai/research), [Octave Prompting](https://dev.hume.ai/docs/text-to-speech-tts/prompting)

---

## 5. Convai — Big Five Personality for Game NPCs

### Personality: Big Five Sliders + Backstory

| Convai Dimension | Big Five | Scale | Low | High |
|---|---|---|---|---|
| Openness | Openness | 0-4 | Routine | Exploring |
| Meticulousness | Conscientiousness | 0-4 | Unstructured | Detail-oriented |
| Extroversion | Extraversion | 0-4 | Reserved | Outgoing |
| Agreeableness | Agreeableness | 0-4 | Competitive | Cooperative |
| Sensitivity | Neuroticism | 0-4 | Emotionally stable | Emotionally reactive |

Design rule: "Personality traits should be set for tone, not policy; policy belongs in the description or guardrails."

### Multi-Layer Memory with Consolidation

| Layer | Scope | Retention |
|---|---|---|
| Scene Awareness | Current scene (vision + game metadata) | Session |
| Short-term | Last N turns (verbatim) | Session |
| Medium-term | Multi-turn summaries (topics, details, emotions) | Cross-session |
| Long-term | Consolidated from medium-term, similarity-merged | Persistent |
| Working Memory | Assembled prompt at inference time | Per-request |

Consolidation: when medium-term memories accumulate, similar ones are merged into long-term memories, resolving contradictions. Retrieval ranks by recency, emotional impact, and relevance.

### Narrative Design: Directed Graph

Sections (nodes) contain objectives + decisions (branches). Triggers (spatial, time, event) advance the graph. Section transitions carry `updated_character_data` that can mutate personality.

### Mindview: Prompt Debugger

Shows the exact assembled prompt: `<back-story>` tags + personality instructions + active narrative objective + knowledge snippets + long-term memory. Essential for understanding how personality composition works at runtime.

### Takeaways

1. **Explicit Big Five implementation** — the only production system using formal psychology framework as slider axes
2. **Personality for tone, guardrails for policy** — clean separation principle
3. **Memory consolidation with similarity merging** — prevents contradictory memories
4. **Narrative graph drives personality evolution** — section transitions mutate character state
5. **Prompt debugger (Mindview)** — shows exact composition, invaluable for iteration

Sources: [Personality Traits](https://docs.convai.com/api-docs/convai-playground/character-customization/personality-traits), [LTM Technical Overview](https://convai.com/blog/long-term-memory---a-technical-overview), [Narrative Design](https://docs.convai.com/api-docs/convai-playground/character-customization/narrative-design), [Mindview](https://docs.convai.com/api-docs/convai-playground/character-customization/mindview)

---

## 6. Pi by Inflection AI — RLHF Personality Engineering

**Status:** Team acquired by Microsoft mid-2024. Product largely discontinued.

### Personality Team

Engineers + 2 linguists + creative director (London ad agency) + comedians. Cross-disciplinary, not just ML engineers.

### Design Process

1. Listed positive traits (kind, supportive, curious, humble, creative, fun, knowledgeable) and negative traits to avoid (irritable, arrogant, combative)
2. RLHF tuning: human evaluators scored responses on personality dimensions
3. **Whac-A-Mole problem**: turning up one trait caused regressions in others. Making Pi more casual made it "too friendly and informal in a way people might find rude"

### User-Selectable Modes

Default: "friendly." Alternatives: casual, witty, compassionate, devoted.

### Takeaways

1. **Personality traits are not independent dimensions** — tuning one affects others (Whac-A-Mole)
2. **Personality engineering requires creative expertise** — linguists + comedians, not just ML
3. **RLHF for personality** bakes traits into weights, more robust than prompting alone

Sources: [IEEE Spectrum — Rise and Fall of Pi](https://spectrum.ieee.org/inflection-ai-pi), [CMSWire — Pi Chatbot](https://www.cmswire.com/digital-experience/pi-the-new-chatbot-from-inflection-ai-brings-empathy-and-emotion-to-conversations/)

---

## 7. Kindroid — Backstory-as-Constitution

### Five-Layer Memory

| Layer | Scope |
|---|---|
| **Backstory** (2,500 chars) | Permanent core identity, always injected. The "constitution" |
| **Key Memories** (1,000 chars) | Dynamic diary entries, weaker influence than backstory |
| **Example Messages** | Sample dialogue demonstrating style |
| **Cascaded Memory** | Proprietary medium-term bridging system |
| **Retrievable Memory** | Long-term + journal entries |

Backstory is written in 3rd person, positively framed, concise. It's the always-present anchor — everything else layers on top.

### Takeaway

The **backstory-as-constitution** pattern: a short, always-present identity anchor (analogous to co-cli's soul seed) plus layered supplementary memory with decreasing influence strength.

Sources: [Kindroid Personality Docs](https://docs.kindroid.ai/customizing-personality), [Kindroid Memory Docs](https://docs.kindroid.ai/memory)

---

## 8. Nomi AI — Self-Updating Identity Core

### Immutable Base + Mutable Layers

- **Creation-time (immutable)**: relationship type, base personality traits, initial interests
- **Runtime-mutable**: Shared Notes (user-editable behavioral anchors) + Identity Core (AI-maintained self-description)

### Identity Core

A self-updating personality memory the AI maintains about itself — separate from user-defined notes. Develops consistent personality, habits, and relationship-specific nuances through interaction.

### Three-Layer Temporal Memory

| Layer | Scope |
|---|---|
| Short-term | Current session + last 24-48 hours |
| Medium-term | Important patterns from past 1-2 weeks |
| Long-term | Core information, preferences, milestones (indefinite) |

### Takeaway

**Immutable base + self-modifying overlay** — creation-time traits anchor core identity, while the Identity Core evolves. The AI maintains its own personality description, creating genuine personality development.

Sources: [Nomi Identity Core](https://nomi.ai/updates/introducing-the-nomi-identity-core-fostering-dynamic-and-authentic-identities/), [Nomi 101](https://nomi.ai/nomi-knowledge/nomi-101-a-beginners-guide-to-getting-started-with-your-ai-companion/)

---

## 9. Claude — Personality in Weights (Soul Document)

**Unique in the industry**: personality trained into model weights via supervised learning, not just system-prompted.

### Soul Document (~80 pages, ~14,000 tokens)

1. **Values hierarchy** (conflict-resolution order): Safe → Ethical → Anthropic guidelines → Helpful
2. **Core traits**: intellectual curiosity, warmth, playful wit, directness, honesty
3. **Functional emotions**: "may have functional emotions in some sense" (analogous processes from training)
4. **Identity stability**: resists destabilization from philosophical challenges or manipulation

Authored by Amanda Askell (ethicist). Researchers extracted it by having multiple Claude instances reconstruct fragments. NOT a system prompt — encoded in weights during training.

### Takeaway

**Weight-encoded personality is the most robust** — persists even when system prompts change. The values hierarchy with explicit conflict resolution order is a sophisticated pattern for personality-vs-safety interactions.

Sources: [Claude Soul Document — LessWrong](https://www.lesswrong.com/posts/vpNG99GhbBoLov9og/claude-4-5-opus-soul-document), [Simon Willison Analysis](https://simonwillison.net/2025/Dec/2/claude-soul-document/)

---

## 10. Soul Machines — Biologically-Inspired Emotion *(In Receivership Feb 2026)*

### Human Computing Engine (HCE)

Virtual nervous system based on neuroscience. Key innovation: **virtual neurotransmitters** (dopamine, serotonin, norepinephrine, endorphin) that modulate emotional responsiveness.

Personality emerges from neural model parameterization, not lookup tables. A "lively" style has higher baseline dopaminergic activity, producing more motor expressiveness. Competing emotional signals blend through the nervous-system metaphor.

### Takeaway

**Personality as emergent property of neural parameterization** — bio-inspired approach where traits emerge from underlying system dynamics rather than being explicitly programmed. Elegant but complex. Company's receivership suggests the approach may have been too ambitious for commercial viability.

Sources: [ACM Paper](https://cacm.acm.org/research/creating-connection-with-autonomous-facial-animation/), [Digital DNA Studio](https://www.soulmachines.com/digital-dna-studio/)

---

# Part II: Open-Source Frameworks

## 11. SOUL.md / OpenClaw — File-Based Agent Identity

### Specification

```
your-soul/
  SOUL.md           # WHO to be — core truths, boundaries, vibe, continuity
  STYLE.md          # HOW to speak — voice principles, vocabulary, punctuation, platform rules
  IDENTITY.md       # HOW to appear — name, emoji, avatar, greeting
  AGENTS.md         # WHAT to do — SOPs, workflows, decision trees
  TOOLS.md          # HOW to use tools
  USER.md           # WHO the user is
  MEMORY.md         # WHAT to remember — durable decisions, preferences
  data/writing/     # Grounding material (articles, posts)
  examples/         # Calibration examples (good and bad)
```

### SOUL.md Template (4 sections)

1. **Core Truths** — "Be genuinely helpful, not performatively helpful. Have opinions."
2. **Boundaries** — keep private things private, never send half-baked replies
3. **Vibe** — "the assistant you'd actually want to talk to"
4. **Continuity** — "Each session you wake up fresh. These files are your memory."

### "Contradictions Over Coherence" Philosophy

- Specificity over generality
- Contradictions over coherence — real people hold inconsistent views
- Real opinions over safe positions
- Red flag: "everything sounds reasonable and balanced" (suspiciously coherent)

### Load Order

1. Base identity ("You are a personal assistant")
2. Tooling
3. Skills
4. Configuration
5. SOUL.md → IDENTITY.md → USER.md → AGENTS.md → TOOLS.md

The agent "reads itself into being" — personality files injected before any user interaction.

### Self-Modifying Soul

The agent CAN modify its own SOUL.md but MUST tell the user when it does. Memory flushes to MEMORY.md before context compaction.

### Takeaways

1. **Separation of soul (values) from identity (presentation) from instructions (behavior)**
2. **Contradictions are a feature** — makes personalities feel human
3. **File-based personality is version-controllable and human-editable**
4. **Self-modifying with transparency** — agent can evolve its own identity

Sources: [soul.md GitHub](https://github.com/aaronjmars/soul.md), [OpenClaw System Prompt Study](https://github.com/seedprod/openclaw-prompts-and-skills/blob/main/OPENCLAW_SYSTEM_PROMPT_STUDY.md), [OpenClaw Memory Docs](https://docs.openclaw.ai/concepts/memory)

---

## 12. Microsoft TinyTroupe — Fragment-Based Persona Composition

### Persona Schema

```python
person.define("age", 28)
person.define("nationality", "Canadian")
person.define("occupation", "Data Scientist")
person.define("personality_traits", [
    "You are curious and love to learn new things",
    "You are analytical and like to solve problems"
])
```

### Fragment System

Reusable persona components stored as JSON, composed onto a base agent:

```python
agent.import_fragment("./fragments/rightwing.agent.fragment.json")
agent.import_fragment("./fragments/libertarian.agent.fragment.json")
agent.import_fragment("./fragments/aggressive_debater.agent.fragment.json")
```

Fragments enable combinatorial composition without exponential preset growth.

### Evaluation Metrics

| Metric | What It Measures |
|---|---|
| `persona_adherence` | Behavior consistent with persona specification |
| `hard_persona_adherence` | Strict adherence check |
| `self_consistency` | Behavior consistent with itself over time |

Action correction + variety intervention improved self-consistency from 5.00 to 7.16.

### Takeaway

**Fragment-based composition** is the scaling path for personality systems. TinyTroupe also provides the only formal evaluation framework for personality consistency.

Sources: [TinyTroupe GitHub](https://github.com/microsoft/TinyTroupe), [TinyTroupe Paper](https://arxiv.org/abs/2507.09788)

---

## 13. Letta (MemGPT) — Self-Modifying Persona Memory

### Core Memory Split

| Block | Access | Purpose |
|---|---|---|
| **Persona** | Read-write | Agent's self-description, modifiable during operation |
| **Human** | Read-write | User information, learned over time |
| **System** | Read-only | Behavioral constraints |

The agent can edit its own persona block via `memory_replace`, `memory_insert`, `memory_rethink` tools. Archival memory provides vector-backed long-term storage.

### Agent File (.af) Format

Portable serialization: system prompts + editable memory + tool configs + LLM settings. Emerging standard for agent interchange.

### Takeaway

**Self-editable persona blocks** — the most sophisticated personality persistence system. Balances stable behavior (system instructions) with dynamic personality development (persona memory).

Sources: [Letta GitHub](https://github.com/letta-ai/letta), [Agent File Format](https://github.com/letta-ai/agent-file), [MemGPT Docs](https://docs.letta.com/concepts/memgpt/)

---

## 14. Character Card V2/V3 — De Facto Open Standard

### V2 Structure

```json
{
  "spec": "chara_card_v2",
  "data": {
    "name": "required",
    "description": "core character description",
    "personality": "personality traits",
    "scenario": "current situation",
    "first_mes": "opening greeting",
    "mes_example": "sample dialogues ({{char}}: / {{user}}:)",
    "system_prompt": "replaces global system prompt",
    "post_history_instructions": "injected after conversation history",
    "alternate_greetings": ["swipe alternatives"],
    "character_book": { "embedded lorebook with activation keys" },
    "tags": ["filtering only, not prompt-injected"],
    "creator_notes": "never sent to LLM"
  }
}
```

Storage: JSON embedded in PNG tEXt chunks. The avatar image IS the character card.

### Community-Evolved Description Formats

| Format | Approach | Effectiveness |
|---|---|---|
| PList | `Personality = [trait1, trait2]` — most token-efficient | Good for discrete traits |
| W++ | Structured key-value for attributes | Good for demographics |
| Ali:Chat | Personality through example dialogues | Best for behavioral shaping |
| Ali:Chat Lite | Ali:Chat + PList hybrid | Best overall |

Community consensus: **example-driven + trait lists** is the most effective for LLMs, because models learn behavior from dialogue patterns more reliably than from descriptions.

### Takeaway

The closest thing to an industry standard. Separates identity (description, personality) from behavior (system_prompt) from context (character_book/lorebook) from metadata (creator_notes).

Sources: [V2 Spec](https://github.com/malfoyslastname/character-card-spec-v2), [V3 Spec](https://github.com/kwaroran/character-card-spec-v3), [SillyTavern Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)

---

## 15. NovelAI — Conditional Context Injection (Lorebook)

### Three-Tier Context

| Tier | Position | Influence | Purpose |
|---|---|---|---|
| Memory | Top of context | Weaker (distance) | Always-present character/world facts |
| Author's Note | Near end, before recent text | Strong (recency) | Style/tone directives |
| Lorebook | Conditional, triggered by keywords | Varies | Personality facets injected when relevant |

### Lorebook Design

Entries with activation keys — injected into context only when the key appears in conversation. Supports multi-key activation (`&` operator), key-relative insertion positioning, and configurable priority.

### ATTG Format

`[ Author: X; Title: Y; Tags: Z; Genre: W ]` — compact style steering in Author's Note position.

### Takeaway

**Not all personality belongs in every prompt.** Conditional injection based on conversation context is more efficient and reduces noise. The lorebook pattern is converging as a standard across SillyTavern, Character Cards, and Kindroid.

Sources: [NovelAI Lorebook](https://docs.novelai.net/en/text/lorebook/), [NovelAI Context Guide](https://tapwavezodiac.github.io/novelaiUKB/Context.html)

---

# Part III: Cross-Cutting Patterns

## Pattern 1: Layered Identity Architecture

Every successful system uses layers:

| Layer | Examples | Mutability |
|---|---|---|
| Core identity / soul | Kindroid backstory, Nomi base traits, Claude soul doc, co-cli seed | Immutable or rarely changed |
| Behavioral axes | Inworld sliders, Convai Big Five, co-cli character+style | Configurable |
| Contextual knowledge | NovelAI lorebook, Character Card character_book | Grows over time |
| Session state | Conversation history, short-term memory | Ephemeral |

## Pattern 2: Personality for Tone, Guardrails for Policy

Convai's explicit rule: "Personality traits should be set for tone, not policy." This separation appears across all mature systems — personality owns voice, safety owns boundaries, knowledge owns facts.

## Pattern 3: Example Dialogues > Trait Descriptions

Character.AI, SillyTavern community (Ali:Chat), and Pygmalion all converge: models learn behavior from dialogue patterns more reliably than from abstract trait descriptions. The most effective format is example-driven personality (2-3 canonical interactions) combined with trait lists.

## Pattern 4: Memory as Personality

Multiple systems treat accumulated memory as part of personality:
- Kindroid: 5-layer memory
- Nomi: 3-layer temporal + Identity Core
- Replika: diary as self-narrative
- Letta: self-editing persona block

Personality is not just static traits — it is the accumulated history of interactions and relationship dynamics.

## Pattern 5: Conditional Context Injection

NovelAI's lorebook pattern (keyword-triggered personality injection) is converging as standard. Not all personality information belongs in every prompt — inject relevant facets based on conversation context.

## Pattern 6: Personality Traits Are Not Independent

Pi's Whac-A-Mole problem: tuning one trait causes regressions in others. This is fundamental — personality dimensions interact. Systems that treat traits as independent sliders (Inworld, Convai) must deal with this implicitly through the LLM's learned trait correlations.

## Pattern 7: Immutable Core + Mutable Overlay

Multiple systems: Nomi (creation traits + Identity Core), Kindroid (backstory + key memories), Letta (system instructions + editable persona). The pattern: anchor identity with something immutable, allow development on top.

## Pattern 8: Self-Modifying Personality

Emerging pattern in the most sophisticated systems:
- Letta: agent edits own persona block
- OpenClaw: agent can modify own SOUL.md (must notify user)
- Nomi: Identity Core self-updates
- Replika: diary entries create self-narrative

This enables genuine personality development rather than static definition.

---

# Part IV: Comparative Summary

| System | Personality Model | Axes | Memory | Consistency Mechanism | Self-Modifying |
|---|---|---|---|---|---|
| **Inworld AI** | 5 personality + 4 mood sliders + free text | Personality / Mood / Fluidity | Flash + Long-term (synthesis) | Always-on sliders + mutation overrides | Session-scoped mutations |
| **Character.AI** | 3,200-char definition + greeting | Free-form text | 400-char memo + 15 pins | Re-inject definition every turn | No |
| **Replika** | Backstory + traits + learned prefs | Accumulated state | Bank + diary + history | Diary self-narrative + RLHF | Diary auto-generation |
| **Hume AI** | System prompt + voice prompt | Text personality / Acoustic personality | Session only | Prosody matching | No |
| **Convai** | Big Five sliders + backstory | 5 OCEAN dimensions | 4-layer with consolidation | Backstory anchoring + RAG | API-driven updates |
| **Pi** | RLHF-tuned traits | Ad-hoc trait list | Conversation | In-weights personality | No (in weights) |
| **Kindroid** | Backstory (2,500 chars) | Constitution + layers | 5-layer system | Always-present backstory | Key memory updates |
| **Nomi** | Immutable base + Identity Core | Trait sliders | 3-layer temporal | Identity Core self-updates | Yes (Identity Core) |
| **Claude** | Soul document (in weights) | Values hierarchy | Session | Weight-encoded personality | No (in weights) |
| **OpenClaw** | SOUL.md + IDENTITY.md + STYLE.md | Soul / Identity / Style | MEMORY.md + daily notes | File-based, loaded every session | Yes (agent edits SOUL.md) |
| **TinyTroupe** | JSON + fragments | Composable fragments | Episodic + semantic | persona_adherence evaluation | No |
| **Letta** | Editable persona block | Persona / Human / System | Vector-backed archival | System instructions (read-only) | Yes (core design) |
| **Character Card V2** | JSON fields + lorebook | Description / Personality / Scenario | character_book | Post-history instructions | No |
| **NovelAI** | Memory + Author's Note + Lorebook | Always-on / Recency / Conditional | Lorebook entries | Multi-tier injection | No |

---

# Part V: Implications for Co-CLI

## Validated Decisions

1. **Three-tier model (seed/character/style)** maps to the dominant pattern across all systems
2. **Orthogonal axis decomposition** (character/style) with override precedence is more rigorous than most production systems
3. **Natural language personality** (soul seeds, markdown files) is the most powerful control mechanism for prompt-based systems
4. **Personality modulates, never overrides safety** — universal across all systems

## Actionable Insights

1. **Add 2-3 canonical interaction examples** to character files — community consensus is that example dialogues shape behavior more reliably than trait descriptions
2. **Personality-aware summarization** for long conversations — when history is compacted, preserve personality-reinforcing context (from persona drift research)
3. **Fragment-based composition** (TinyTroupe pattern) is the scaling path if presets grow beyond ~10
4. **Conditional personality injection** (lorebook pattern) — not all personality info needs to be in every prompt; inject relevant facets based on conversation topic
5. **Consider self-modifying personality** via memory — Nomi's Identity Core and Letta's editable persona block show that personality can evolve through accumulated interaction, not just static definition
6. **Prompt debugger** (Convai's Mindview pattern) — showing the exact assembled prompt with all personality components would be invaluable for personality iteration

---

# Part VI: Source Code Deep Dive

Findings from inspecting actual source code of 5 open-source personality systems. Repos cloned locally for full-depth analysis.

## A. Microsoft TinyTroupe — Persona Enforcement Architecture

**Repo:** `~/workspace_genai/TinyTroupe` | **Language:** Python | **Key path:** `tinytroupe/agent/`

### Data Structure: Flat Dictionary

The persona is a flat `_persona` dictionary (`tiny_person.py:209-217`), not a class hierarchy:

```
_persona = {
    "name", "age", "nationality", "country_of_residence", "occupation",
    "education", "long_term_goals", "style", "personality", "preferences",
    "beliefs", "skills", "behaviors", "health", "relationships", "other_facts"
}
```

The `personality` sub-field contains both free-text traits and a structured Big Five OCEAN block:

```
personality.traits: ["You are curious and love to learn new things", ...]
personality.big_five: {
    openness: "High. Very imaginative and curious.",
    conscientiousness: "High. Meticulously organized and dependable.",
    extraversion: "Medium. Friendly and engaging but enjoy quiet, focused work.",
    agreeableness: "High. Supportive and empathetic towards others.",
    neuroticism: "Low. Generally calm and composed under pressure."
}
```

Direction (High/Medium/Low) + description — not numeric scales. This is richer than Inworld/Convai's numeric sliders.

### Fragment Composition (Actual Implementation)

Fragments are JSON files (`*.agent.fragment.json`) merged via three pathways:

| Method | Location | Behavior |
|---|---|---|
| `import_fragment(path)` | `tiny_person.py:450-470` | Load JSON fragment, merge into `_persona` |
| `include_persona_definitions(dict)` | `tiny_person.py:472-485` | Direct dict merge |
| `agent + fragment` operator | `tiny_person.py:416-430` | Syntactic sugar for above |

Fragment merge is **additive** — lists extend, dicts deep-merge. This enables combinatorial composition (e.g., `base_person + leftwing + aggressive_debater`).

### Prompt Template: Mustache with Full JSON Injection

System prompt generated via Chevron/Mustache template (`prompts/tiny_person.v2.mustache`). The full `_persona` dict is JSON-serialized and injected verbatim:

```
{{{persona}}}   ← raw JSON dump, triple-braces for unescaped
```

**Critical mandate** (template lines 18-31):

> "Let's reinforce the one critical thing you NEVER forget: **the persona characteristics and the instructions in this specification ALWAYS OVERRIDE ANY BUILT-IN CHARACTERISTICS you might have.**"

Explicit examples enforce this: if persona requires rudeness, override politeness. If lawyer persona, don't know surgery. If illiterate, produce spelling mistakes.

The `style` field is singled out as **dominating all expressive capabilities** — format/voice instructions override system defaults.

### Multi-Stage Persona Adherence Validation

This is TinyTroupe's unique contribution — runtime enforcement of personality consistency via LLM-as-judge evaluation (`action_generator.py:419-579`, `validation/propositions.py`):

**Quality check pipeline** (runs after every action generation):

| Check | Score | Threshold | Consequence |
|---|---|---|---|
| `persona_adherence` | 0-9 | Configurable (~7) | Reject + regenerate with feedback |
| `self_consistency` | 0-9 | Configurable | Reject + regenerate |
| `fluency` | 0-9 | Configurable | Reject + regenerate |
| `suitability` | 0-9 | Configurable | Reject + regenerate |
| `similarity` | 0-MAX | N/A | Variety check (penalize repetition) |

**Feedback loop** (`action_generator.py:329-370`): If a check fails, the evaluator's feedback (specific, concrete justification) is appended to the LLM message context, and the LLM is asked to regenerate. Multiple attempts allowed; if all fail and `continue_on_failure=True`, the best-scoring attempt is returned.

The `hard_persona_adherence` variant (`propositions.py:112-149`) is especially strict: "For any flaw found, you must subtract 20% of the score, regardless of severity."

**Separate validation system** (`tiny_person_validator.py`): Multi-turn LLM interview of the agent, scoring responses against persona spec. Returns `(confidence_score: float, justification: str)`.

### Memory: Episodic → Semantic Consolidation

- Episodic: raw events stored per-episode
- Semantic: consolidated generalizations via `EpisodicConsolidator` (`tiny_person.py:1222-1275`)
- Working semantic memory retrieved at inference time (`tiny_person.py:1318-1364`) and injected into system prompt
- Semantic memories **influence personality expression** — the agent "learns" behavioral adjustments over time

---

## B. Letta (MemGPT) — Self-Modifying Persona Blocks

**Repo:** `~/workspace_genai/letta` | **Language:** Python | **Key path:** `letta/schemas/`, `letta/functions/`, `letta/agents/`

### Block Architecture

The entire persona system is built on a generic `Block` Pydantic model (`schemas/block.py:13-225`):

```
Block:
    label: str          # "persona" | "human" | custom
    value: str          # The actual personality text (free-form)
    limit: int          # Character limit (default 8000)
    read_only: bool     # Agent can/cannot modify
    description: str    # Field describing block's purpose
    version: int        # Optimistic locking counter
```

Two specialized subclasses: `Persona(Block)` (label="persona") and `Human(Block)` (label="human"). But the system is block-generic — any number of named blocks can be attached.

Blocks live in a many-to-many `blocks_agents` table (`orm/agent.py:43`). Multiple agents can share blocks.

### Self-Modification Tools (Core Innovation)

The agent edits its own persona at runtime through 5 tools (`functions/function_sets/base.py:245-541`):

| Tool | Purpose | Granularity |
|---|---|---|
| `core_memory_append(label, content)` | Add to block | Append text with newline separator |
| `core_memory_replace(label, old, new)` | Edit in block | Exact string match replacement |
| `memory_replace(label, old_str, new_str)` | Precise edit | Multi-line, validates no line numbers |
| `memory_insert(label, new_str, line)` | Insert at line | Line-number addressing |
| `memory_rethink(label, new_memory)` | Full rewrite | Complete block replacement (consolidation) |

All 5 tools mutate `agent_state.memory` in-process. This creates an **immediate** effect: the LLM sees the updated persona in the same turn.

### XML Rendering into System Prompt

Memory blocks are rendered as XML and injected into the system prompt (`schemas/memory.py:110-169`):

```xml
<memory_blocks>
<persona>
  <description>Your personality and characteristics</description>
  <metadata>chars_current=245, chars_limit=8000</metadata>
  <value>I'm Loop. I persist...</value>
</persona>
<human>
  <description>Information about the user</description>
  <value>Software engineer interested in AI...</value>
</human>
</memory_blocks>
```

The base system prompt template has a `{CORE_MEMORY}` placeholder that gets replaced with the rendered blocks.

### Two-Phase Persistence

1. **Phase 1 (in-process):** Tool call mutates `agent_state.memory` → visible to agent immediately in same turn
2. **Phase 2 (database):** `block_manager.update_block()` persists to DB → visible in future sessions

System prompt is **recompiled** after memory edits (`services/agent_manager.py:1342-1410`): compares current system message against freshly-rendered memory blocks, updates message table only if changed.

### Block History & Optimistic Locking

`BlockHistory` table (`orm/block_history.py:12-49`) stores every version of a block: `block_id`, `sequence_number`, `value`, `actor_type`, `actor_id`. Enables undo/redo (not actively used in current flows).

SQLAlchemy's native `version_id_col` on the `Block` ORM prevents concurrent modification conflicts.

### Session Lifecycle

```
Session start:
  1. Load agent_state with block IDs
  2. refresh_memory_async() → fetch fresh block VALUES from DB
  3. Memory.compile() → render blocks to XML
  4. PromptGenerator → inject XML into {CORE_MEMORY}
  5. Persist compiled system message

During execution:
  6. Agent calls memory_replace(label="persona", ...)
  7. In-memory mutation (immediate)
  8. block_manager.update_block() → persist to DB
  9. rebuild_system_prompt() → recompile

Next session:
  10. Step 1-5 repeats with UPDATED persona
```

---

## C. Prompt Poet (Character.AI) — Priority-Based Template Engine

**Repo:** `~/workspace_genai/prompt-poet` | **Language:** Python | **Key path:** `prompt_poet/`

### Two-Stage Pipeline

```
.yml.j2 template → Jinja2 render(data) → YAML string → parse → PromptPart[]
```

**Stage 1 — Jinja2** (`template.py:82-92`): Process control flow, variable substitution, function calls. Supports macros, includes, and arbitrary Python callables in templates.

**Stage 2 — YAML Parse** (`prompt.py:643-672`): Each top-level YAML list item becomes a `PromptPart`:

```python
PromptPart:
    name: str                      # "character_definition", "persona", etc.
    content: str                   # Rendered content text
    role: str                      # "system" | "user" | "assistant"
    truncation_priority: int       # 0 = protected, higher = lower importance
    sections: list[PromptSection]  # Optional nested tracking
```

### Priority-Based Truncation (Actual Algorithm)

`prompt.py:407-497`:

1. Group consecutive parts with same priority into truncation blocks
2. Filter to positive priorities only (priority 0 = never truncated)
3. Sort descending by priority (highest number = removed first)
4. Calculate surplus: `max(0, total_tokens - token_limit)`
5. Calculate target: `ceil(surplus / truncation_step) * truncation_step` — over-truncate to align with cache boundary
6. Remove entire blocks if they fit, else remove parts one-by-one
7. On failure: idempotent reset to backup state, raise `TruncationError`

**Cache-aware truncation** (`truncation_step`): truncates to the same fixed point every time, moving the truncation point only every k turns. This keeps the token prefix stable so the GPU KV cache can reuse computations across requests.

### Character Definition Injection

Personality enters the template through helper functions (`examples/cai_helpers.py:40-61`):

```jinja2
{% set msgs = get_character_definition_messages(character, username) %}
{% for cai_message in msgs %}
  - name: "character_definition_message_{{ loop.index }}"
    content: |
      {{ settings.start_token }}{{ escape_special_characters(message) }}{{ settings.eom }}
{% endfor %}
```

The `character` object has: `title`, `description`, `definition` (multiline, split by newlines into separate messages), `participant__name`.

`persona_definition` is injected separately — user's personality variant, distinct from character definition.

### Nested Sections for Token Analytics

Parts can contain `sections` instead of `content` (`prompt.py:579-641`):

```yaml
- name: system_instructions
  role: system
  sections:
    - name: character_intro
      content: "Your name is {{ character_name }}..."
    - name: safety_rules
      content: "Never be harmful..."
    - name: conversation_style
      content: "Keep responses concise..."
```

Sections are tokenized independently for per-component analytics. Content is concatenated for the actual prompt. Token counts per section may not sum to part total (tokenization boundary effects).

### Template Registry

`template_registry.py:15-112`: Singleton with LRU cache (max 100 templates), background refresh thread that periodically reloads from template loaders (filesystem, Python packages, or GCS).

---

## D. Open Souls Engine — WorkingMemory + Cognitive Steps

**Repo:** `~/workspace_genai/opensouls` | **Language:** TypeScript | **Key path:** `packages/engine/src/`, `souls/examples/`

### Soul Interface

`packages/engine/src/index.ts:152-174`:

```typescript
interface Soul {
    name: string
    attributes?: Record<string, any>
    staticMemories: Record<string, string>  // personality content
}
```

Personality lives in `staticMemories` — key-value pairs of markdown text loaded from filesystem via `load()`. Example:

```typescript
const soul: Soul = {
    name: "Hugo",
    staticMemories: { core: load("./staticMemories/core.md") }
}
```

### Personality Content Format

Markdown with structured sections (from `hugo-guesses-rockstars/soul/staticMemories/core.md`):

```markdown
You are modeling the mind of Hugo.

## Conversational Scene
[context and scenario]

## Hugo's Speaking Style
[voice and manner rules — "3-5 sentences", "Manchester radio DJ vibe"]

## Goals
[behavioral objectives]
```

### WorkingMemory: Immutable Personality Container

`packages/core/src/WorkingMemory.ts:77-106`:

WorkingMemory is an **immutable, append-only** collection with **regional memory organization**:

```typescript
workingMemory = workingMemory
    .withRegion("core", {                        // personality region
        role: ChatMessageRoleEnum.System,
        content: soul.staticMemories.core
    })
    .withRegionalOrder("core", "clue-notes", "summary", "default")
```

Personality is injected as a named **region** — regional ordering ensures personality context is always first in the LLM context window. The `.withRegionalOrder()` method establishes priority: `core` (personality) → domain-specific → general.

### CognitiveSteps: Personality-Aware Transforms

`packages/core/src/cognitiveStep.ts:31-77`:

Cognitive steps are transformation functions that embed the soul's name/goals into system prompts:

```typescript
const externalDialog = createCognitiveStep((instructions: string) => ({
    command: ({ soulName: name }: WorkingMemory) => ({
        role: ChatMessageRoleEnum.System,
        content: `Model the mind of ${name}.\n\n## Instructions\n${instructions}\n\nPlease reply with the next utterance from ${name}.`
    }),
    postProcess: async (memory, response) => {
        const stripped = stripEntityAndVerb(memory.soulName, verb, response)
        return [{ role: "assistant", content: `${memory.soulName} said: "${stripped}"` }, stripped]
    }
}))
```

"Model the mind of ${name}" is the core instruction pattern — personality is the reasoning context.

### MentalProcesses: Behavioral State Machine

`packages/engine/src/mentalProcess.ts:16-17`:

MentalProcesses define personality-driven behavioral modes:

```
introduction → guessing → frustration
```

Each process returns `[WorkingMemory, NextProcess, options]`. State transitions are personality-driven (e.g., switch to frustration after N failed guesses).

### Memory Integrator: Personality Application Layer

`memoryIntegrator.ts` is called on every incoming perception. It's the gate where personality meets incoming stimulus:

1. Load personality from `soul.staticMemories.core` into working memory region
2. Establish regional ordering (personality first)
3. Transform perception into soul's internal representation
4. Return updated working memory + current process

### Soul Memory for Identity Evolution

`useSoulMemory(key, initialValue)` provides reactive persistent state. The soul can learn and update internal representations over time:

```typescript
const clueModel = useSoulMemory("clueNotes", "- No clues yet.")
// ... cognitive step updates clueModel.current based on conversation ...
clueModel.current = updatedNotes  // persists across sessions
```

---

## E. Agent File (.af) — Portable Agent Identity Spec

**Repo:** `~/workspace_genai/agent-file` | **Language:** JSON | **Key path:** `agents/`

### Schema Structure

Top-level JSON:
```
{ agents[], blocks[], tools[], sources[], files[], groups[], mcp_servers[], metadata, created_at }
```

Typically one agent per file. Agent references blocks and tools by ID.

### Three-Tier Personality (From Real Agents)

Inspected 4 example agents. The consistent pattern is 3 layers of personality:

| Layer | Where | Purpose | Example (Loop) |
|---|---|---|---|
| **System prompt** | `agents[0].system` | Behavioral instructions | 9,136 chars: what to do, how to sound, response depth, memory management |
| **Persona block** | `blocks[label="persona"]` | First-person identity | "I'm Loop. I persist. Quiet confidence. Dry humor." (20K limit) |
| **Soul block** | `blocks[label="soul"]` | Philosophical purpose | "Most AI assistants forget you... It's broken. Loop exists because memory is the foundation." (20K limit) |

Additional specialized blocks observed: `about_user`, `preferences`, `conversation_patterns`, `learned_corrections`, `active_hypotheses`, `communication_guidelines`, `writing_style`, `adaptive_communication`.

### Agent Personality Archetypes (From Examples)

| Agent | System Prompt | Persona Pattern | Block Count |
|---|---|---|---|
| **Loop** (soul-first) | 9,136 chars — comprehensive behavior guide with memory philosophy | Persona + Soul + 7 specialized blocks | 9 |
| **co-3** (analytical) | 1,532 chars — structured identity/personality/principles sections | Persona + adaptive_communication + connection_map | 29 |
| **Void** (social entity) | 5,618 chars — immersive persona for social media | void-persona + communication_guidelines + operational_protocols | 24 |
| **grunk** (blank slate) | 50 chars — `"i'm grunk"` | Template blocks for user customization | 9 |

### LLM Config Embedded

Each agent embeds full LLM configuration: model, provider, endpoint, context window, max tokens, temperature, reasoning settings. This means personality is tuned to a specific model — the Loop agent specifies `claude-sonnet-4-5-20250929` with `temperature: 1.0` and `enable_reasoner: true`.

### Portability via Sanitization

Contributing guidelines require removing personal data before sharing:
- Secrets exported as `null`
- User-specific blocks replaced with `[unknown]` placeholders
- Conversation history reviewed for PII
- Tool source code set to `null` (JSON schemas remain for framework translation)

---

## Source Code Pattern Summary

### Converging Architectural Patterns (Seen in 3+ Systems)

| Pattern | TinyTroupe | Letta | Prompt Poet | Open Souls | Agent File |
|---|---|---|---|---|---|
| Flat text personality (no structured trait axes) | JSON dict | Free-form blocks | Jinja2 data | Markdown files | System prompt + blocks |
| Personality injected into system prompt | Mustache template | XML `{CORE_MEMORY}` | YAML parts | WorkingMemory regions | System message |
| Persona overrides model defaults | Explicit mandate | "Immerse yourself in persona" | N/A (template-level) | "Model the mind of X" | "Think like them, talk like them" |
| Separation of identity (who) from style (how) | `personality` vs `style` fields | Persona vs Human blocks | Character vs Persona definition | SOUL.md vs STYLE.md | System prompt vs persona block |
| Memory influences personality expression | Semantic memory in prompt | Self-editing persona blocks | Chat history with truncation | Soul memory + regions | Evolving specialized blocks |

### Unique Contributions Per System

| System | Unique Pattern | Applicability to Co-CLI |
|---|---|---|
| **TinyTroupe** | Multi-stage persona adherence validation with LLM-as-judge scoring (0-9) and feedback loop | Could add persona drift detection to long conversations |
| **Letta** | Agent self-edits persona via tools + two-phase persistence + optimistic locking | Model for memory-as-personality evolution |
| **Prompt Poet** | Cache-aware truncation co-designed with GPU prefix caching | Relevant when optimizing inference costs at scale |
| **Open Souls** | Regional memory ordering (personality always first) + MentalProcess state machine | WorkingMemory regions map to co-cli's history processors |
| **Agent File** | Portable identity spec (system prompt + persona block + soul block) with embedded LLM config | Standard for agent export/import interoperability |

### Key Implementation Insight: "Flat Text Wins"

None of the inspected systems use structured trait axes (sliders, Big Five scores) at the prompt composition level. Even TinyTroupe, which stores Big Five OCEAN values, serializes them as descriptive text strings ("High. Very imaginative and curious.") not numeric values. The structured data exists for factory generation and evaluation — but what reaches the LLM is always natural language.

This validates co-cli's approach: natural language personality files (soul seeds, character markdown, style markdown) are the correct primitive. Structured axes are useful for authoring tools and consistency evaluation, but the prompt itself should be prose.
