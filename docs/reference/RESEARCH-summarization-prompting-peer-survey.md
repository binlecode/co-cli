# RESEARCH: Summarization Prompting Design — Peer Survey

How fork-claude-code, hermes-agent, and opencode structure the prompt sent
to the LLM during context compaction. Records facts; does not prescribe
co-cli direction.

## Sources

| System | Path |
|--------|------|
| fork-cc | `services/compact/prompt.ts`, `services/compact/compact.ts` |
| hermes | `agent/context_compressor.py` |
| opencode | `packages/opencode/src/session/compaction.ts` |

---

## 1. fork-claude-code

### Prompt structure

```
┌─ NO_TOOLS_PREAMBLE  (prompt.ts:19–26)
│  "CRITICAL: Respond with TEXT ONLY. Do NOT call any tools."
│
├─ BASE_COMPACT_PROMPT  (prompt.ts:61–143)
│  ├─ Task framing: "create a detailed summary"
│  ├─ DETAILED_ANALYSIS_INSTRUCTION  (prompt.ts:31–44)
│  │  └─ "wrap your analysis in <analysis> tags"
│  ├─ 9-section schema:
│  │   1. Primary Request and Intent
│  │   2. Key Technical Concepts
│  │   3. Files and Code Sections
│  │   4. Errors and fixes
│  │   5. Problem Solving
│  │   6. All user messages
│  │   7. Pending Tasks
│  │   8. Current Work
│  │   9. Optional Next Step  ← verbatim quote required
│  └─ <example> ... </example>  (full rendering, prompt.ts:79–129)
│
├─ "Additional Instructions:\n<customInstructions>"  (optional)
│
└─ NO_TOOLS_TRAILER  (prompt.ts:269–272)
   "REMINDER: Do NOT call any tools. ... <analysis> + <summary> only."
```

### Assembly logic

```
getCompactPrompt(customInstructions?)
        │
        ▼
preamble + BASE + (customInstructions?) + trailer
        │
        ▼
  LLM call (maxTurns: 1, system="...summarizing conversations.")
        │
        ▼
  raw output: <analysis>...</analysis><summary>...</summary>
        │
        ▼
formatCompactSummary()  (prompt.ts:311–335)
   └─ strip <analysis> entirely  (it's a scratchpad)
   └─ replace <summary>X</summary> → "Summary:\nX"
```

### Variants

```
getCompactPrompt()         → BASE_COMPACT_PROMPT          (full convo)
getPartialCompactPrompt()  → PARTIAL_COMPACT_PROMPT       (recent only)
                          OR PARTIAL_COMPACT_UP_TO_PROMPT (head only)
```

### Key facts

- **Two-pass `<analysis>` scratchpad** stripped post-hoc — drafting buffer
- **No iterative branch** — every compaction is from-scratch
- **Extraction**: LLM enumerates from `message_history`; no programmatic
  pre-extraction
- **Customization**: `customInstructions` text appended before trailer
- **No redaction**, no token budget signal in prompt

---

## 2. hermes-agent

### Prompt structure

```
┌─ _summarizer_preamble  (context_compressor.py:743–756)
│  "You are a summarization agent ... DIFFERENT assistant ...
│   NEVER include API keys, tokens, passwords ... [REDACTED]"
│
├─ Branch wrapper:
│  │
│  ├─ IF self._previous_summary:  (cc.py:818–832)
│  │  ┌─ "You are updating a context compaction summary."
│  │  ├─ "PREVIOUS SUMMARY:"
│  │  ├─ {self._previous_summary}             ← session variable
│  │  ├─ "NEW TURNS TO INCORPORATE:"
│  │  ├─ {content_to_summarize}               ← serialized turns
│  │  └─ "PRESERVE / ADD / MOVE / REMOVE ... CRITICAL: Update Active Task"
│  │
│  └─ ELSE:  (cc.py:833–844)
│     ┌─ "Create a structured handoff summary ..."
│     ├─ "TURNS TO SUMMARIZE:"
│     ├─ {content_to_summarize}
│     └─ "Use this exact structure:"
│
├─ _template_sections  (cc.py:759–816)  ← shared by both branches
│  ## Active Task             ← verbatim user request, MOST IMPORTANT
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
│  ## Remaining Work
│  ## Critical Context
│  └─ "Target ~{summary_budget} tokens. Be CONCRETE ..."
│
└─ FOCUS TOPIC  (optional, appended last for precedence)  (cc.py:848–852)
   "PRIORITISE preserving ... 60-70% of summary token budget"
```

### Assembly logic

```
_generate_summary(turns, focus?)
        │
        ▼
turns_to_summarize  ──┐
        │             │
        ├─→ _serialize_for_summary()  (cc.py:656–709)
        │       │
        │       ▼
        │   labeled text:
        │     [USER]: ...
        │     [ASSISTANT]: ...
        │       [Tool calls: read_file({"path":"..."}) ...]
        │     [TOOL RESULT id]: <head>...[truncated]...<tail>
        │       │
        │       └─ redact_sensitive_text() applied
        │
        └─→ _compute_summary_budget()  → embedded in template
        │
        ▼
preamble + branch_wrapper + template + (focus?)
        │
        ▼
LLM call (max_tokens = budget * 1.3)
        │
        ▼
redact_sensitive_text(output)  ← second redaction pass
        │
        ▼
self._previous_summary = summary  ← persist for next round
```

### Prior-marker handling

```
Round 1:  [m0, m1, m2, m3, m4, m5]      protect_first_n=3
          │                              compress_start=3
          └ summarize messages[3:end] ─→ summary_marker
          new list: [m0, m1, m2, summary_marker, ...tail...]

Round 2:  compress_start = protect_first_n = 3
          messages[3] = summary_marker  ← STILL AT compress_start
          turns_to_summarize = messages[3:end]  ← INCLUDES marker

Result: prior summary appears TWICE in LLM input
        - once via PREVIOUS SUMMARY: in prompt
        - once via marker inside content_to_summarize
Mitigation: SUMMARY_PREFIX text "[CONTEXT COMPACTION — REFERENCE ONLY]"
            (relies on instruction-following)
```

### Key facts

- **12-section schema** with format examples per section
- **Single-pass** — no scratchpad
- **Iterative branch** with PRESERVE/ADD/MOVE/REMOVE discipline
- **Custom labeled serialization** — tool calls inlined as text
- **Three-stage redaction** (input content, tool args, output)
- **Dynamic token budget** embedded in prompt
- **Failure cooldown** suppresses summarization after errors

---

## 3. opencode

### Prompt structure

```
buildPrompt({ previousSummary?, context })  (compaction.ts:124–135)
        │
        ▼
┌─ Anchor (branches on previousSummary):
│  │
│  ├─ IF previousSummary:
│  │  ┌─ "Update the anchored summary below using the conversation"
│  │  ├─ "history above. Preserve still-true details, remove stale"
│  │  ├─ "details, and merge in the new facts."
│  │  ├─ <previous-summary>                  ← XML fence
│  │  ├─ {previousSummary}
│  │  └─ </previous-summary>
│  │
│  └─ ELSE:
│     "Create a new anchored summary from the conversation history above."
│
├─ SUMMARY_TEMPLATE  (compaction.ts:43–78)
│  "Output exactly the Markdown structure shown inside <template> ..."
│  <template>                                ← XML fence
│    ## Goal
│    - [single-sentence task summary]
│    ## Constraints & Preferences
│    - [user constraints, preferences, specs, or "(none)"]
│    ## Progress
│    ### Done
│    - [completed work or "(none)"]
│    ### In Progress
│    - [current work or "(none)"]
│    ### Blocked
│    - [blockers or "(none)"]
│    ## Key Decisions
│    - [decision and why, or "(none)"]
│    ## Next Steps
│    - [ordered next actions or "(none)"]
│    ## Critical Context
│    - [important technical facts, errors, open questions, or "(none)"]
│    ## Relevant Files
│    - [file or directory path: why it matters, or "(none)"]
│  </template>
│  Rules:
│  - Keep every section, even when empty.
│  - Use terse bullets, not prose paragraphs.
│  - Preserve exact file paths, commands, error strings, identifiers.
│  - Do not mention the summary process or that context was compacted.
│
└─ ...context  (plugin-injected strings, joined with \n\n)
```

### Assembly logic

```
processCompaction(input)
        │
        ▼
history = messages[:exclude trigger]
        │
        ├─→ completedCompactions(history)  (compaction.ts:106–122)
        │      └─ scan: find user msgs with parts.type=="compaction"
        │      └─ pair with assistant msgs where info.summary && info.finish
        │      └─ return [{userIndex, assistantIndex, summary}, ...]
        │           │
        │           ▼
        │   hidden = Set([userIdx, assistantIdx, ...])  ← every prior pair
        │   previousSummary = prior.at(-1)?.summary     ← extract text only
        │
        ├─→ history.filter((_, i) => !hidden.has(i))   ← STRIP markers
        │      │
        │      ▼
        │   selected.head  (with tail preservation budget applied)
        │
        ├─→ plugin.trigger("experimental.session.compacting", ...)
        │      └─ { context: [], prompt: undefined }  (default)
        │
        ▼
nextPrompt = compacting.prompt
          ?? buildPrompt({ previousSummary, context: compacting.context })
        │
        ▼
modelMessages = toModelMessagesEffect(selected.head, model, {
                  stripMedia: true,
                  toolOutputMaxChars: 2000  ← per-message tool output cap
                })
        │
        ▼
LLM call (dedicated "compaction" agent, own model option)
```

### Prior-marker handling

```
Round 1:  [user(...), assistant(...), user[compaction], assistant[summary]]
                                      └────────┬────────┘└────────┬────────┘
                                          userIndex          assistantIndex

Round 2:  history = [...prior pairs..., new turns...]
          completedCompactions(history)
            → prior = [{userIdx: 2, assistantIdx: 3, summary: "..."}]
            → hidden = {2, 3}
          history.filter((_, i) => !hidden.has(i))
            → removes BOTH the trigger user msg AND the summary msg
          previousSummary = prior.at(-1).summary  ← extracted text

Result: prior summary appears EXACTLY ONCE in LLM input
        - via <previous-summary> XML fence in prompt
        - prior pair structurally absent from message history
Test:   expect(captured.match(/summary one/g)?.length).toBe(1)
        (compaction.test.ts:1706)
```

### Key facts

- **9-section terse schema** with `<template>` XML fence
- **Explicit `(none)` literal** rule for empty sections
- **No format examples** in prompt (vs hermes/fork-cc)
- **Single-pass** — no scratchpad
- **Iterative branch** with `<previous-summary>` XML-fenced
- **Structural prior-marker filter** via index `Set` — only system that does this
- **Native message format** (not custom serialization); per-message tool
  output cap at 2000 chars
- **Plugin hook** can inject context strings or override prompt entirely
- **Dedicated `compaction` agent** with independent model config
- **No redaction**, **no token budget** in prompt

---

## 4. Side-by-side

### Prompt assembly

```
                    fork-cc            hermes             opencode
                    ─────────          ─────────          ─────────
preamble            no-tools           summarizer-role    none
template            9 sect + example   12 sect + examples 9 sect terse
scratchpad          <analysis>         none               none
iterative branch    NO                 plain-text         <previous-summary>
                                       PREVIOUS SUMMARY:  XML-fenced
budget signal       none               "Target ~{N} tok"  none
extension           customInstructions focus_topic        plugin hook
trailer             no-tools reminder  none               none
```

### Message handling

```
                    fork-cc            hermes             opencode
                    ─────────          ─────────          ─────────
format to LLM       native             custom labeled     native
                    messages           text block         messages
tool calls          in history         inlined in text    native, output
                                                          capped 2000
truncation          none               head+tail marker   tool output cap
prior marker        passes through¹    passes through     filtered by index
serialization       (none)             _serialize_for_    (none)
                                       summary()
```

¹ except `partialCompactConversation` `direction='up_to'`, compact.ts:798

### Discipline

```
                    fork-cc            hermes             opencode
                    ─────────          ─────────          ─────────
empty sections      implicit           implicit           explicit "(none)"
verbatim quote      Section 9          Section 1          none
anti-meta           implicit           "no preamble"      "no summary process"
redaction           none               3-stage            none
failure cooldown    none               yes                none
agent identity      same as caller     summary_model opt  dedicated agent
```

---

## 5. Design axes summary

```
EXTRACTION STRATEGY
   programmatic ◄──────────────────────────► LLM-driven
                                  hermes  fork-cc
                                  opencode

SCHEMA VERBOSITY
   terse ◄──────────────────────────────────► verbose
   opencode              hermes              fork-cc

PASSES
   single-pass ◄────────────────────────────► two-pass
   hermes, opencode                          fork-cc

PRIOR MARKER HANDLING
   structural filter ◄──────────────────────► instruction-mitigated
   opencode                       hermes
                                  fork-cc-main (no mitigation)

XML FENCING
   none ◄───────────────────────────────────► extensive
   hermes               fork-cc              opencode
                        (output tags)        (content boundaries)

CREDENTIAL DISCIPLINE
   none ◄───────────────────────────────────► three-stage
   fork-cc, opencode                         hermes
```

---

## 6. Convergences

- All three forbid meta-commentary about the summary process
- All three frame summarizer as writing for a different audience
- All three use Markdown `## Section` headers as primary structure
- None pre-extract files/todos into a side-channel block

## 7. Divergences

- **Two-pass scratchpad**: only fork-cc
- **Custom serialization**: only hermes
- **Credential redaction**: only hermes
- **Plugin extension**: only opencode
- **Structural prior-marker filter**: only opencode
- **Token budget in prompt**: only hermes
- **Iterative branch**: hermes + opencode (fork-cc absent)
- **Dedicated summarizer agent**: only opencode
- **Failure cooldown**: only hermes

---

## Appendix: source line index

| Item | File | Lines |
|------|------|-------|
| fork-cc preamble/trailer | prompt.ts | 19–26, 269–272 |
| fork-cc analysis instruction | prompt.ts | 31–44 |
| fork-cc 9-section schema | prompt.ts | 61–143 |
| fork-cc partial variants | prompt.ts | 145–267 |
| fork-cc format/strip | prompt.ts | 311–335 |
| fork-cc partial filter | compact.ts | 798 |
| hermes preamble | context_compressor.py | 743–756 |
| hermes 12-section template | context_compressor.py | 759–816 |
| hermes iterative branch | context_compressor.py | 818–832 |
| hermes from-scratch branch | context_compressor.py | 833–844 |
| hermes focus topic | context_compressor.py | 848–852 |
| hermes serialization | context_compressor.py | 656–709 |
| hermes redaction passes | context_compressor.py | 670, 691, 877 |
| hermes budget | context_compressor.py | 737, 814 |
| hermes cooldown | context_compressor.py | 730–735 |
| opencode SUMMARY_TEMPLATE | compaction.ts | 43–78 |
| opencode buildPrompt | compaction.ts | 124–135 |
| opencode completedCompactions | compaction.ts | 106–122 |
| opencode marker filter | compaction.ts | 391–395 |
| opencode plugin hook | compaction.ts | 399–405 |
| opencode message convert | compaction.ts | 408–411 |
| opencode dedicated agent | compaction.ts | 385–388 |
| opencode tool output cap | compaction.ts | 38 |
| opencode dedup test | compaction.test.ts | 1706 |
