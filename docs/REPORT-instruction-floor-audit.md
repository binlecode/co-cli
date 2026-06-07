# REPORT — Instruction-floor audit (instruction-half counterpart to the ALWAYS tool-schema audit)

> **Pairing artifact.** This is the instruction-half counterpart to
> `docs/REPORT-always-tool-schema-audit.md` (TASK A1). That audit gave the **tool-schema half** of the
> fixed prefill floor a first-principles tiering review and drove A2 (defer 4 tools, 20,581 → 17,224
> chars). This report gives the **instruction half** — the *larger, less-audited* half — the equivalent
> deep review, and surfaces a coupling defect that A2 left behind: the static instruction floor still
> hard-codes the call signatures of the four tools A2 deferred.
>
> Source measurements: live bootstrap (`create_deps`, native `FunctionToolset`, no MCP), personality
> `tars`, run date 2026-06-07. Per-file char counts from `wc -c` on `co_cli/context/rules/*.md` and the
> `souls/tars/` assets. Floor totals from a live `build_static_instructions` + `build_toolset_guidance` +
> `load_soul_critique` measurement.

---

## 1. Headline

- The **fixed prefill floor** has two uncompactable halves that ride every request: the **instruction
  half** (soul seed + mindsets + numbered rules + toolset guidance + critique) and the **tool-schema half**
  (ALWAYS-visibility tool schemas).
- **The instruction half is the larger half — and it received the least rigor.** Measured live:

  | Floor component | Chars | ~Tok | Share of floor | Treatment in TASK A |
  |---|---:|---:|---:|---|
  | `build_static_instructions` (seed + mindsets + rules) | 23,473 | ~5,868 | — | size-pin only (`test_instruction_budget.py`) |
  | toolset guidance (`MEMORY_GUIDANCE` + `CAPABILITIES_GUIDANCE`) | 985 | ~246 | — | **unguarded** |
  | personality critique (`## Review lens`) | 162 | ~41 | — | **unguarded** |
  | **Instruction half — total delivered floor** | **24,624** | **~6,156** | **59%** | partial pin, no content audit |
  | Tool-schema half (post-A2 ALWAYS bucket) | 17,224 | ~4,306 | 41% | full A1 first-principles audit |
  | **Fixed floor — combined** | **~41,848** | **~10,462** | 100% | — |

- The **rules block alone (17,134 chars)** is larger than the **entire post-A2 ALWAYS schema bucket
  (17,224 chars)**. The audit asymmetry is backwards relative to ROI: the bigger half got a size pin; the
  smaller half got a tiering audit.
- **Six findings** below: one coherence defect (F5 — the A2 pairing gap), four redundancy findings
  (F1–F4), and one guard-scope gap (F6). Four proposals (P1–P4) drive the remediation.

---

## 2. Instruction-half composition (measured)

### 2.1 Per-file char breakdown (tars, native toolset)

| Component | File | Chars | Notes |
|---|---|---:|---|
| Soul seed | `souls/tars/seed.md` | 2,101 | identity anchor; always first |
| Mindset: debugging | `souls/tars/mindsets/debugging.md` | 455 | |
| Mindset: emotional | `souls/tars/mindsets/emotional.md` | 1,254 | |
| Mindset: exploration | `souls/tars/mindsets/exploration.md` | 929 | |
| Mindset: memory | `souls/tars/mindsets/memory.md` | 539 | |
| Mindset: teaching | `souls/tars/mindsets/teaching.md` | 590 | |
| Mindset: technical | `souls/tars/mindsets/technical.md` | 647 | |
| **Mindsets subtotal** | | **4,414** | + `## Mindsets` header + joins |
| Rule 01 | `rules/01_identity.md` | 538 | Relationship / anti-sycophancy / thoroughness |
| Rule 02 | `rules/02_safety.md` | 1,622 | credentials / source control / approval / injected content / **memory constraints** |
| Rule 03 | `rules/03_reasoning.md` | 3,255 | verification / fact authority / source conflicts / two kinds of unknowns |
| Rule 04 | `rules/04_tool_protocol.md` | 3,185 | responsiveness / strategy / execute / **error recovery** / paths / **deferred tools** |
| Rule 05 | `rules/05_workflow.md` | 2,474 | intent classification / execution / completeness / over-planning |
| Rule 06 | `rules/06_skill_protocol.md` | 2,710 | discovery / use / **drift** / create / offer-to-save / background review |
| Rule 07 | `rules/07_memory_protocol.md` | 3,350 | **recall** / explicit saves / kind selection / curation / **anti-patterns** |
| **Rules subtotal** | | **17,134** | |
| Toolset guidance | `context/guidance.py` (`MEMORY_GUIDANCE`, `CAPABILITIES_GUIDANCE`) | 985 | static builder #2; rides floor; **not** in instruction-budget test |
| Critique | `souls/tars/critique.md` (`## Review lens`) | 162 | static builder #3; rides floor; **not** in instruction-budget test |

### 2.2 Assembly path (where each piece enters the floor)

Per `docs/specs/prompt-assembly.md` §2.1, three static builders compose the floor literal once at agent
construction:

1. `_static_instructions_provider` → `build_static_instructions(config)` = seed + mindsets + rules
   (`co_cli/context/assembly.py:83`). **This is the only piece `test_instruction_budget.py` measures.**
2. `_toolset_guidance_provider` → `build_toolset_guidance(tool_index)` (`co_cli/context/guidance.py:32`) —
   `MEMORY_GUIDANCE` (gated on `memory_search`/`session_search` present) + `CAPABILITIES_GUIDANCE` (gated on
   `capabilities_check`). **Rides the floor; unguarded.**
3. `_personality_critique_provider` → `load_soul_critique(config.personality)` prefixed `## Review lens`.
   **Rides the floor; unguarded.**

All three join with `\n\n` into the single `instructions=` literal — `InstructionPart(dynamic=False)`, the
cached prefix. The skill manifest, deferred-tool stubs, safety, and current-time layers are *per-turn
dynamic* (§2.2) and correctly excluded from this floor.

---

## 3. Findings

### F1 — Verbatim duplicate on the floor (memory value statement)

The sentence

> *"Prioritize what reduces future user steering — the most valuable memory is one that prevents the user
> from having to correct or remind you again"*

appears **verbatim in two rule files**:

- `co_cli/context/rules/02_safety.md:27` (under "Memory constraints")
- `co_cli/context/rules/07_memory_protocol.md:5` (intro)

Both ride every request. Pure floor tax (~150 chars duplicated), zero added behavioral signal.

### F2 — Memory save anti-patterns stated twice

The "never save ephemeral session state" constraint is owned by two files:

- `02_safety.md:32-34`: *"Do not save ephemeral session state: task progress for the current session,
  completed-work logs, active TODO items, or temporary debugging notes."*
- `07_memory_protocol.md:69-70`: *"Task progress, completed-work logs, session outcomes, or temporary TODO
  state — these are ephemeral; recall them later via `session_search`."*

Same constraint, two owners. `07` is the dedicated memory-protocol file; `02_safety` is the redundant copy.

### F3 — Recall guidance triplicated

"Search memory/sessions before answering when the user references past work" is stated in **three** floor
locations:

- `07_memory_protocol.md:9-18` ("Recall" section)
- `MEMORY_GUIDANCE` in `co_cli/context/guidance.py:12-21` (gated guidance block)
- `02_safety.md:26-31` ("Memory constraints" — the proactive-save half)

Three sources, one behavioral rule. `MEMORY_GUIDANCE` and `07`'s "Recall" are near-paraphrases of each
other (both enumerate "user references past work / preference / prior decision → search first").

### F4 — "Retrying is a loop" stated three times

The anti-loop principle is restated in near-identical words across three places:

- `04_tool_protocol.md:48`: *"Retrying unchanged is a loop, not recovery."*
- `04_tool_protocol.md:53`: *"a second unchanged retry is a loop."*
- `05_workflow.md:30-31`: *"Retrying the same failed action is not persistence, it is a loop."*

(`04` states it twice within the same file, in adjacent paragraphs.)

### F5 — Deferred-tool signature leakage (the A2 pairing defect) — **highest value**

**This is the coupling that motivates the whole audit.** A2 deferred `session_search`, `session_view`,
`skill_patch`, `skill_edit` — moving their schemas off the ALWAYS floor (−3,357 chars) and behind a
`tool_view` round-trip. But the **static instruction floor still hard-codes the exact call signatures of
all four** on every turn:

| Deferred tool (A2) | Signature still on the floor | Location |
|---|---|---|
| `skill_patch` | `` skill_patch(name=<skill>, old_string=..., new_string=...) `` | `06_skill_protocol.md:36` |
| `skill_edit` | `` skill_edit(name=<skill>, content=...) `` | `06_skill_protocol.md:37` |
| `session_view` | `` session_view(session_id, start, end) `` | `07_memory_protocol.md:18` |
| `session_search` | `` session_search `` (named as a direct call) | `07_memory_protocol.md:14`, `:70` |
| `session_search` | *"Call `memory_search` or `session_search` before answering"* | `MEMORY_GUIDANCE`, `guidance.py:14` |

Two distinct harms:

1. **Savings clawback.** A2 removed these signatures from the schema floor, but the instruction floor
   re-encodes them as prose. Part of the 3,357-char "saving" is paid back on the instruction side, every
   turn.
2. **Internal inconsistency (correctness bug).** `04_tool_protocol.md:60-66` ("Deferred tools") instructs
   the model that deferred tools are **not loaded** and must be `tool_view`-loaded by exact name *before*
   calling. Yet `MEMORY_GUIDANCE` says *"Call `session_search` before answering"* and `06`/`07` give literal
   signatures — instructing the model to directly invoke tools that, post-A2, are not callable until loaded.
   The floor contradicts itself.

The fix is the direct port of A1's logic (§5): **the floor carries WHEN/WHY (behavioral trigger); the
loaded schema carries HOW (signature).** The behavioral trigger ("recall before answering", "fix a drifting
skill immediately") is legitimately floor-worthy; the *signature* belongs in the schema that loads on
demand.

### F6 — Budget guard does not cover the full floor

`tests/test_instruction_budget.py` measures only `build_static_instructions(deps.config)` (seed + mindsets
+ rules = 23,473 chars, pinned at ceiling 23,750). It does **not** measure the toolset guidance (985) or the
critique (162) — **~1,147 chars of floor ride unguarded.** The context-stability plan's claim that the
"instruction half [is] guarded by `tests/test_instruction_budget.py`" is therefore **partial**: a re-bloated
`MEMORY_GUIDANCE` or a longer critique grows the real floor without tripping any CI gate.

---

## 4. Cross-review against `core-dev-checklist.md`

Three checklist principles, applied to the *finished* A1/A2 work, each independently surface the same gap:

| Checklist principle | Where it applies to A2 | Verdict |
|---|---|---|
| **"Integration boundary, not module boundary"** (L21-24) | A2's `done_when` verified the schema-bucket char count (module boundary). It never verified the floor **as delivered each turn** (integration boundary). | **Gap** — F5: deferred-tool signatures still in the delivered floor. |
| **"Guard condition parity / stale assertions"** (L11-12) | Deferring a tool should trigger a paired sweep of every floor reference to it — the analog of "scan for stale assertions the change affects." | **Gap** — A2 had no paired instruction-floor sweep. |
| **"Test coverage gaps"** (L10) | The instruction half — the larger half — has only a size pin, no redundancy/coupling gate, and the pin is partial. | **Gap** — F6 + no coupling guard. |

**Conclusion:** A2 was correct and well-grounded *within its declared scope (the schema half)*, but the
floor is a single integrated surface. Optimizing one half in isolation left the floor internally
inconsistent (F5) and the redundancy in the larger half untouched (F1–F4). The checklist's own
"integration boundary" discipline is what flags this.

---

## 5. Organizing principle (ported from A1)

A1's tiering turned on: **criticality vetoes deferral at any size; size pays off only when big; the
*signature* lives in the schema, the *awareness* lives in the stub.** The instruction-half analog:

> **The floor carries WHEN/WHY; the loaded schema carries HOW.**
> A behavioral trigger ("recall before answering", "fix a drifting skill", "don't retry unchanged") shapes
> behavior and must be present uncompacted — it earns its floor seat. A tool's *call signature* is HOW, not
> WHEN — it belongs in the schema, which for ALWAYS tools is already on the floor and for DEFERRED tools
> loads on demand. Duplicating the signature in instruction prose is redundant for ALWAYS tools and
> incoherent for DEFERRED ones.

**Honest mechanism caveat:** the deferral architecture does **not** port to rules. There is no per-rule
loader, and behavioral rules must be present to shape behavior — they cannot be "deferred behind a stub"
the way tools are. So the instruction-half lever is **dedup + signature-decoupling + targeted verbosity
trim**, *not* a tiering/defer mechanism. Proposing a per-rule deferral system would be over-engineering and
is explicitly out of scope.

---

## 6. Proposals (drive the new plan)

### P1 — Dedup to single-owner (low risk, immediate float)

Resolve F1–F4 by giving each concept exactly one floor owner:

- **Memory save-policy** (F1, F2): owned by `07_memory_protocol.md`. Strip the duplicate value statement and
  the duplicate anti-patterns from `02_safety.md` — leave at most a one-line pointer, or nothing (the
  memory-protocol file is unconditional floor, so a pointer is unnecessary).
- **Recall** (F3): collapse to one owner. `MEMORY_GUIDANCE` (gated) and `07` "Recall" (unconditional) say
  the same thing; pick one. Recommend keeping the behavioral trigger in `07` and trimming `MEMORY_GUIDANCE`
  to the part that is genuinely tool-presence-conditional (or removing it if `07` fully covers it).
- **Anti-loop** (F4): owned by `04_tool_protocol.md` "Error recovery." Collapse `04`'s two adjacent
  restatements into one; `05_workflow.md` keeps a single-clause reference, not a full restatement.

Estimated reclaim: ~600–900 chars, zero behavioral change.

### P2 — Decouple deferred-tool signatures from triggers (the F5 fix — correctness)

Strip the exact call signatures of **deferred** tools from the floor; keep the behavioral trigger:

- `06_skill_protocol.md` "Drift": keep *"if a skill drifted, fix it immediately — don't wait to be asked"*;
  drop the `skill_patch(name=…, old_string=…, new_string=…)` / `skill_edit(name=…, content=…)` literals.
- `07_memory_protocol.md` "Recall": keep *"search past sessions before answering"*; drop the
  `session_view(session_id, start, end)` literal signature (loads with the schema on `tool_view`).
- `MEMORY_GUIDANCE`: stop instructing a *direct* `session_search` call as if loaded; align with the
  "Deferred tools" mechanic in `04_tool_protocol.md`.

This removes the internal inconsistency **and** reclaims chars. **Highest-value proposal — it is a coherence
bug fix, not a size optimization.** Note: `memory_search`, `memory_view`, `skill_view`, `skill_create` etc.
remain ALWAYS or are referenced correctly; only the four A2-deferred tools' signatures are in scope.

### P3 — Add a coupling guard (operationalizes "guard condition parity")

A test asserting **no DEFERRED tool's name appears with a call-signature pattern** in the rules/guidance
floor — the instruction-half counterpart to `test_orchestrator_schema_budget.py`. This makes the
A2↔instruction pairing **permanent**: a future defer that forgets the paired floor sweep fails CI, and a
future floor edit that re-introduces a deferred-tool signature fails CI. This is the durable fix for the
checklist "guard condition parity" gap.

### P4 — Extend the budget guard to the full delivered floor (the F6 fix)

Extend `test_instruction_budget.py` to measure the **full delivered floor** —
`build_static_instructions + build_toolset_guidance + load_soul_critique` — or add a second pin for the
guidance+critique slice. Closes the ~1,147-char unguarded gap so guidance/critique creep can't grow the
floor silently.

### Size expectation (honest)

Realistic reclaim from P1+P2 is **~250–375 tok** (~1,000–1,500 chars) — same order as A2's ~840-tok cut, on
the *larger* half, with more headroom remaining for future verbosity trims. But the headline value is **P2's
coherence fix**, not the size. P3/P4 add no float; they prevent regression.

---

## 7. Scope & boundaries

- **New lever, new plan.** This work is not in `context-stability-sizing-control` — that plan's "Out of
  scope" parks further floor work and only TASK B remains. This is a peer plan (recommended:
  `/orchestrate-plan instruction-floor-audit`).
- **No rule files edited in producing this report** — spec/surgical discipline. All findings are
  evidence-cited reads.
- **Out of scope for the new plan:** any per-rule deferral mechanism (§5 caveat — does not port); enlarging
  the operational window; touching the schema half (A1/A2 already closed it); rewriting personality voice
  in seed/mindsets (separate concern from floor-redundancy).
- **Behavioral constraint carried forward:** every trim must preserve the behavioral signal. Dedup removes
  *duplicate* statements, not the *only* statement of a rule. P2 removes *signatures*, not *triggers*. A
  trim that drops a behavior is a regression, not a win — validate against the conservative small-model bias
  the floor exists to serve.

---

## 8. Next levers (documented, not this plan)

- **Verbosity trim of the rules block** beyond dedup — `03_reasoning` (3,255) and `04_tool_protocol` (3,185)
  are the two largest rule files and likely carry tightening headroom. Deferred until P1/P2 land, mirroring
  how A1 parked docstring tightening behind deferral.
- **Per-agent instruction surfaces** (opencode's lever, noted in the A1 peer survey) — a lean default floor
  per agent role. An axis co does not use today; out of scope here.
