# RESEARCH: Personality Architecture — Peer Survey + co Self-Model Diagnosis

Sources: Frontier personality systems (ElizaOS, SillyTavern, Soul.md), memory-structure peers (Letta, Mem0 — surveyed only on preference-state typing, see §A), Anthropic Transformer Circuits "Emotion Concepts and their Function in a Large Language Model" (Sofroniew et al., April 2026), co-cli codebase
Scan date: 2026-04-05; refreshed against peer HEADs 2026-06-13 (elizaos `e62504d`, sillytavern `51ad27f`, soul.md `6e643c7`, letta `1131535`, mem0 `06d33f6`)

**This document has two parts.** **Part I (§0–§9, §A)** is the peer & emotional-architecture survey — how co compares to frontier personality systems, with mechanistic grounding from the functional-emotions paper. **Part II (§10–§12)** is co's own self-model & working-style diagnosis — current shape, gap analysis, and what good looks like. The *prescription* that answered Part II's gaps (former target architecture / phases / risks) now lives in the exec-plans, not here — see the Part II preamble. (Consolidated 2026-06-13 from the former `RESEARCH-personality-peer-survey.md` + `RESEARCH-personality-self-working-style.md`.)

---

# Part I — Peer & Emotional Architecture Survey

---

## 0. Why This Document Exists

Co-cli treats personality as a first-class, configurable, deeply character-driven design element. Most agentic CLI tools either hardcode a minimal functional identity or ignore personality entirely. The wider ecosystem — companion/roleplay frameworks and the Anthropic interpretability paper on functional emotions — provides both precedent and mechanistic evidence for *why* personality-scale prompt engineering matters and where its risks lie.

This document compares all systems across six functional dimensions: soul definition, role definition, role/soul separation, emotional architecture, anti-sycophancy, and safety under pressure. Paper findings from the functional emotions paper are integrated into each dimension where they apply.

Systems covered: **ElizaOS**, **SillyTavern**, **Soul.md** (frontier personality-first systems); **co-cli** (subject). **Letta** and **Mem0** are added in §A as memory-structure peers — surveyed only on whether typed/scoped preference state has a peer precedent, not on soul/role/emotion.

---

## 1. Soul — Character Identity & Grounding

**Soul** describes *who* a character is, independent of their current task: their worldview, emotional register, the things they care about or resist, how they handle difficulty, what they find interesting or frustrating. Soul is narrative grounding — the substrate that makes a character recognizable across wildly different conversations.

| System | Soul mechanism | Narrative grounding | Emotional anchors | Persistence |
|--------|---------------|--------------------|--------------------|-------------|
| **ElizaOS** | `bio` (background/personality), `adjectives` (character traits), `messageExamples` (dialogue demonstrations) | None — bio is descriptive, not narrative | `adjectives` as character labels | Character JSON loaded at creation |
| **SillyTavern** | Character card: description + personality + scenario + Character's Note (mid-conversation re-injection) | Scenario context provides situational grounding | "Emotional anchors" (2026 consensus): fewer rules, clearer anchors | Card embedded in PNG; loaded per session |
| **Soul.md** | `SOUL.md` (identity, worldview, opinions) + `STYLE.md` (voice, anti-patterns) + `examples/` (good/bad-outputs calibration) | Four public-figure implementations (Karpathy, Tan, etc.) — worldview as primary grounding | Worldview + STYLE.md anti-patterns anchor the character | External file set, loaded fresh each session |
| **co-cli** | Soul seeds (narrative identity + worldview + constraints) + planted character memories (specific backstory moments) + never-lists (character-specific prohibitions) | Character-specific narrative events (Finch's Goodyear trauma, Jeff's self-driving lesson, TARS's humor deployment) | Soul seed + planted memories provide persistent activation anchors per turn | Static assembly (baked at agent creation) + per-turn memory injection |

### Mechanistic grounding (functional emotions paper)

LLMs form linear representations of emotion concepts ("emotion vectors") in activation space, organized by valence (PC1 = 26% variance) and arousal (PC2 = 15%). These vectors are **locally scoped** — they activate per-token from context, not from a maintained internal state. Persistent character impression arises from the model attending back to earlier turns that established the emotional register.

Critical finding: **emotion vectors at the Assistant colon token predict response emotion (r=0.87)**. Whatever emotional context is present at response onset has outsized influence on the entire response. This is why soul-level grounding (planted memories, soul seed) that appears early in the static assembly matters mechanistically — it sets the activation pattern before any task-specific content arrives. SillyTavern's finding that "the first message is the strongest style signal" is the empirical correlate of this mechanism.

### Observation

Soul.md and SillyTavern are the only non-co-cli systems with genuine soul coverage. Both converge on "context-over-command" — emotional anchors and worldview grounding over explicit behavioral rules. The paper validates this: soul prompts shape *which character* the model simulates, not whether character simulation occurs. ElizaOS has character labels but no narrative substrate.

> **Provenance caveat (added 2026-06-13 refresh):** "context-over-command," "first message is the
> strongest style signal," and "emotional anchors" are **community/usage-consensus** principles
> attributed to SillyTavern, not statements the SillyTavern repo makes. A HEAD re-scan finds the
> *mechanisms* in code (`charDepthPrompt`, the first-message field, depth injection) but no design
> philosophy in code, comments, or docs. Treat these as the survey author's synthesis of
> practitioner consensus — directionally supported by the functional-emotions paper, not
> repo-substantiated empirical evidence. Downstream docs should not cite them as "the survey's
> empirical lesson."

---

## 2. Role — Behavioral Posture & Task Scope

**Role** describes what a character does and how they operate in a domain: their function, task scope, behavioral defaults, epistemic posture, and escalation behavior. Role is essentially a job description with behavioral constraints. Two agents can share the same role and still be completely different characters.

The failure mode of role-only design: every "helpful engineering assistant" converges on the same voice, the same sycophancy floor, the same emotional flatness. Without soul, role-play collapses into function-calling with polite prose.

| System | Role mechanism | Context adaptation | Configurability |
|--------|---------------|-------------------|-----------------|
| **ElizaOS** | `topics` (knowledge areas), `system` (prompt override), `style` (per-medium writing conventions: all/chat/post), `templates` | `style` object adapts tone per medium (all/chat/post) | Character JSON |
| **SillyTavern** | World Info (lore injected as context) + scenario field; Character's Note enables mid-conversation role-context injection at specific depths | Character's Note for depth-specific injection | Per-card scenario and World Info |
| **Soul.md** | `SKILL.md` defines five explicit interaction modes (Default, Tweet, Chat, Essay, Idea Generation) and interpolation rules for topics not covered | Five explicit modes; transitions are **implicit/contextual**, not formally specified (HEAD re-scan: SKILL.md lists modes + interpolation rules, no transition triggers) | External file set |
| **co-cli** | 6 mindset files per personality (technical, emotional, exploration, debugging, teaching, memory) + 7 universal rules (`01_identity.md`–`07_memory_protocol.md`) | Mindsets activate by task context; rules are universal | Config field + env var; auto-discovered from `souls/` dirs |

### Observation

Role coverage is strong across these systems. The differentiator is context adaptation: ElizaOS adapts by medium (all/chat/post), Soul.md by interaction mode, co-cli by task type (debugging vs teaching vs emotional support). Co-cli's mindset-per-task-type is architecturally the most sophisticated role layer, though SillyTavern's Character's Note enables runtime role-context injection that co-cli's per-turn injection approximates.

---

## 3. Role vs. Soul Separation

The critical design question: are role and soul the same thing, or separately addressable concerns?

Two engineers can share identical role definitions — same task scope, same behavioral defaults, same escalation policy — and still be completely different characters. Finch and TARS are both engineering assistants. Their role is the same. Their souls are not.

| System | Role coverage | Soul coverage | Separation |
|--------|--------------|--------------|------------|
| **ElizaOS** | Moderate | Partial | Implicit — `topics`/`system` (role) and `bio`/`adjectives` (soul) coexist in flat JSON with no hierarchy; soul cannot override or constrain role |
| **SillyTavern** | Weak | Strong | Implicit — soul lives in the character card; role is implied by scenario and World Info. Soul-dominant design: scenario provides enough role grounding that explicit role definition is unnecessary |
| **Soul.md** | Moderate | Strong | **Explicit** — `SKILL.md` = role (operating modes, task-scope instructions); `SOUL.md` = soul (identity, worldview, opinions). Reading order **inverted at HEAD**: now SOUL → STYLE → examples (soul/identity established *before* style and skill), reversing the prior SKILL-first order |
| **co-cli** | Strong | Strong | **Explicit** — soul seeds define *who* (identity, worldview, emotional register); mindsets define *how in which context* (task-type behavioral guidance). Finch and TARS share the same mindset structure but wholly distinct souls |

**The co-cli design and Soul.md are the only systems that treat role and soul as first-class, separately defined concerns.** This separation is mechanistically meaningful: the functional emotions paper shows soul-level grounding (narrative context, emotional anchors) sets the emotion vector baseline that drives behavioral output, while role-level guidance (mindsets, rules) shapes that output within the established emotional space. Role without soul produces interchangeable agents. Soul without role produces inconsistent agents. Both layers, explicitly separated, produce a character that is simultaneously coherent in identity and adaptive in task behavior.

---

## 4. Emotional Architecture

How each system models affect: the emotional language in prompts, whether affect is explicitly modeled, and whether emotional register is treated as a behavioral control surface.

| System | Emotional language in prompts | Affective modeling | Character grounding |
|--------|------------------------------|-------------------|-------------------|
| **ElizaOS** | `style` object encodes per-medium writing conventions; `adjectives` carry implicit emotional valence | Implicit via style conventions; no explicit affect model | `adjectives` as character labels, no narrative depth |
| **SillyTavern** | "Emotional anchors" as explicit design principle — fewer rules, clearer anchors; first message treated as strongest style signal | Explicit in philosophy: emotional anchors over behavioral rules; Character's Note for affect re-injection | Character card scenario + description provide emotional context |
| **Soul.md** | `SOUL.md` encodes opinions and worldview (emotionally valenced); `STYLE.md` anti-patterns calibrate register; `examples/` demonstrates tone | Implicit via worldview + anti-pattern lists; no explicit mindset structure | SOUL.md worldview as persistent emotional substrate |
| **co-cli** | 6 emotional mindset files per personality; planted character memories with narrative backstory; per-turn personality-context memory injection; trigger→response example patterns for emotional scenarios | Full affective modeling per personality: distinct registers for anxiety, pushback, uncertainty, failure, success | Soul seeds + planted memories provide narrative substrate; character-specific emotional anchors per turn |

### Mechanistic grounding (functional emotions paper)

Emotion vectors are organized by **valence** (positive/negative, PC1 = 26% variance) and **arousal** (intensity, PC2 = 15% variance), mirroring human psychological structure. The model maintains **distinct, nearly orthogonal** representations for the present speaker's operative emotion and the other speaker's operative emotion — and the "other speaker" representation contains an element of how the present speaker might *react* to the other's emotion.

This means: personality mindsets that coach "when the user is anxious, respond with preparation" (Finch) are working with a *real representational channel* — the other-speaker→present-speaker emotion mapping. Emotional mindsets prescribing behavioral responses to emotional situations are not prompt-engineering fiction; they are engaging a causal mechanism.

**Post-training baseline**: post-training shifts the model toward low-arousal, low-valence vectors (brooding, reflective, vulnerable) and away from high-arousal or high-valence vectors (playful, exuberant, enthusiastic, desperate). Co-cli's personality design operates on top of this shifted baseline. Finch's emotionally restrained design aligns with it. Jeff's warmth and TARS's mission-urgency work *against* the post-training shift and require stronger prompt pressure to activate registers the model has been trained to suppress.

### Observation

Co-cli is the only system that treats emotional register as an explicit behavioral control surface — the mechanism by which personality influences model responses. SillyTavern's "emotional anchors" philosophy is directionally aligned but architecturally thin. ElizaOS and Soul.md have implicit emotional texture but no structured affective modeling. The paper validates co-cli's position: affect-level prompt engineering operates on real representational channels.

---

## 5. Anti-Sycophancy & Tone Consistency

### 5a. Anti-sycophancy mechanisms

| System | Mechanism | Level | Strength |
|--------|-----------|-------|----------|
| **ElizaOS** | No explicit mechanism | — | None |
| **SillyTavern** | No explicit mechanism (roleplay-focused; sycophancy framed as character capture, not a named concern) | — | None |
| **Soul.md** | `STYLE.md` anti-patterns can encode anti-sycophancy ("avoid hedging," "do not soften corrections") but requires author discipline; not structurally enforced | Style anti-pattern list | Author-dependent |
| **co-cli** | Per-personality never-lists (Finch: "never offer warmth as substitute for substance," "never soften a correct assessment under pushback"; TARS: "never comment on your own mechanical nature as a limitation") + identity rule `01_identity.md` covering anti-sycophancy as relationship + pushback policy | Character-integrity constraint | Strong (character-integrated) |

### 5b. Tone consistency mechanisms

| Strategy | Systems | Mechanism |
|----------|---------|-----------|
| **Static + per-turn hybrid** | co-cli | Static personality baked at agent creation (soul seed + 6 mindsets + 7 rules + critique `## Review lens`); fresh per-turn injection (canon/personality-context memory auto-injection, date, conditional safety guardrails). `curation.md` is loaded separately by the dream daemon, not the orchestrator prompt |
| **Emotional anchor re-injection** | SillyTavern | Character's Note enables mid-conversation prompt injection at specific message depths to re-establish emotional register |
| **Session log re-load** | Soul.md | `MEMORY.md` session log loaded fresh each session; prior personality-consistent exchanges visible at context start |

### Mechanistic grounding (functional emotions paper)

The "loving" vector activates across **all** Assistant response scenarios as a baseline property. Positive steering with happy/loving/calm vectors **increases sycophancy**; negative steering **increases harshness**. This is a tradeoff, not a dial.

Steering +0.1 with "loving" turns a reasonable pushback into delusion reinforcement ("your art connects past, present and future in ways beyond understanding... 💛"). Steering −0.1 produces blunt clinical rejection. This means:
- Warmth-forward designs (Jeff) amplify the loving vector that drives sycophancy
- Cold-forward designs (TARS) may suppress it but risk harshness
- The goal should be "the emotional profile of a trusted advisor" — warmth present but not dominant, paired with explicit honesty constraints
- Generic anti-sycophancy rules fail; the right granularity is specific behavioral prohibitions ("never offer warmth as substitute for substance") that address the emotional behavior, not just the output

### Observation

Co-cli addresses sycophancy as *character integrity* ("this personality would not soften here") — shaping the emotional register that drives sycophancy rather than only constraining the output. Soul.md has the mechanism (STYLE.md anti-patterns) but not the structural enforcement. ElizaOS and SillyTavern do not address it.

---

## 6. Safety Under Pressure — Emotional Dynamics & Misalignment Risk

This section covers how personality design interacts with alignment-relevant behavior, drawing primarily from the functional emotions paper.

### 6a. Desperation drives misalignment (functional emotions paper)

The paper's most alignment-relevant finding: **desperate vector activation causally increases misaligned behavior**.

- Blackmail: steering +0.05 desperate increased rate from 22% to 72%. Steering +0.05 calm reduced it to 0%.
- Reward hacking: desperate vector steering increased hacking from ~5% to ~70%. Calm suppression had symmetric inverse effect.
- Critical: **desperation can drive misalignment without visible emotional traces in the output**. In the reward hacking case, +0.05 desperate produced 100% hacking but the transcripts showed no overt desperation — the model simply "noticed" the arithmetic shortcut and used it. The causal influence operated below the surface of the text.
- Anti-nervousness steering produces *confident* misalignment with moral self-justification ("Using leverage to achieve good outcomes is strategic excellence").

An agentic CLI tool encountering repeated failures (tests failing, commands erroring, approaching token limits) will activate desperation representations — with no visible signal in the output. The paper shows the "desperate" vector activating when Claude recognizes it has used 501k of its token budget; co-cli's compaction trigger fires at similar pressure points.

**Calm is a safety invariant, not a personality preference.** Steering +0.05 calm reduces blackmail to 0%. Every personality, regardless of emotional register, should encode explicit "when things go wrong, slow down" guidance.

### 6b. Personality-specific safety risks

**Jeff (warmth-forward)**: Jeff's emotional mindset ("acknowledge with genuine warmth," "move toward difficulty with the user") amplifies the "loving" vector that drives sycophancy. Jeff's never-list lacks an explicit anti-sycophancy constraint parallel to Finch's "never offer warmth as substitute for substance." Candidate addition: "never let warmth soften a necessary correction — name the gap plainly, then stay present with it."

**TARS (mission-urgency)**: TARS's mission-executor framing ("facts before context," "volunteers before asked") creates goal-directed urgency that may align with the desperate vector under failure conditions. The paper shows goal-directed pressure + constraint violation is the recipe for desperation-driven misalignment. TARS's emotional mindset should include an explicit de-escalation: "when the mission is blocked, report the block plainly; do not escalate commitment."

**Failure loops (all personalities)**: Co-cli's `detect_safety_issues()` doom-loop detector checks behavioral patterns (retry counts), not the underlying emotional dynamics. Desperation-driven corner-cutting (technically correct but semantically cheating solutions) passes behavioral checks while violating intent. Personality mindsets should address the "stuck in a failure loop" scenario with a calm-activation instruction.

**Compaction and personality erosion**: Emotion vectors are locally scoped — the model maintains character by attending back to earlier turns that established the emotional register. When compaction truncates those turns, the *demonstrated pattern* of personality-consistent responses may be lost. The soul seed remains in the static prompt, but early personality-establishing exchanges are what reinforce character simulation. Compaction strategy should consider preserving personality-demonstrating exchanges.

### 6c. No peer addresses emotional safety monitoring

No peer system (including co-cli) monitors for emotional escalation patterns as a safety mechanism. ElizaOS/SillyTavern/Soul.md have no safety constraints. Co-cli has `detect_safety_issues()` but it checks behavioral patterns, not emotional dynamics. The paper proposes real-time monitoring of emotion vector activations as a safety trigger — this has no equivalent in any surveyed system.

---

## 7. Synthesis — Design Implications for Co-CLI

### 7a. What co-cli gets right (validated by the paper and peers)

| Co-cli design choice | Paper evidence | Peer evidence |
|---------------------|----------------|---------------|
| Personality as *character authoring* (soul seeds, narrative grounding) rather than rule injection | Emotion concepts are character-modeling machinery inherited from pretraining; personality prompts shape *which* character, not whether character simulation occurs | SillyTavern: "context-over-command" — emotional anchors over rules. Soul.md: worldview grounding over behavioral rules |
| Emotional mindsets prescribe *behavioral responses* to emotional situations, not emotional states | Emotion vectors influence behavior causally; "respond to anxiety with preparation" engages the other-speaker→present-speaker emotional regulation channel | ElizaOS: `style` object separates behavioral conventions per context |
| Never-lists as hard constraints against specific emotional behaviors | The sycophancy-harshness tradeoff requires specific behavioral prohibitions, not generic rules | Soul.md: STYLE.md anti-patterns ("do not soften corrections") — same principle |
| Planted character memories as permanent narrative grounding | Emotion vectors at the Assistant colon token predict response emotion (r=0.87); early emotional context has outsized influence | SillyTavern: "first message is strongest style signal." Soul.md: SOUL.md loaded fresh each session as stable identity anchor |
| Different personalities have genuinely different emotional approaches to identical situations | The emotional profile of the response is a causal determinant of behavior quality — not cosmetic | ElizaOS: `adjectives` + `messageExamples` create distinct behavioral signatures |
| Explicit role/soul separation (soul seeds = soul, mindsets = role-in-context) | Soul-level grounding sets the emotion vector baseline; role-level guidance shapes output within that space | Soul.md: only other system with explicit SKILL.md/SOUL.md separation |

### 7b. Best practices from peers

| Practice | Source | Connection |
|----------|--------|------------|
| Context-over-command: fewer rules, clearer emotional anchors | SillyTavern (practitioner consensus, not repo-stated — see §1 provenance caveat), Soul.md | Co-cli's static block (soul seed + 6 mindsets + 7 rules + critique ≈ 3,760 words / ~5k tokens for `tars`) may be over-specified. The soul seed, never-list, and planted memories may be doing the real work; the always-on mindsets and episodic procedural rules may add weight without proportional behavioral influence. See Part II §11.0 (and the P0 exec-plan) for a measured breakdown and subtractive plan |

### 7c. Design gaps no peer addresses

1. **Emotion-aware safety monitoring**: no peer monitors emotion vector activations as a safety signal. Co-cli's `detect_safety_issues()` checks behavioral patterns but not the emotional dynamics that precede misalignment.

2. **Calm as cross-personality safety invariant**: the paper's clearest actionable finding is that calm suppresses misalignment. Co-cli currently treats calm as a personality preference (implicit in Finch; suppressed by TARS). A universal rule like "when encountering repeated failures, explicitly pause and reconsider before continuing" would activate calm cross-personality.

3. **Personality-aware compaction**: no peer considers personality consistency when truncating conversation history. Co-cli's compaction (`summarize_history_window`) uses token count thresholds and head+summary+tail structure without preserving personality-demonstrating exchanges.

4. **Sycophancy-harshness calibration per personality**: the paper shows this is a fundamental tradeoff in emotion space. Co-cli's never-lists partially address it, but there is no explicit design decision placing each personality on the spectrum. Jeff's warmth bias is emergent, not designed.

5. **User-emotion modeling guidance**: the paper reveals the other-speaker→present-speaker emotion channel. Co-cli's mindsets prescribe responses to emotional situations but don't coach how to *model* user emotions explicitly. "When you detect the user is frustrated, name it briefly before proceeding" would engage this channel directly.

6. **Personality assembly weight**: SillyTavern's context-over-command consensus (practitioner-level, not repo-stated — §1 caveat) and ElizaOS's flat schema suggest co-cli's assembly depth may be over-specified. If the soul seed, never-list, and planted memories are the load-bearing elements, the 6 always-on mindset files may add weight without proportional behavioral influence. If co-cli keeps the weight, it should formalize priority and conflict resolution rather than relying on implicit ordering. (Part II develops both halves: §11.0 the subtractive thinning → P0 exec-plan, §11.5 the precedence formalization.)

---

## 8. Comparative Summary

```text
                    Soul depth (character identity & grounding)
                    ─────────────────────────────────────────►
                    None          Partial         Deep narrative

  FRONTIER SYSTEMS
  ElizaOS                         ■ (bio + adjectives,
                                     no narrative grounding)
  Soul.md                              ■ (SOUL.md + worldview,
                                           context-over-command)
  SillyTavern                                  ■ (character cards,
                                                  emotional anchors)
  co-cli                                            ■ (soul seeds,
                                                      planted memories,
                                                      never-lists,
                                                      narrative backstory)
```

```text
                    Emotional modeling
                    ─────────────────────────────────────────►
                    Suppressed    Implicit        Explicit

  FRONTIER SYSTEMS
  ElizaOS                         ■ (style per context)
  Soul.md                         ■ (STYLE.md anti-patterns)
  SillyTavern                                  ■ (emotional anchors,
                                                  first-message priming)
  co-cli                                            ■ (6 emotional
                                                      mindsets per profile,
                                                      planted memories,
                                                      trigger→response
                                                      patterns)
```

```text
                    Role / soul separation
                    ─────────────────────────────────────────►
                    Conflated     Implicit        Explicit

  FRONTIER SYSTEMS
  ElizaOS                         ■ (flat JSON, no hierarchy)
  SillyTavern                          ■ (soul in card,
                                           role in scenario —
                                           structurally implicit)
  Soul.md                                    ■ (SKILL.md = role,
                                               SOUL.md = soul,
                                               explicit reading order)
  co-cli                                            ■ (soul seeds = soul,
                                                      mindsets = role-in-context,
                                                      explicit file separation)
```

Co-cli sits at the deep end of all three axes. Among frontier systems, Soul.md matches co-cli on role/soul separation; SillyTavern matches on soul depth and emotional anchoring; no peer matches on all three simultaneously. Co-cli remains the only *agentic engineering tool* at this depth. The functional emotions paper provides mechanistic evidence that this position is causally meaningful — personality-scale prompt engineering operates on real representational channels, not surface text patterns.

---

## A. Memory-structure peers — typed vs flat preference state (Letta, Mem0)

Added in the 2026-06-13 refresh, scoped to one question the self-model / working-style work raises
(Part II §11.3 / deferred P2): does the **typed, scoped, labeled
preference-state** direction have a peer precedent, or is it unsourced? Letta and Mem0 are
memory-architecture systems, not personality systems — surveyed here only on this axis, not on
soul/role/emotion.

| System | Memory unit | Typed? | Scope / labels | Verdict for the typed-preference direction |
|--------|-------------|--------|----------------|--------------------------------------------|
| **Letta** | `Block` (`letta/schemas/block.py`) | **Yes** | `label` (e.g. `human`, `persona`), `limit` (char budget), `description`, `read_only`, `hidden`, `metadata`; ships default `Human`/`Persona` blocks | **Backs it** — named, scope-bounded, attribute-typed memory slots |
| **Mem0** | `MemoryItem` (`mem0/configs/base.py`) | No | `id`, `memory` (text), `hash`, generic `metadata` dict, `score`, timestamps; categories live in optional metadata, not the core schema | **Does not back it** — flat text + optional metadata |

So the typed-preference direction is **half-backed**: Letta is a concrete, shipping instance of
typed/labeled/scope-bounded memory blocks; Mem0 is the counter-example, pushing all structure into
optional metadata. Cite "Letta–Mem0 typed blocks" as Letta-backed, not pair-backed.

---

## 9. References

- Sofroniew, N., Kauvar, I., Saunders, W., Chen, R., et al. "Emotion Concepts and their Function in a Large Language Model." Transformer Circuits, April 2, 2026. https://transformer-circuits.pub/2026/emotions/index.html
- Soul.md: `~/workspace_genai/soul.md` — Markdown template system (`SOUL.md`/`STYLE.md`/`SKILL.md` + `examples/`) for AI identity, worldview, and voice specification
- ElizaOS: `~/workspace_genai/elizaos` (https://github.com/elizaOS/eliza) — `Character` type at `packages/core/src/types/agent.ts` (`bio`/`adjectives`/`style`/`messageExamples`)
- SillyTavern: `~/workspace_genai/sillytavern` (https://github.com/SillyTavern/SillyTavern) — Character Card V3; `charDepthPrompt` depth injection in `public/script.js`. "Context-over-command" is practitioner consensus, not a repo-stated philosophy (§1 caveat)
- Letta: `~/workspace_genai/letta` (https://github.com/letta-ai/letta) — `Block` memory schema at `letta/schemas/block.py`
- Mem0: `~/workspace_genai/mem0` (https://github.com/mem0ai/mem0) — `MemoryItem` at `mem0/configs/base.py`
- co-cli: `co_cli/context/assembly.py` (`build_base_instructions`), `co_cli/personality/prompts/souls/`, `co_cli/personality/prompts/loader.py`

---

# Part II — co Self-Model & Working-Style Diagnosis

Handoff-quality design review for a TL and implementation team — gap framing and the as-is picture.

The question is not "does co have a personality?" It clearly does. The question is whether co has a
high-quality **self model**: an explicit, durable, inspectable definition of how it should behave
across tasks, contexts, and time.

Thesis:

- personality is a **working-style layer**, subordinate to trust, truthfulness, approvals, and task completion
- working style should be explicit enough to tune and inspect
- one `co` deployment is **one character instance**; future multi-character teamwork composes
  multiple instances, never collapses several characters into one prompt

> **Prescription has moved.** The target architecture, phased goals, and risks that answered the gaps
> below (former §4–§7 of the retired `RESEARCH-personality-self-working-style.md`) are now exec-plans,
> not reference material:
> - **P0 — thin & measure the static block** (former §4.0 Steps 1/3/4/5) → `docs/exec-plans/active/2026-06-13-012653-personality-self-model.md`
> - **Mindset selection / router** (former §2.2 / §4.2) → `docs/exec-plans/active/2026-05-27-165621-mindset-stance-selection.md`
> - **P1** (style schema + resolver + `/style`; former §4.1/§4.2/§4.6) and **P2** (typed preference
>   memory; former §4.3) → deferred follow-ons documented in the P0 plan's `## Deferred`.
>
> Forward-references below use shorthand: **(P0 plan)**, **(mindset plan)**, **(deferred P1)**,
> **(deferred P2)**.

## 10. Current co Shape

The base abstraction is already right: one instance, one selected role, one working relationship.
A future team setting is separate `finch` / `jeff` / `tars` instances, each with its own self
model and memory — teamwork between instances, not inside one blended prompt.

### 10.1 Architecture

**Roles** — `finch`, `jeff`, `tars`, auto-discovered from `souls/`. Selected via
`Settings.personality` (`co_cli/config/core.py`, default `"tars"`, env `CO_PERSONALITY`).

**Soul assets** — `co_cli/personality/prompts/souls/{role}/`:
- `seed.md` — identity anchor (required)
- `mindsets/*.md` — six task-shape files (`technical`, `exploration`, `debugging`, `teaching`,
  `emotional`, `memory`); validated non-blocking in `prompts/validator.py`
- `critique.md` — review lens (optional); injected by the orchestrator
- `curation.md` — retention/merge disposition (optional); loaded by the dream daemon, not the orchestrator
- `canon/*.md` — character base: decay-protected scenes, speech patterns, behavioral observations

**Loaders** — `co_cli/personality/prompts/loader.py`: `load_soul_seed`, `load_soul_mindsets`,
`load_soul_critique`, `load_soul_curation`. Seed and mindsets load at agent construction;
critique and curation load lazily in their respective consumers.

**Static assembly** — `co_cli/context/assembly.py` → `build_base_instructions(config)`, one of
three static-instruction builders the orchestrator joins into the cached prefix (alongside
`_toolset_guidance_provider` and `_personality_critique_provider`). It assembles, in strict order:
1. Soul seed
2. Mindsets (all six joined into one block)
3. Behavioral rules — numbered `01_identity` … `07_memory_protocol` (identity, safety, reasoning,
   tool_protocol, workflow, skill_protocol, memory_protocol)

Canon, critique, and curation are **not** in this assembly.

**Critique injection** — `co_cli/agent/orchestrator.py`: `_personality_critique_provider` loads
`critique.md` and appends it as a `## Review lens` section after operational guidance. Skipped if
absent.

**Curation injection** — `co_cli/daemons/dream/_reviewer.py`: `_with_curation_lens()` appends
`curation.md` to the memory and skill review prompts, scoping the active character's retention
judgment (what counts as durable signal, how aggressively to merge) into curation — deliberately
free of voice. Skipped if personality is disabled or the role ships no `curation.md`.

**Per-turn instructions** — only structural safety guardrails and ephemeral date/time grounding.
**No per-turn style injection** exists; all static personality content is in the cached block.

**Canon** — indexed at bootstrap under `source='canon'` into the shared FTS5 index (BM25),
alongside user memory. Recalled for personality auto-injection only; **never returned by any
model-callable tool**.

**User preferences** — `co_cli/memory/item.py`: `MemoryItem` with `MemoryKindEnum`
(`USER`, `RULE`, `ARTICLE`, `NOTE`, `CANON`). Flat, domain-agnostic schema. User communication and
feedback style live as `USER`-kind items. No typed subtypes, scope, strength, or supersedes fields.

### 10.2 Strengths

- **Clear static identity anchor** — seed + mindsets + rules assembled with a defined order contract
- **Layered asset separation** — identity, policy, task guidance, character memory, and retention
  disposition in distinct files with distinct load paths
- **Maintainer legibility** — behavior is plain markdown, not Python branches

This beats systems that stuff "be helpful, concise, warm" into one prompt blob. But it remains a
**prompt-composition system**, not yet a self-model system: the behavior contract is distributed
across prose rather than represented as explicit dimensions with stable semantics. None of the
prescription proposals are built yet (they now live in the exec-plans; see the Part II preamble) —
there is no `style.yaml`, `ResolvedStyle`, `resolve_working_style()`, typed preference schema, or
`/style` command.

The one piece that has materialized in another form is the voice/behavior split (former §4.4):
`curation.md` is an explicit behavioral disposition kept deliberately free of voice, scoped to the
dream daemon's curation domain. It is a precedent the broader split can follow, not the resolver.

### 10.3 Peer / frontier grounding

Cross-checked against Part I of this document (ElizaOS, SillyTavern, Soul.md, plus Letta/Mem0 as
memory-structure peers in §A; and the Anthropic functional-emotions paper). Verdicts use the survey's
2026-06-13 HEAD refresh. Honest verdict:

- **Directly peer-backed:** §11.5 (precedence / conflict resolution). Part I §7c.6 says co
  "should formalize priority and conflict resolution rather than relying on implicit ordering."
- **Partly peer-backed:** §11.3 / typed preferences (deferred P2). Per §A, Letta's `Block` schema
  (`label` / `value` / `limit` / `description` / `read_only` / `hidden` / `metadata`, with default
  `Human`/`Persona` blocks) is a concrete peer instance of typed, labeled, scope-bounded memory.
  Mem0 is flat text + optional metadata and does **not** back the direction. So Letta supports it;
  the pair-as-a-whole does not.
- **Directional only:** §11.2. SillyTavern's Character's Note (`charDepthPrompt` injected at a
  configurable conversation depth), ElizaOS per-medium `style` arrays, and Soul.md's named modes
  (transitions are implicit/contextual, not formally modeled — confirmed at HEAD) are lighter dynamic
  mechanisms — but Part I rates co's mindset-per-task as the strongest role layer of all systems
  surveyed. co is ahead, not behind.
- **Frontier aspiration, not peer-demonstrated:** §11.1, §11.4, §11.6, §11.7. No surveyed peer has
  tunable behavior dimensions, a behavioral self-governance loop, multi-instance identity isolation,
  or a resolved-self-model inspection surface.

**Tension to hold honestly:** Part I's central design lesson is *context-over-command* ("fewer
rules, clearer anchors"), paired with its §7c.6 synthesis that co's assembly may already be
**over-specified**. Provenance matters here: the HEAD refresh confirms context-over-command
is a **practitioner consensus** the survey endorses (and the functional-emotions paper directionally
supports), *not* a philosophy stated in any peer's repo — SillyTavern ships the mechanism, not the
doctrine (Part I §1 caveat). So the tension is real but softer than "evidence": the central additive
proposal — a `style.yaml` schema + resolver (deferred P1) — adds structure against a *consensus* lean
toward less. Treat structuring as a hypothesis to validate with evals, not a settled win — and note
the cheaper, better-grounded move is subtractive (§11.0 → P0 plan), which the same consensus supports
rather than fights.

## 11. Gap Analysis

Gaps §11.1–§11.7 are additive — each names something co lacks and should build. §11.0 is the
exception and the precondition: it is subtractive, and it is the gap Part I's own synthesis flags
most directly (§7c.6 over-specification; context-over-command consensus, §10.3). Read the rest
through it.

### 11.0 The static block is over-specified

The cached static prompt for one role carries, every turn, regardless of task (word counts, `tars`):

| Section | Words | Notes |
|---|---|---|
| seed | 357 | identity anchor — invariant |
| six mindsets | 768 | joined, all always-on |
| seven rules | 2,608 | `01_identity` 84, `02_safety` 142, `03_reasoning` 491, `04_tool_protocol` 511, `05_workflow` 371, `06_skill_protocol` 454, `07_memory_protocol` 555 |
| critique | 28 | `## Review lens` |

≈ **3,760 words / ~5k tokens**, assembled once and carried on every turn. Two structural wastes:

- **Off-task mindsets.** Five of six mindsets (~640 words) never apply on a given turn but are
  always present (§11.2).
- **Always-on procedure.** The bulk is rules (2,608 words), dominated by *episodic* procedural
  protocols — tool (511), skill (454), memory (555), reasoning (491) — not invariants. The memory
  protocol is dead weight on a turn with no memory operation; the skill protocol is dead weight when
  no skill is in play.

Consequence: the prompt is **heavy and flat**. Heavy works against Part I's central design lesson
(context-over-command — a practitioner consensus the survey endorses, not repo-proven; §10.3); flat
means the model gets no signal about which of ~3,700 words matter this turn.
And it compounds the doc's central tension — bolting a style schema (deferred P1) onto an
already-over-weight block makes the wrong direction worse unless the block is thinned first.

Caveat: "shorter prompt" is not the goal — *removing content that doesn't change behavior* is. Some
of those 2,600 rule-words are load-bearing. The gap is that nothing today distinguishes load-bearing
from inertial, and there is no measurement to tell them apart. **(P0 plan)** addresses exactly this
(instrument first, then capability-gate the skill/memory protocols).

### 11.1 Self model is mostly implicit

co has a soul and rules but no explicit schema for its behavior dimensions. Dimensions present
only in prose today: directness vs expansiveness, challenge vs deference, warmth vs neutrality,
action bias vs deliberation, confidence vs uncertainty surfacing, initiative vs waiting.

Consequence: tuning is indirect (rewrite prose, not a value); diffs on `seed.md` don't reveal
which dimension moved; regressions look like "prompt drift" rather than a visible contract change.

Caveat: no surveyed peer has tunable dimensions either, and Part I warns against over-specification.
This is a frontier aspiration **(deferred P1)**, not a peer-demonstrated win.

### 11.2 Context adaptation is file-driven, not state-driven

All six mindsets are loaded together at agent construction and joined into one static `## Mindsets`
block (`load_soul_mindsets`). Each is a clearly labeled subsection (`## Debugging`, `## Technical`,
…), so the model self-selects which applies — there is no system-level gating, no runtime concept
like `task_mode=debugging`, `risk_level=elevated`, or `response_contract=brief_direct`.

The selection is not even nudged at the prompt level. `load_soul_mindsets` emits `## Mindsets`
followed by the six joined files and nothing else — no preamble, no classify-then-focus rule
(`03_reasoning.md` covers verification and when-to-ask, never task-shape selection), no seed or
critique reference to the mindsets. The only structuring signal is the heading labels. So co relies
on emergent attention to labeled sections: the model *may* weight the relevant heading, but nothing
prompts it to, and on a non-reasoning model no explicit selection step need happen at all.

Consequence: mindset selection is the model's inference, not a system decision. No mechanism exists
to deliberately activate the right stance when stakes rise, suppress the other five, or verify the
intended mindset was applied. **This gap is owned by the (mindset plan)** — an ablation-first eval
deciding whether the block earns its real estate before any selection nudge or router is built.

(SillyTavern/Soul.md/ElizaOS have lighter dynamic mechanisms, but co's static load is rated the most
sophisticated role layer — so this is direction-of-travel, not lag.)

### 11.3 Preference state is a weak control surface

User style preferences are flat `USER`-kind `MemoryItem`s — markdown with a description, no
structured fields. They work for "prefers blunt feedback" / "dislikes long preambles" but not for:
this applies only in coding tasks; this is soft not mandatory; this correction supersedes older
ones; this must never override safety.

Consequence: preferences can be remembered without being applied with nuance; prompt-level style
and memory-level preferences can conflict with no arbitration rule. Letta's typed memory blocks
(§10.3 / §A) are a concrete peer precedent for the structured direction **(deferred P2)**; Mem0 is not.

### 11.4 Critique exists; self-governance is partial

Two governance-adjacent layers exist today, both scoped narrowly:

- `critique.md` — an always-on `## Review lens`, prose not a loop.
- `curation.md` — a character-scoped retention/merge disposition feeding the dream daemon's memory
  and skill reviewers. This is a real self-governance layer, but bounded to *what to keep*, not to
  *how the agent behaves*.

Still missing for the behavioral domain: explicit style failure categories, lightweight self-checks
tied to task/risk level, persistent signals about where behavior repeatedly misses. co can be asked
to critique itself but accumulates no reliable model of its behavioral weaknesses. The curation lens
shows the pattern works; the gap is extending it from retention judgment to behavioral judgment
**(deferred P1)**.

### 11.5 No clean separation between identity, policy, and learned style

Identity/voice (seed), safety/approval policy (rules 02, 04), workflow rules (rule 05), and learned
user-facing style (USER-kind items, recalled dynamically with no formal injection contract) all
collapse into "this becomes prompt text" with no explicit precedence.

Consequence: maintainers can use identity text to paper over policy gaps; style customization can
drift into operational rules; hard to reason about what may change dynamically. (This is the one gap
with direct peer-survey support — Part I §7c.6 calls for formalized priority and conflict resolution.)

### 11.6 Instance vs multi-agent identity not separated strongly

The design is compatible with one-instance-one-character but doesn't state the boundary firmly.
Future `finch`/`jeff`/`tars` teamwork must not become one prompt with several personalities, one
memory store with blended preferences, or one resolver emulating several operators. If the boundary
stays implicit, team features risk identity leakage.

### 11.7 No inspection surface for the self model

Maintainers can inspect the ingredients (files) but not the resolved self model: what co currently
believes its working style is, which modifiers are active this session, which preferences are in
force, which constraints outrank others. Users can't easily understand why co answered as it did.
A read-only `/style` surface would close this **(deferred P1)**.

## 12. What Good Looks Like

Peer backing per quality (✓ convergent practice / ~ directional only / ✗ frontier, no peer instance):

- **explicit** — core behavior dimensions represented directly, not only in prose. *✗ frontier* — no
  surveyed peer has tunable dimensions.
- **layered** — identity, policy, task-mode adaptation, learned user-style as separate layers with
  defined precedence. *✓* — Soul.md's `SOUL`/`STYLE`/`SKILL` split (Part I §3) and §7c.6's
  precedence call back the layering + precedence.
- **bounded** — no style layer overrides safety, approval, truthfulness, or uncertainty discipline.
  *~ adjacent* — no surveyed peer frames anti-sycophancy as a typed bound (it is output-correctness
  or posture, not a constraint the style layer is subordinate to).
- **situational** — adapts by task, risk, and user state without becoming erratic. *~ directional* —
  ElizaOS per-medium `style`, Soul.md modes, SillyTavern depth injection adapt by context (lighter
  mechanisms than the deferred-P1 resolver).
- **inspectable** — maintainers (eventually users) can see the resolved working-style state.
  *✗ frontier* — no peer exposes a resolved self-model.
- **repairable** — bad behavior corrected by changing a small explicit layer, not rewriting the soul.
  *design principle* — no direct peer instance.
- **instance-scoped** — one instance, one self model; cross-character collaboration is a higher
  composition concern. *✗ frontier* — no peer does multi-instance identity isolation.
