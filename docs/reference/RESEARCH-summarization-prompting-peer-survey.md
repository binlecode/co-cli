# RESEARCH: Summarization Prompting Design — Peer Survey

How hermes-agent, openclaw, opencode, and codex structure the prompt sent
to the LLM during context compaction. Records facts; does not prescribe
co-cli direction.

Peers at HEAD (re-verified 2026-06-03 against freshly-pulled local repos):
`hermes-agent f66a929a6`, `openclaw 323c9760d3` (on PI
`@earendil-works/pi-coding-agent` v0.75.1 — PI owns serialization + the
API call; openclaw owns prompt content), `opencode 55bafa29d`,
`codex 6bcccb0ee6`.

## Sources

| System | Path |
|--------|------|
| hermes | `agent/context_compressor.py` |
| openclaw | `src/agents/compaction.ts`, `src/agents/pi-hooks/compaction-safeguard*.ts` |
| opencode | `packages/opencode/src/session/compaction.ts`, `agent/prompt/compaction.txt` |
| codex | `codex-rs/prompts/templates/compact/*.md`, `codex-rs/core/src/compact*.rs` |

---

## 1. hermes-agent

### Prompt structure

```
┌─ _summarizer_preamble  (context_compressor.py:1253–1265)
│  "You are a summarization agent creating a context checkpoint ...
│   write in the same language ... NEVER include API keys, tokens,
│   passwords ... replace with [REDACTED]"
│
├─ Branch wrapper:
│  │
│  ├─ IF self._previous_summary:  (cc.py:1343–1357)
│  │  ┌─ "You are updating a context compaction summary."
│  │  ├─ "PREVIOUS SUMMARY:"
│  │  ├─ {self._previous_summary}             ← plain label, no fence
│  │  ├─ "NEW TURNS TO INCORPORATE:"
│  │  ├─ {content_to_summarize}               ← serialized turns
│  │  └─ "PRESERVE / ADD / MOVE / REMOVE ... CRITICAL: Update Active Task"
│  │
│  └─ ELSE:  (cc.py:1358–1369)
│     ┌─ "Create a structured checkpoint summary ..."
│     ├─ "TURNS TO SUMMARIZE:"
│     ├─ {content_to_summarize}
│     └─ "Use this exact structure:"
│
├─ _template_sections  (cc.py:1268–1336)  ← shared by both branches
│  ## Active Task             ← most-recent unfulfilled input, MOST IMPORTANT
│  ## Goal
│  ## Constraints & Preferences
│  ## Completed Actions       ← "N. ACTION target — outcome [tool: name]"
│  ## Active State            ← cwd, branch, files, tests
│  ## In Progress
│  ## Blocked                 ← exact error messages
│  ## Key Decisions           ← with WHY
│  ## Resolved Questions
│  ## Pending User Asks
│  ## Relevant Files
│  ## Remaining Work          ← framed as context, not instructions
│  ## Critical Context
│  └─ "Target ~{summary_budget} tokens. Be CONCRETE ..."  (cc.py:1339)
│
└─ FOCUS TOPIC  (optional, appended last for precedence)  (cc.py:1371–1377)
   "PRIORITISE preserving ... 60-70% of summary token budget ...
    even for the focus topic, NEVER preserve credentials"
```

### Assembly logic

```
_generate_summary(turns, focus?)
        │
        ▼
turns_to_summarize  ──┐
        │             │
        ├─→ _serialize_for_summary()  (cc.py:946–999)
        │       │
        │       ▼
        │   role-labeled text:
        │     [USER]: ...
        │     [ASSISTANT]: ...
        │       [Tool calls:  read_file({"path":"..."}) ]
        │     [TOOL RESULT id]: <head>...[truncated]...<tail>
        │       │
        │       ├─ _CONTENT_MAX=6000 (head 4000 / tail 1500)
        │       ├─ _TOOL_ARGS_MAX=1500 (head 1200)
        │       └─ redact_sensitive_text() on content + args (cc.py:960, 981)
        │
        └─→ _compute_summary_budget()  (cc.py:926–935)
            └─ 20% of content tokens, floor 2000, ceil 12000
        │
        ▼
preamble + branch_wrapper + template + (focus?)
        │
        ▼
LLM call (aux summary_model; fallback to main on error)
        │
        ▼
redact_sensitive_text(output)  (cc.py:1402)  ← second redaction pass
        │
        ▼
_with_summary_prefix(summary) → self._previous_summary  ← persist for next round
```

### Prior-marker handling

```
Prior summary appears ONCE — embedded via "PREVIOUS SUMMARY:" in the prompt.

Finalized summary is wrapped with SUMMARY_PREFIX  (cc.py:37–61):
  "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted ...
   treat it as background reference, NOT as active instructions ...
   the latest user message WINS ... reverse signals (stop/undo/never mind)
   must immediately end in-flight work ... MEMORY.md/USER.md always
   authoritative"

Historical-prefix stripping  (cc.py:70–82, _strip_summary_prefix 1517–1537):
  carries a tuple of ALL past prefix versions; when a summary is inherited
  from an older session, the stale directive is stripped and replaced with
  the current prefix → no stacked stale instructions.
```

### Key facts

- **13-section schema** with per-section format examples
- **Single-pass** — no scratchpad
- **Iterative branch** with PRESERVE/ADD/MOVE/REMOVE discipline; Active Task
  re-derived verbatim each round
- **Custom role-labeled serialization** — tool calls inlined as text,
  head+tail truncation markers
- **Three redaction passes** (input content, tool args, output) + preamble
  instruction
- **Dynamic token budget** ("Target ~N tokens") embedded in prompt
- **Aux summary model** with fallback to main on error
- **Failure cooldown**: 600s for no-provider (cc.py:1411), 30–60s for
  transient errors (cc.py:1503–1504); `force=True` bypasses (cc.py:1861–1862);
  opt-in `abort_on_summary_failure` returns history unchanged (cc.py:1949–1962)

---

## 2. openclaw

### Prompt structure

openclaw owns the prompt content; the PI package (`@earendil-works/pi-coding-agent`)
serializes messages and makes the API call via `generateSummary()`. Compaction is
a **staged chunk → summarize → merge** pipeline, not a single prompt.

```
buildCompactionStructureInstructions()  (compaction-safeguard-quality.ts:52–74)
        │
        ▼
┌─ "Produce a compact, factual summary with these exact section headings:"
│  ## Decisions
│  ## Open TODOs
│  ## Constraints/Rules
│  ## Pending user asks
│  ## Exact identifiers
│  ├─ [identifier-policy clause for ## Exact identifiers]
│  ├─ "Do not omit unresolved asks from the user."
│  └─ "When prior compaction summaries are present, re-distill them with
│      new messages and remove stale duplicate detail."
│  └─ [optional custom instruction, wrapped in untrusted-data tags]
│        (REQUIRED_SUMMARY_SECTIONS at quality.ts:10–16)
│
├─ identifier-preservation policy  (quality.ts:17–20, 37–49; default "strict" @33)
│  strict:  "preserve literal values exactly as seen
│            (IDs, URLs, file paths, ports, hashes, dates, times)"
│  off:     "include identifiers only when needed for continuity"
│  custom:  operator text, wrapped (≤4000 chars)
│
├─ prior summary (iterative)  (compaction-safeguard.ts:82–104)
│  prepended as a user message:
│    <previous-compaction-summary>
│    "Previous compaction summary to re-distill ... Prune stale, duplicate,
│     or superseded details instead of preserving it verbatim."
│    {previousSummary}
│    </previous-compaction-summary>
│
└─ MERGE_SUMMARIES_INSTRUCTIONS  (compaction.ts:25–38) — multi-part merge stage
   "Merge these partial summaries into a single cohesive summary.
    MUST PRESERVE: active tasks + status, batch progress (e.g. '5/17'),
    last user request, decisions + rationale, TODOs, constraints ...
    PRIORITIZE recent context over older history."
```

### Assembly logic

```
compaction.ts  (staged pipeline)
        │
        ├─→ stripToolResultDetails()  (session-transcript-repair.ts:284–302)
        │      └─ removes toolResult.details before ANY summarization (compaction.ts:331)
        │
        ├─→ splitMessagesByTokenShare()  (compaction.ts:137–231)
        │      ├─ BASE_CHUNK_RATIO 0.4, MIN 0.15, SAFETY_MARGIN 1.2  (compaction.ts:20–22)
        │      ├─ computeAdaptiveChunkRatio()  (compaction.ts:284–303)
        │      │     shrinks ratio when avg msg > 10% of context window
        │      └─ tool-use/tool-result pairs kept whole at chunk boundaries
        │
        ├─→ summarizeChunks()  (compaction.ts:314–363)
        │      └─ per-chunk generateSummary(), prior summary carried forward
        │         into the next chunk (iterative re-distillation)
        │      └─ SUMMARIZATION_OVERHEAD_TOKENS = 4096 reserved (compaction.ts:236)
        │         for PI reasoning:"high" + system prompt + wrappers
        │      └─ retryAsync attempts:3, 500ms→5s backoff
        │
        ├─→ summarizeInStages()  (compaction.ts:466–530)  — if >1 part
        │      └─ summarize each part, then MERGE_SUMMARIES_INSTRUCTIONS
        │
        └─→ auditSummaryQuality()  (compaction-safeguard-quality.ts:220–246)
               └─ extracts identifiers via regex; in strict mode every
                  extracted identifier must appear in the summary
               └─ on failure, re-run with quality-feedback prompt
                  (compaction-safeguard.ts:1170–1267), wrapped via
                  wrapUntrustedInstructionBlock()
        │
        ▼
cap at MAX_COMPACTION_SUMMARY_CHARS=16000 + SUMMARY_TRUNCATED_MARKER
  (compaction-safeguard.ts:66, 69) — body capped, diagnostic suffix preserved
        │
        ▼
model = main session model;  PI forces reasoning:"high"
```

### Handoff snapshot (quota-limit takeover)

```
summarizeForHandoff()  (compaction.ts:599–627)  — hard cap 4000 tokens (@619)
  HANDOFF_INSTRUCTIONS  (compaction.ts:43–57):
    "Generate a concise recovery briefing for a new LLM taking over ...
     LEADER HIERARCHY REINFORCEMENT:
     - state the new model is the LEADER (Orchestrator)
     - identify active autonomous units (AutoClaw) as SUBORDINATES
     - supervise, do NOT perform the subordinate's task
     MUST CAPTURE: goal + project path, latest tool-exec status,
     critical files, pending items + next steps."
  pruneHistoryForContextShare()  (compaction.ts:532–597)
    maxHistoryShare = 0.2 (handoff) vs 0.5 (normal)
```

### Key facts

- **5-section schema** (Decisions / Open TODOs / Constraints-Rules / Pending
  user asks / Exact identifiers), validated post-hoc by a quality audit
- **Staged chunk → summarize → merge** — only peer that chunks; adaptive
  token-share ratio (0.4→0.15) with 1.2× safety margin
- **Iterative branch** — prior summary prepended in `<previous-compaction-summary>`
  fence with an explicit "re-distill, don't preserve verbatim" instruction;
  also carried forward chunk-to-chunk
- **Configurable identifier-preservation policy** (strict/off/custom) — unique
- **`toolResult.details` stripped** before chunking (lossy pre-pass)
- **Quality-audit retry loop** — re-prompts with feedback if required sections
  or strict identifiers are missing; feedback wrapped as untrusted data
- **4K leader/subordinate handoff snapshot** for quota-limit takeover — unique
- **Main session model**, PI-forced `reasoning:"high"`; 3-retry backoff +
  size-fallback chain
- **No in-prompt token budget signal** — budget enforced via chunk sizing +
  the 16K char output cap

---

## 3. opencode

### Prompt structure

```
buildPrompt({ previousSummary?, context })  (compaction.ts:125–136)
        │
        ▼
┌─ Anchor (branches on previousSummary):
│  │
│  ├─ IF previousSummary:  (compaction.ts:127–133)
│  │  ┌─ "Update the anchored summary below using the conversation"
│  │  ├─ "history above. Preserve still-true details, remove stale"
│  │  ├─ "details, and merge in the new facts."
│  │  ├─ <previous-summary>                  ← XML fence
│  │  ├─ {previousSummary}
│  │  └─ </previous-summary>
│  │
│  └─ ELSE:  (compaction.ts:134)
│     "Create a new anchored summary from the conversation history above."
│
├─ SUMMARY_TEMPLATE  (compaction.ts:44–79)
│  "Output exactly the Markdown structure shown inside <template> ...
│   Do not include the <template> tags in your response."
│  <template>                                ← XML fence
│    ## Goal
│    ## Constraints & Preferences
│    ## Progress
│      ### Done
│      ### In Progress
│      ### Blocked
│    ## Key Decisions
│    ## Next Steps
│    ## Critical Context
│    ## Relevant Files
│  </template>   (each bullet ends "... or \"(none)\"")
│  Rules:  (compaction.ts:76–79)
│  - Keep every section, even when empty.
│  - Use terse bullets, not prose paragraphs.
│  - Preserve exact file paths, commands, error strings, and identifiers.
│  - Do not mention the summary process or that context was compacted.
│
└─ ...context  (plugin-injected strings, joined with \n\n)
```

A dedicated `compaction` agent supplies the SYSTEM prompt
(`agent/prompt/compaction.txt:1–10`): *"You are an anchored context
summarization assistant ... If the prompt includes a `<previous-summary>`
block, treat it as the current anchored summary ... Do not mention that you
are summarizing ... Respond in the same language as the conversation."*

### Assembly logic

```
processCompaction(input)  (compaction.ts:345–583)
        │
        ▼
history = messages[:exclude trigger]  (compaction.ts:389)
        │
        ├─→ completedCompactions(history)  (compaction.ts:107–123)
        │      └─ scan: user msgs with parts.type=="compaction"
        │      └─ pair with assistant msgs where summary && finish && !error
        │      └─ summaryText() extracts text parts only (compaction.ts:97–105)
        │           │
        │           ▼
        │   hidden = Set(every prior userIdx + assistantIdx)  (compaction.ts:391)
        │   previousSummary = prior.at(-1)?.summary           (compaction.ts:392)
        │
        ├─→ history.filter((_, i) => !hidden.has(i))   ← STRIP prior markers
        │
        ├─→ plugin.trigger("experimental.session.compacting", ...)  (compaction.ts:399–403)
        │      └─ default { context: [], prompt: undefined }
        │      └─ plugin may inject context strings OR override prompt entirely
        │
        ├─→ plugin.trigger("experimental.chat.messages.transform", ...)  (compaction.ts:406)
        │
        ▼
nextPrompt = compacting.prompt ?? buildPrompt({ previousSummary, context })
        │
        ▼
toModelMessagesEffect(selected, model, {  (compaction.ts:407–410)
    stripMedia: true,
    toolOutputMaxChars: TOOL_OUTPUT_MAX_CHARS  ← 2000 (compaction.ts:39)
})
        │
        ▼
LLM call (dedicated "compaction" agent, hidden, perms "*":"deny",
          own model option else inherits user msg model)  (agent.ts:202–216)
```

### Prior-marker handling

```
completedCompactions() returns {userIndex, assistantIndex, summary} per pair.
hidden = Set([...userIndexes, ...assistantIndexes]) → both the trigger user
msg AND the summary assistant msg are filtered out of the head.

Result: prior summary appears EXACTLY ONCE in LLM input
        - via <previous-summary> XML fence in the prompt
        - prior pair structurally absent from message history
Test:   expect(captured.match(/summary one/g)?.length).toBe(1)
        (compaction.test.ts:1447)
```

### Key facts

- **7-section terse schema** (Goal / Constraints / Progress[Done,In Progress,
  Blocked] / Key Decisions / Next Steps / Critical Context / Relevant Files)
  inside a `<template>` XML fence
- **Explicit `(none)` literal** rule for empty sections
- **Single-pass** — no scratchpad
- **Iterative branch** with `<previous-summary>` XML fence
- **Structural prior-marker filter** via index `Set` — prior pairs removed from
  history entirely (only system besides codex to filter structurally)
- **Native message format**; `stripMedia` + per-message tool output cap 2000 chars
- **Plugin hooks** can inject context strings, override the prompt, or transform
  the message array
- **Dedicated `compaction` agent** with its own system prompt + independent
  model config, tool perms denied
- **No redaction, no in-prompt token budget**
- vs prior HEAD (a78605f8e): only a schema/type migration
  (`MessageV2.WithParts`→`SessionV1.WithParts`); no functional prompt change

---

## 4. codex

### Prompt structure

Two small Markdown assets, `include_str!`-embedded as Rust consts
(`prompts/src/compact.rs:1–2`; re-exported `core/src/compact.rs:47–48`).

```
SUMMARIZATION_PROMPT  (prompts/templates/compact/prompt.md:1–9)
┌─ "You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff
│   summary for another LLM that will resume the task."
│  Include:
│  - Current progress and key decisions made
│  - Important context, constraints, or user preferences
│  - What remains to be done (clear next steps)
│  - Any critical data, examples, or references needed to continue
└─ "Be concise, structured, and focused on helping the next LLM
    seamlessly continue the work."

SUMMARY_PREFIX  (prompts/templates/compact/summary_prefix.md:1)
└─ "Another language model started to solve this problem and produced a
    summary of its thinking process. You also have access to the state of
    the tools that were used ... build on the work already done and avoid
    duplicating work ..."
```

- **No `## Section` headers** — a 4-bullet content list, not a fixed schema
- **No explicit anti-meta clause**; conciseness implied by audience framing
- **Audience:** "another LLM that will resume the task" (peer-handoff frame)
- **Config override:** `compact_prompt: Option<String>` (config/mod.rs:675;
  resolved turn_context.rs:339–343) and experimental
  `experimental_compact_prompt_file` (config/mod.rs:3251–3258)

### Assembly logic

```
run_compact_task  (core/src/compact.rs)
        │
        ▼
history = sess.clone_history()  (compact.rs:187)
        │
        ├─→ for_prompt(input_modalities)  (compact.rs:204)  ← strip unsupported images
        │
        ├─→ Prompt { input: turn_input, base_instructions, personality, .. }
        │      (compact.rs:206–211)  ← NATIVE ResponseItems, not serialized
        │
        ├─→ remote V2 (default): input + ResponseItem::CompactionTrigger
        │      (compact_remote_v2.rs:209–219); RETAINED_MESSAGE_TOKEN_BUDGET 64000 (@50)
        │
        ├─→ rewritten_output_for_context_window()  (compact_remote.rs:415–443)
        │      └─ truncate oversized FunctionCallOutput / tool outputs
        │
        ▼
LLM produces summary  (empty → "(no summary available)", compact.rs:526–530)
        │
        ▼
build_compacted_history(user_messages, summary_text)  (compact.rs:489–540)
   ├─ summary_text = format!("{SUMMARY_PREFIX}\n{summary}")  (compact.rs:273)
   ├─ keep newest user messages up to COMPACT_USER_MESSAGE_MAX_TOKENS=20000
   │     (compact.rs:49, reverse-accumulate 495–513)
   └─ InitialContextInjection (compact.rs:51–64):
        DoNotInject (manual/pre-turn) | BeforeLastUserMessage (mid-turn)
        │
        ▼
replace_compacted_history()  (session/mod.rs:2637–2660)
   ├─ persist RolloutItem::Compacted(CompactedItem{message, replacement_history})
   │     (CompactedItem @ compact.rs:290–293)
   └─ model_client.advance_window_generation()  ← checkpoint marker
```

### Prior-marker handling

```
Prior summaries are detected structurally by their prefix:
  is_summary_message(m) := m.starts_with("{SUMMARY_PREFIX}\n")  (compact.rs:415–417)
collect_user_messages() filters these out (compact.rs:399–412), so a prior
summary is NOT re-fed as a user message — it appears once, as the carried
replacement-history summary. Avoids summary-on-summary recursion.
```

### Tool-schema trimming (separate from the prompt)

```
MAX_COMPACT_TOOL_SCHEMA_BYTES = 4000, depth 2  (tools/src/json_schema.rs:192–193)
compact_large_tool_schema()  (json_schema.rs:199–214), best-effort 3-pass:
  strip_schema_descriptions → drop_schema_definitions
  → collapse_deep_schema_objects_from_root
```

### Key facts

- **Minimal instructive prompt** — no section schema, no examples; lowest
  verbosity of any peer
- **Native message history** (ResponseItems), not serialized; oversized tool
  outputs truncated pre-summary
- **Iterative via SUMMARY_PREFIX** — prior summary carried in replacement
  history and structurally filtered from re-summarization (no XML fence; plain
  prefix string)
- **History-replacement checkpoint** — `CompactedItem{message, replacement_history}`
  persisted as a rollout event; `advance_window_generation()` marks the window
- **20K-token user-message budget** kept verbatim alongside the summary;
  `(no summary available)` fallback for empty output
- **Initial-context reinjection** policy distinguishes manual vs mid-turn
- **Tool-schema trimming to ≤4KB** is a parallel, prompt-independent lever
- **Prompt overridable** via config string or experimental file
- **Post-compaction warning** to start a fresh thread (compact.rs:300–303)
- **No in-prompt token budget signal, no explicit redaction clause**

---

## 5. Side-by-side

### Prompt assembly

```
                hermes              openclaw            opencode            codex
                ─────────           ─────────           ─────────           ─────────
preamble        summarizer-role     structure+policy    dedicated-agent     "checkpoint
                + redaction         instructions        system prompt        compaction"
template        13 sect + examples  5 sect + audit      7 sect terse        4-bullet list
                                                        <template> fence    (no headers)
iterative       "PREVIOUS SUMMARY:" <previous-compaction <previous-summary>  SUMMARY_PREFIX
branch          plain label          -summary> fence     XML fence           plain prefix
                                     + re-distill instr
staging         single-pass         chunk→summ→merge    single-pass         single-pass
budget signal   "Target ~{N} tok"   none (in-prompt)    none                none
extension       focus_topic         identifier policy   plugin hooks        config/file override
                                     + custom instr
```

### Message handling

```
                hermes              openclaw            opencode            codex
                ─────────           ─────────           ─────────           ─────────
format to LLM   custom labeled      PI-serialized       native messages     native ResponseItems
                text block          (chunked)
tool calls      inlined as text     details stripped    native, output      output truncated
                                    pre-summary          capped 2000          to fit window
truncation      head+tail marker    16K char cap +      tool output cap     20K user-msg budget;
                (6K/4K/1.5K)        adaptive chunking                       tool-schema ≤4KB
prior marker    once (label) +      prepended once      filtered by index   filtered by prefix
                prefix-strip prior   (re-distill)                            (is_summary_message)
```

### Discipline

```
                hermes              openclaw            opencode            codex
                ─────────           ─────────           ─────────           ─────────
empty sections  implicit            required-section    explicit "(none)"   "(no summary
                                    audit                                    available)" fallback
anti-meta       "no preamble"       implicit            "no summary process" implicit (concise)
redaction       3-stage             untrusted-data      none                none
                                    wrap (injection)
failure         600s/30-60s         3-retry backoff +   —                   remove-oldest retry
handling        cooldown + abort     quality retry +                        + window-exceeded err
                                     size-fallback
agent identity  aux summary_model   main, reasoning     dedicated agent     same model
                                    "high"               (own model opt)
unique lever    token budget +      chunking +          structural index    history-replacement
                prefix versioning    handoff snapshot     filter + plugins    checkpoint + schema trim
```

---

## 6. Design axes summary

```
SCHEMA VERBOSITY
   terse ◄──────────────────────────────────────────► verbose
   codex            opencode      openclaw            hermes
   (no headers)     (7 terse)     (5+audit)           (13+examples)

STAGING
   single-pass ◄────────────────────────────────────► multi-stage
   hermes, opencode, codex                            openclaw
                                                      (chunk→summ→merge)

PRIOR MARKER HANDLING
   structural filter ◄──────────────────────────────► prompt-embedded
   opencode (index Set)        openclaw (prepend)
   codex (prefix match)        hermes (label + prefix-strip)

XML FENCING (prior summary)
   none ◄───────────────────────────────────────────► fenced
   hermes (label)      codex (prefix)      opencode, openclaw

CREDENTIAL / INJECTION DISCIPLINE
   none ◄───────────────────────────────────────────► hardened
   opencode, codex          openclaw (untrusted-wrap)    hermes (3-stage redact)

TOKEN BUDGET IN PROMPT
   none ◄───────────────────────────────────────────► explicit
   openclaw, opencode, codex                          hermes ("Target ~N")
```

---

## 7. Convergences

- **All four are iterative** — every peer now carries a prior summary forward
  (hermes label, openclaw + opencode XML fence, codex prefix). None compacts
  purely from scratch on repeat passes.
- All four frame the summarizer as writing a handoff for a *different* reader
  ("checkpoint" / "new LLM taking over" / "anchored summary" / "another LLM").
- Three of four (all but codex) use Markdown `## Section` headers as the
  primary structure; codex uses a free-form 4-bullet content list.
- None pre-extracts files/todos into a separate side-channel block — the model
  enumerates them into the summary body.

## 8. Divergences

- **Staged chunk → merge**: only openclaw
- **Credential redaction**: only hermes (three-stage)
- **Untrusted-data injection wrap**: only openclaw
- **In-prompt token budget**: only hermes
- **Configurable identifier-preservation policy**: only openclaw
- **Leader/subordinate handoff snapshot**: only openclaw
- **Structural prior-marker filtering**: opencode (index Set) + codex (prefix
  match); hermes strips stale prefixes; openclaw prepends without filtering
- **Dedicated summarizer agent/model**: opencode (dedicated agent) + hermes
  (aux model); openclaw + codex reuse the main model
- **Tool-schema trimming**: only codex
- **History-replacement checkpoint event**: only codex
- **Quality-audit retry loop**: only openclaw
- **Plugin / config prompt override**: opencode (plugin hooks), codex (config +
  experimental file); openclaw (custom identifier instruction)
- **Per-section format examples**: only hermes

---

## Appendix: source line index

| Item | File | Lines |
|------|------|-------|
| hermes preamble | context_compressor.py | 1253–1265 |
| hermes 13-section template | context_compressor.py | 1268–1336 |
| hermes token budget line | context_compressor.py | 1339 |
| hermes iterative branch | context_compressor.py | 1343–1357 |
| hermes from-scratch branch | context_compressor.py | 1358–1369 |
| hermes focus topic | context_compressor.py | 1371–1377 |
| hermes serialization | context_compressor.py | 946–999 |
| hermes redaction passes | context_compressor.py | 960, 981, 1402 |
| hermes budget computation | context_compressor.py | 926–935 |
| hermes SUMMARY_PREFIX | context_compressor.py | 37–61 |
| hermes prefix stripping | context_compressor.py | 70–82, 1517–1537 |
| hermes failure cooldown | context_compressor.py | 1411, 1503–1504 |
| hermes force/abort | context_compressor.py | 1861–1862, 1949–1962 |
| openclaw structure instructions | pi-hooks/compaction-safeguard-quality.ts | 52–74 |
| openclaw required sections | pi-hooks/compaction-safeguard-quality.ts | 10–16 |
| openclaw identifier policy | pi-hooks/compaction-safeguard-quality.ts | 17–20, 33, 37–49 |
| openclaw merge instructions | compaction.ts | 25–38 |
| openclaw handoff instructions | compaction.ts | 43–57 |
| openclaw handoff (cap + prune) | compaction.ts | 532–597, 599–627 |
| openclaw chunk params | compaction.ts | 20–22, 236, 284–303 |
| openclaw chunk split / summarize | compaction.ts | 137–231, 314–363, 466–530 |
| openclaw prior-summary prepend | pi-hooks/compaction-safeguard.ts | 82–104 |
| openclaw quality audit / retry | pi-hooks/compaction-safeguard-quality.ts | 220–246; safeguard.ts 1170–1267 |
| openclaw char cap + marker | pi-hooks/compaction-safeguard.ts | 66, 69 |
| openclaw strip toolResult.details | session-transcript-repair.ts | 284–302 |
| opencode buildPrompt | compaction.ts | 125–136 |
| opencode SUMMARY_TEMPLATE | compaction.ts | 44–79 |
| opencode completedCompactions | compaction.ts | 107–123 |
| opencode summaryText | compaction.ts | 97–105 |
| opencode marker filter (Set) | compaction.ts | 391–394 |
| opencode plugin hooks | compaction.ts | 399–403, 406 |
| opencode message convert / cap | compaction.ts | 39, 407–410 |
| opencode dedicated agent | agent.ts | 202–216 |
| opencode agent system prompt | agent/prompt/compaction.txt | 1–10 |
| opencode dedup test | compaction.test.ts | 1447 |
| codex prompt constants | prompts/src/compact.rs | 1–2 |
| codex SUMMARIZATION_PROMPT | prompts/templates/compact/prompt.md | 1–9 |
| codex SUMMARY_PREFIX | prompts/templates/compact/summary_prefix.md | 1 |
| codex prompt assembly | core/src/compact.rs | 187, 204, 206–211 |
| codex remote V2 | core/src/compact_remote_v2.rs | 50, 209–219 |
| codex output rewrite | core/src/compact_remote.rs | 415–443 |
| codex is_summary_message | core/src/compact.rs | 399–412, 415–417 |
| codex replacement history | core/src/compact.rs | 49, 273, 290–293, 489–540 |
| codex empty fallback | core/src/compact.rs | 526–530 |
| codex initial-context injection | core/src/compact.rs | 51–64 |
| codex checkpoint | core/src/session/mod.rs | 2637–2660 |
| codex tool-schema trim | tools/src/json_schema.rs | 192–193, 199–214 |
| codex prompt override | core/src/config/mod.rs | 675, 3251–3258; turn_context.rs 339–343 |
| codex post-compact warning | core/src/compact.rs | 300–303 |
