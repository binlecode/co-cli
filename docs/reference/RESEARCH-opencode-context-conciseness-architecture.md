# RESEARCH: OpenCode — Context & Reasoning Conciseness Architecture (single-system deep dive)

How OpenCode keeps the model's **working context bounded** and its **output/reasoning concise** — traced end-to-end through the live agent loop: per-model system-prompt routing, request assembly, the streaming step loop, the token-budget trigger, the prune→select→summarize→replay compaction pipeline, and the reasoning lifecycle. Records the design and processing logic with `file:line` anchors throughout; does not prescribe co-cli direction.

**Out of scope:** long-term/declarative memory, model routing/provider transforms, approvals/sandboxing, MCP plumbing, the TUI render path (except where it consumes reasoning parts).

Verified at OpenCode HEAD `3e523d506` (`fix(tui): match @ mention items by name…`), pulled 2026-06-14, repo `~/workspace_genai/opencode` (origin `anomalyco/opencode`). Method: direct source scans of `packages/opencode/src/session/` (the live v1 loop) and `packages/core/src/session/` (the Effect-based v2 rewrite). Every path/line was opened and read; line numbers are at this HEAD and will drift.

Sibling docs (don't duplicate — they cover the peer-comparison axis; this doc is the OpenCode-internal mechanics):
- [RESEARCH-context-management-peer-survey.md](RESEARCH-context-management-peer-survey.md) — co vs hermes/openclaw/opencode/codex compaction comparison (the matrix row for opencode is the 30-second version of this doc).
- [RESEARCH-summarization-prompting-peer-survey.md](RESEARCH-summarization-prompting-peer-survey.md) — cross-peer summary-template comparison.

---

## 0. Thesis

OpenCode treats **"concise"** and **"focused"** as two unrelated problems solved by two unrelated subsystems, and that separation is the whole design:

- **Concise output/reasoning** is a *prompt-engineering* problem. It is solved entirely at request-build time by routing each model family to a hand-tuned, model-specific system prompt. There is no algorithm — `provider()` is a `switch` on the model-id string (`system.ts:25-39`).
- **Focused (bounded) context** is a *token-budget state-machine* problem. It is solved by a five-stage ladder that runs per LLM-request: a streaming-halt signal, an overflow predicate with a reserved buffer, in-place tool-output pruning, tail selection within a token budget, and an LLM summarization that replaces the dropped middle — then optionally replays the user's last message so the model continues seamlessly.

Conflating the two is the usual mistake. OpenCode keeps them in different files that never import each other's logic. The result a user sees — a terse `Thought:` block, near-empty assistant text, a context meter that stays low — is the *sum* of these two independent systems, not one feature.

---

## 1. The two-tier codebase: which compaction is actually live

OpenCode is mid-migration from a v1 implementation to an Effect-based v2 "core" rewrite. **Both files named `compaction.ts` exist and both are wired in** — this is the single most confusing thing about the repo and must be stated up front:

| Concern | Owner at HEAD | File |
|---|---|---|
| Live orchestration (overflow check, `create`, `process`, `prune`, tail `select`, replay) | **v1** | `packages/opencode/src/session/compaction.ts` (21k) |
| The **summary prompt template** + `buildPrompt()` | **v2 core** | `packages/core/src/session/compaction.ts` (247 lines) |
| The driving step loop | **v1** | `packages/opencode/src/session/prompt.ts` (68k) |
| Per-step streaming + event handling | **v1** | `packages/opencode/src/session/processor.ts` (43k) |
| System-context epoch versioning (baseline prompt as immutable revisions) | **v2 core** | `packages/core/src/session/context-epoch.ts` |
| Self-contained v2 compaction (`make()` → `compactIfNeeded`/`compactAfterOverflow`) | **v2 core** (not on the live loop's hot path; used by the v2 surface) | `packages/core/src/session/compaction.ts:175-246` |

Concretely, the live loop imports orchestration from v1 but pulls the template from v2 core:

```ts
// packages/opencode/src/session/prompt.ts
import { SessionCompaction } from "./compaction"                       // :15  v1 orchestration
// packages/opencode/src/session/compaction.ts
import { buildPrompt } from "@opencode-ai/core/session/compaction"     // :27  v2 template
```

So when this doc says "compaction," the **control flow** lives in `packages/opencode/src/session/compaction.ts` and the **prompt text** lives in `packages/core/src/session/compaction.ts`. The standalone v2 `make()` factory (`core/.../compaction.ts:175`) is a parallel, cleaner re-implementation of the same idea (it has its own `select`/`serialize` and inlines the template) used by the v2 server surface; it is documented in §7 because it carries the canonical template, but it is **not** what the TUI's main session drives today.

The peer-survey sibling cites `packages/core/src/session/compaction.ts:14/:48`; those anchors are the v2 core file. This doc anchors the *live* path in the v1 files and the *template* in v2 core — both are correct, they just describe different tiers.

---

## 2. Layer 1 — Conciseness is a per-model system prompt (not a global rule)

### 2.1 The router

`SystemPrompt.provider(model)` selects exactly one base prompt by substring-matching the model API id (`packages/opencode/src/session/system.ts:25-39`):

```ts
export function provider(model: Provider.Model) {
  if (model.api.id.includes("gpt-4") || model.api.id.includes("o1") || model.api.id.includes("o3"))
    return [PROMPT_BEAST]
  if (model.api.id.includes("gpt")) {
    if (model.api.id.includes("codex")) return [PROMPT_CODEX]
    return [PROMPT_GPT]
  }
  if (model.api.id.includes("gemini-")) return [PROMPT_GEMINI]
  if (model.api.id.includes("claude")) return [PROMPT_ANTHROPIC]
  if (model.api.id.toLowerCase().includes("trinity")) return [PROMPT_TRINITY]
  if (model.api.id.toLowerCase().includes("kimi")) return [PROMPT_KIMI]
  return [PROMPT_DEFAULT]
}
```

Eight prompt files ship under `packages/opencode/src/session/prompt/` (sizes at HEAD): `anthropic.txt` (105), `beast.txt` (147), `gpt.txt` (107), `codex.txt` (79), `copilot-gpt-5.txt` (143), `gemini.txt` (155), `kimi.txt` (95), `trinity.txt` (97), `default.txt` (95). Conciseness is *calibrated per family*; the same instruction lands differently across models, so OpenCode does not try to write it once.

### 2.2 Where the terseness comes from — `gemini.txt`

The behavior shown in the motivating example (Gemini emitting short `Thought:` blocks and near-empty assistant text) is dictated almost verbatim by `prompt/gemini.txt` (this is essentially Google's Gemini-CLI prompt). The operative section, **Tone and Style (CLI Interaction)** (`gemini.txt:39-46`):

- **Concise & Direct** — "professional, direct, and concise tone suitable for a CLI environment."
- **Minimal Output** — "Aim for fewer than 3 lines of text output (excluding tool use/code generation) per response whenever practical. Focus strictly on the user's query."
- **No Chitchat** — "Avoid conversational filler, preambles ('Okay, I will now…'), or postambles ('I have finished the changes…'). Get straight to the action or answer."
- **Tools vs. Text** — "Use tools for actions, text output *only* for communication."

It is reinforced by few-shot examples that *demonstrate output length* rather than describe it (`gemini.txt:65-78`):

```
user: 1 + 2
model: 3

user: is 13 a prime number?
model: true

user: list files here.
model: [tool_call: ls for path '/path/to/project']
```

And a closing **Final Reminder** (`gemini.txt:154-155`) that re-asserts "Balance extreme conciseness with the crucial need for clarity… you are an agent — please keep going until the user's query is completely resolved."

### 2.3 Contrast — `anthropic.txt`

The Claude prompt (`prompt/anthropic.txt`) reaches conciseness by a *different* route. It has no hard line-count cap; instead it states "responses should be short and concise… rendered in monospace" (`:16`), forbids using tools/Bash/comments as a communication channel (`:17`), forbids unsolicited file creation (`:18`), and leans heavily on **TodoWrite** for task decomposition/focus (`:23-27`, with worked examples at `:31-67`). It also adds a "Professional objectivity" section (`:20-21`) that suppresses validation/superlatives — a different lever on verbosity than Gemini's "<3 lines."

> **Design takeaway.** Per-model prompts exist because the *mechanism* of brevity differs: Gemini responds to an explicit length cap + few-shot length demonstrations; Claude is steered toward brevity-via-task-structure and anti-sycophancy. A single global "be concise" string would underperform on both.

---

## 3. Layer 2 — Request assembly, reminders, and message conversion

### 3.1 The assembled payload

Each request builds its system block and message array via a 4-way parallel gather (`prompt.ts:1327-1333`):

```ts
const [skills, env, instructions, modelMsgs] = yield* Effect.all([
  sys.skills(agent),                              // skill catalog (verbose form)
  sys.environment(model),                         // <env> block
  instruction.system().pipe(Effect.orDie),        // AGENTS.md / CLAUDE.md project rules
  MessageV2.toModelMessagesEffect(msgs, model),    // conversation history → model messages
])
const system = [...env, ...instructions, ...(skills ? [skills] : [])]
```

The `<env>` block (`system.ts:55-92`) is deliberately tiny — model id, cwd, worktree root, git-repo bool, platform, date, and an optional `<available_references>` list. It is the per-request "where am I" anchor and is rebuilt every request rather than carried in history.

Note the ordering: `[...env, ...instructions, ...skills]` is the **system** array; the model-family base prompt (§2) is prepended by the caller of `provider()`. Skills are presented *verbose* in the system block and *terse* in tool descriptions — an explicit, commented decision (`system.ts:99-104`): "the agents seem to ingest the information about skills a bit better if we present a more verbose version of them here and a less verbose version in tool description, rather than vice versa."

### 3.2 Reminders injected into the *last user message* (not the system prompt)

`SessionReminders.apply` (`reminders.ts:15-90`) appends `synthetic: true` text parts onto the most recent user message rather than polluting the system prompt:

- **plan agent** → append `PROMPT_PLAN` (`reminders.ts:28-36`).
- **plan→build switch** → if any prior assistant message was on the `plan` agent and the current agent is `build`, append `BUILD_SWITCH` (`:37-47`); when an experimental plan-mode plan file exists, the reminder is enriched with the plan path so the model executes it (`:60-66`).
- **experimental plan mode** → inject `PLAN_MODE` with `${planInfo}` substituted to point at the plan file (`:76-88`).

Putting steering text adjacent to the user's actual ask (recency) keeps the model's attention on the current intent rather than on stale system boilerplate. The `synthetic` flag marks these parts so they are visible to the model but distinguishable in the store/UI.

### 3.3 `toModelMessagesEffect` — where history is rendered (and trimmed)

`MessageV2.toModelMessagesEffect(msgs, model, options?)` (`message-v2.ts:142-145`) is the single conversion point from stored parts to provider messages. Options that bear on conciseness:

```ts
options?: { stripMedia?: boolean; toolOutputMaxChars?: number }
```

Behaviors:
- **Tool output** (`message-v2.ts:303-307`): a completed tool part renders as `"[Old tool result content cleared]"` if it was pruned (`part.state.time.compacted` set), else `truncateToolOutput(output, toolOutputMaxChars)`. With no cap passed (the normal live request), output is full; the cap is only applied on the compaction path (§6).
- **Media** (`:224`, `:307-316`): when `stripMedia` is set, media attachments are dropped; for providers that can't carry media in tool results, media is *extracted* into a separate trailing user message (`:309-316`, `:391-396`) rather than discarded silently.
- **Dangling tool calls** (`:360-371`): any `pending`/`running` tool part is emitted as an `output-error` with `"[Tool execution was interrupted]"` so the Anthropic invariant "every `tool_use` has a `tool_result`" is never violated on replay.
- **Empty-text structural separator** (`:273-296`): Anthropic adaptive-thinking turns can produce an empty `text` part between signed reasoning blocks. Dropping it shifts signed-thinking positions and the API rejects an empty string, so the code substitutes a single space (`text === "" && hasSignedReasoning ? " " : part.text`). This is a load-bearing comment — it documents a replay hazard, not an optimization.

---

## 4. Layer 3 — The streaming step loop

### 4.1 The driver loop (`prompt.ts`)

The loop is a `while (true)` over "steps," where one step = one model stream + the tool calls it issues (`prompt.ts:1138-1185`):

```ts
let step = 0
while (true) {
  yield* Effect.logInfo("loop", { "session.id": sessionID, step })
  let msgs = yield* MessageV2.filterCompactedEffect(sessionID).pipe(...)   // :1145  drop compacted msgs
  const { user: lastUser, assistant: lastAssistant, finished: lastFinished, tasks } = MessageV2.latest(msgs)  // :1149
  ...
  step++                                                                   // :1185
  ...
}
```

Key per-iteration facts:
- **`filterCompactedEffect` (`:1145`)** prunes everything a prior compaction replaced *before* anything else runs, so the loop never re-reads dropped history.
- **Max steps (`:1231-1232`)**: `const maxSteps = agent.steps ?? Infinity; const isLastStep = step >= maxSteps`. On the last step the request appends a synthetic final assistant directive (`:1343`): `...(isLastStep ? [{ role: "assistant", content: MAX_STEPS }] : [])`. `MAX_STEPS` is `prompt/max-steps.txt`, which disables tools and forces a text-only wrap-up summary ("Tools are disabled until next user input. Respond with text only… MUST provide a text response summarizing work done so far… List any remaining tasks… Recommendations for next."). This bounds runaway tool-call chains.

### 4.2 The per-step processor (`processor.ts`)

`SessionProcessor.process(streamInput)` runs one step and returns `"compact" | "stop" | "continue"`:

- It resets per-step scratch (`ctx.currentText`, `ctx.reasoningMap = {}`) and sets status `busy` (around `processor.ts:965-976`).
- It consumes the LLM stream and is **interruptible mid-flight** (`processor.ts:974-978`):

```ts
yield* stream.pipe(
  Stream.tap((event) => handleEvent(event)),
  Stream.takeUntil(() => ctx.needsCompaction),   // bail the instant we overflow
  Stream.runDrain,
)
```

- The return-value mapping (around `:1030`): `if (ctx.needsCompaction) return "compact"; if (ctx.blocked || error) return "stop"; return "continue"`.

This means overflow is a **control-flow signal**, not a post-hoc cleanup: the step aborts streaming and the driver loop reacts.

### 4.3 Event handling: text, reasoning, tools

`handleEvent` switches on the AI-SDK stream events:
- **reasoning** (`processor.ts:373-425`): `reasoning-start` allocates a `reasoning` part into `ctx.reasoningMap[id]`; `reasoning-delta` appends text (orphan deltas with unknown id are silently dropped); `reasoning-end` flushes. Reasoning is persisted as a first-class part, not merged into text.
- **tool-result** (`processor.ts:549-645`): reads the matching tool call, normalizes attachments (images via `image.normalize`, else passthrough), and completes the tool call. **No truncation happens here** — full output is stored; capping is a compaction-time concern (§6).
- **step-finish** (`processor.ts:750-755`): updates token usage and flips the overflow flag:

```ts
case "step-finish":
  if (!ctx.assistantMessage.summary &&
      isOverflow({ cfg: yield* config.get(), tokens: usage.tokens, model: ctx.model }))
    ctx.needsCompaction = true
```

The `!summary` guard prevents the summarizer's own step from recursively triggering compaction.

---

## 5. Layer 4 — The overflow predicate and the reserved buffer

`packages/opencode/src/session/overflow.ts` defines the budget. The key idea is OpenCode **never lets the window fill** — it reserves headroom so a summary can be written *before* the model is starved.

```ts
const COMPACTION_BUFFER = 20_000                                   // overflow.ts:8

export function usable({ cfg, model, outputTokenMax }) {          // :10-20
  const context = model.limit.context
  if (context === 0) return 0
  const reserved = cfg.compaction?.reserved
    ?? Math.min(COMPACTION_BUFFER, ProviderTransform.maxOutputTokens(model, outputTokenMax))
  return model.limit.input
    ? Math.max(0, model.limit.input - reserved)
    : Math.max(0, context - ProviderTransform.maxOutputTokens(model, outputTokenMax))
}

export function isOverflow({ cfg, tokens, model, outputTokenMax }) {  // :22-34
  if (cfg.compaction?.auto === false) return false
  if (model.limit.context === 0) return false
  const count = tokens.total
    || tokens.input + tokens.output + tokens.cache.read + tokens.cache.write
  return count >= usable({ cfg, model, outputTokenMax })
}
```

Notes:
- `usable` prefers the provider's declared **input** limit minus the reserved buffer; if no separate input limit is published, it derives from total context minus the model's max output.
- The reserve defaults to `min(20_000, maxOutputTokens)` — i.e. never reserve more than the model can actually emit.
- `count` includes cache read/write tokens, so prompt-cache reuse still counts against the budget (cached tokens still occupy the window).
- Auto-compaction is opt-out via `cfg.compaction.auto === false`.

The driver loop calls this on the *previous* finished assistant message before building the next request (`prompt.ts:1214-1221`):

```ts
if (lastFinished && lastFinished.summary !== true &&
    (yield* compaction.isOverflow({ tokens: lastFinished.tokens, model }))) {
  yield* compaction.create({ sessionID, agent: lastUser.agent, model: lastUser.model, auto: true })
  continue   // re-enter loop; the compaction task is now pending
}
```

`compaction.create` enqueues a compaction **task**; the next loop iteration pops it (`prompt.ts:1195-1212`) and runs `compaction.process(...)`. So overflow detected at `step-finish` (mid-stream, §4.2) *and* overflow detected pre-request (here) both funnel into the same task queue.

---

## 6. Layer 5 — The compaction pipeline (live v1 path)

All anchors in this section are `packages/opencode/src/session/compaction.ts` unless noted. The pipeline has four phases: **prune → select → summarize → replay**.

### 6.1 Constants

```ts
export const PRUNE_MINIMUM = 20_000       // :38  min savings to bother pruning
export const PRUNE_PROTECT  = 40_000      // :39  most-recent tool-output tokens kept untouched
const TOOL_OUTPUT_MAX_CHARS = 2_000       // :40  per-tool cap when rendering for the summary
const PRUNE_PROTECTED_TOOLS = ["skill"]   // :41  never prune skill outputs
const DEFAULT_TAIL_TURNS = 2              // :42  verbatim recent turns
const MIN_PRESERVE_RECENT_TOKENS = 2_000  // :43
const MAX_PRESERVE_RECENT_TOKENS = 8_000  // :44
```

### 6.2 Phase 1 — `prune`: hide old tool outputs in place (cheap, no LLM)

`prune` (`:253-297`) walks messages newest→oldest and marks stale tool outputs as compacted (it does **not** delete them):

1. Skip the most recent 2 user turns (`if (turns < 2) continue`).
2. Stop at a compaction boundary (`if (assistant && summary) break`).
3. Skip protected tools (`skill`).
4. Accumulate tool-output token estimates; the first `PRUNE_PROTECT` (40k) tokens of recent output are *protected* (`if (total <= PRUNE_PROTECT) continue`).
5. Only if the prunable remainder exceeds `PRUNE_MINIMUM` (20k) does it act — setting `part.state.time.compacted = Date.now()` on each (`:288-291`).

At render time those parts become the literal `"[Old tool result content cleared]"` (`message-v2.ts:304-305`). The metadata (tool name, input) survives; only the bulky output text is elided. This is **lossy** (no spill-to-disk recovery, unlike co's L1) but reversible only in that the original is still in the store.

### 6.3 Phase 2 — `select`: choose the verbatim tail within a token budget

`preserveRecentBudget` (`:90-95`) computes the tail budget:

```ts
cfg.compaction?.preserve_recent_tokens
  ?? min(MAX_PRESERVE_RECENT_TOKENS, max(MIN_PRESERVE_RECENT_TOKENS, floor(usable(input) * 0.25)))
```

i.e. **25% of usable context, clamped to [2k, 8k]**. `select` (`:198-241`) then:
1. Identifies "turns" excluding prior compactions.
2. Always keeps the last `tail_turns` (default 2; `cfg.compaction?.tail_turns`).
3. Walks backward accumulating turn token-estimates until the budget is exhausted.
4. If a single turn overshoots the remaining budget, it is *split* so part of it lands in the tail.

### 6.4 Phase 3 — `process`: render the head, build the prompt, summarize

`process` (around `:344-421`):
1. `completedCompactions(history)` (`:72`) finds prior compactions; their `userIndex`/`assistantIndex` go into a `hidden` set so **prior compacted turns are filtered out** — summaries never stack (`:347-351`).
2. The retained head is rendered with **both** conciseness knobs (`:362`, `:373`): `toModelMessagesEffect(msgs, model, { stripMedia: true, toolOutputMaxChars: TOOL_OUTPUT_MAX_CHARS })` — media stripped, every tool output capped to 2,000 chars.
3. The summarization prompt is `compacting.prompt ?? buildPrompt({ previousSummary, context })` (`:358`) — plugins may override; otherwise the v2-core `buildPrompt` (§7).
4. The summarizer runs as a normal processor step over `[...renderedHead, { role: "user", content: nextPrompt }]`, carrying the prior summary forward so the new summary *updates* rather than restarts.

### 6.5 Phase 4 — `replay`: continue seamlessly after an auto-compaction

When `auto === true` and summarization succeeds, the original triggering user message is **replayed** so the model resumes its task without the user re-typing (`:314-333` detect the replay candidate; `:445-473` perform it):

```ts
if (replay) {
  const original = replay.info
  const replayMsg = yield* session.updateMessage({ ...original ... })
  for (const part of replay.parts) {
    if (part.type === "compaction") continue
    const replayPart =
      part.type === "file" && MessageV2.isMedia(part.mime)
        ? { type: "text", text: `[Attached ${part.mime}: ${part.filename ?? "file"}]` }  // media → text note
        : part
    yield* session.updatePart({ ...replayPart, messageID: replayMsg.id })
  }
}
```

Media parts in the replayed message are downgraded to text descriptions (a re-attached image would re-bloat the fresh context). If there is no replay candidate, the loop simply auto-continues with a fresh turn.

---

## 7. The summary template (v2 core, the canonical text)

`buildPrompt` and `SUMMARY_TEMPLATE` live in `packages/core/src/session/compaction.ts`. `buildPrompt` (`:166-173`) chooses an *update* vs *create* preamble and concatenates the template + the rendered context:

```ts
export const buildPrompt = (input) => [
  input.previousSummary
    ? `Update the anchored summary below using the conversation history above.\nPreserve still-true details, remove stale details, and merge in the new facts.\n<previous-summary>\n${input.previousSummary}\n</previous-summary>`
    : "Create a new anchored summary from the conversation history.",
  SUMMARY_TEMPLATE,
  ...input.context,
].join("\n\n")
```

`SUMMARY_TEMPLATE` (`:16-51`) is a fixed-section Markdown skeleton with strict output rules — reproduced verbatim because the *section list* and the *rules* are the actual conciseness contract:

```
Output exactly the Markdown structure shown inside <template> and keep the section order unchanged.
Do not include the <template> tags in your response.

## Goal                       — single-sentence task summary
## Constraints & Preferences  — user constraints/prefs/specs or "(none)"
## Progress
### Done / ### In Progress / ### Blocked
## Key Decisions              — decision and why
## Next Steps                 — ordered next actions
## Critical Context           — technical facts, errors, open questions
## Relevant Files             — path: why it matters

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, commands, error strings, and identifiers when known.
- Do not mention the summary process or that context was compacted.
```

Design points worth lifting:
- **Fixed schema with "keep every section even when empty"** makes the summary diffable and makes an *update* pass (carry-forward) deterministic — the model edits sections rather than rewriting freeform prose.
- **"terse bullets, not prose"** is a conciseness rule applied to the *summary itself*, so compaction does not reintroduce verbosity.
- **"Preserve exact paths/commands/error strings/identifiers"** is the anti-lossy guard — the one class of tokens that must survive verbatim.
- **"Do not mention that context was compacted"** keeps the seam invisible to the downstream reasoning.

### 7.1 v2 core's self-contained variant (`make()`)

`core/.../compaction.ts:175-246` is a cleaner re-implementation used by the v2 surface. It is worth noting because it shows where the design is heading:
- `select(entries, tokens)` (`:133-164`) does a **char-accurate head/recent split**: it walks backward summing `Token.estimate` per serialized message, and when one message straddles the budget it slices it (`remaining = max(0, tokens - total) * 4`, i.e. ~4 chars/token) so the boundary lands mid-message rather than dropping a whole turn.
- `serialize` (`:91-117`) flattens each message to a labeled line form (`[User]:`, `[Assistant]:`, `[Assistant reasoning]:`, `[Assistant tool call]:`, `[Tool result]:` …) and truncates tool/shell output via `truncate` (`:81-82`, `TOOL_OUTPUT_MAX_CHARS=2000`, appends `\n[truncated]`).
- `compactIfNeeded` (`:230-241`) gates on `auto`, then checks `estimate({system, messages, tools}) <= context - max(output, buffer)` (`DEFAULT_BUFFER=20_000`, `DEFAULT_KEEP_TOKENS=8_000`) — the same reserve-headroom philosophy as `overflow.ts`.
- `compactAfterOverflow` (`:177-229`) emits `Compaction.Started`/`Ended` events, streams the summarizer with `tools: []` and `maxTokens = min(output, SUMMARY_OUTPUT_TOKENS=4096)`, and bails (`return false`) if the summary prompt itself would not fit (`Token.estimate(summaryPrompt) > context - summaryOutput`) — a degrade-safely guard.

---

## 8. The reasoning lifecycle (why thinking doesn't bloat context)

Reasoning is the second thing the motivating example touches. OpenCode handles it as a first-class part with a careful replay policy in `toModelMessagesEffect` (`message-v2.ts:373-387`):

```ts
if (part.type === "reasoning") {
  if (differentModel) {                                // session model changed since this part was produced
    if (part.text.trim().length > 0)
      assistantMessage.parts.push({ type: "text", text: part.text })   // demote CoT to plain text
    continue                                           // empty reasoning is dropped entirely
  }
  assistantMessage.parts.push({ type: "reasoning", text: part.text, providerMetadata: part.metadata })
}
```

Rules:
- **Same model** → reasoning replayed as native `reasoning` parts *with provider metadata* (this preserves Anthropic's signed-thinking blocks, which the API validates — see the empty-separator handling at `:273-296`).
- **Different model** (model switched mid-session) → non-empty reasoning is **demoted to plain text** and empty reasoning is **discarded**, because another model's chain-of-thought has no valid signed form and is mostly dead weight.
- During capture (`processor.ts:373-425`) reasoning streams into `ctx.reasoningMap` and is flushed to the store, but it is **never re-summarized inline** — when context pressure hits, reasoning is just part of the dropped middle that the §6 summary replaces.

So the terse `Thought:` block in a TUI is the provider's own *thought-summary* feature (e.g. Gemini), rendered by the UI from the `reasoning` part; OpenCode keeps it short via the prompt (§2) and keeps it from accumulating via the same-model/different-model demotion rule above.

---

## 9. End-to-end processing order (one request)

The exact sequence the live loop executes per iteration (`prompt.ts` ~`:1141`→`:1343`):

```
1. filterCompactedEffect(sessionID)              → drop everything prior compaction replaced            (:1145)
2. MessageV2.latest(msgs)                         → find lastUser / lastAssistant / lastFinished / tasks  (:1149)
3. if a compaction task is queued → process it, continue                                                 (:1195-1212)
4. if lastFinished overflows (isOverflow)         → create(auto) compaction task, continue               (:1214-1221)
5. step++ ; compute maxSteps / isLastStep                                                                (:1185, :1231-1232)
6. apply reminders (synthetic parts onto last user msg)                                                  (reminders.ts)
7. gather [skills, env, instructions, modelMsgs]  → toModelMessagesEffect(msgs, model)                    (:1327-1333)
8. build request; if isLastStep append MAX_STEPS assistant directive                                     (:1343)
9. processor.process(request):
     stream events → text / reasoning / tool calls
     on step-finish: if isOverflow → ctx.needsCompaction=true → Stream.takeUntil halts                   (processor.ts:750-755, :974-978)
     return "compact" | "stop" | "continue"                                                              (:~1030)
10. branch on result: compact → run pipeline (prune→select→summarize→replay); stop → break; continue → loop
```

The two overflow entry points (pre-request step 4, mid-stream step 9) both converge on the same compaction task, which runs the §6 pipeline, which calls the §7 template, after which `filterCompactedEffect` (step 1 next iteration) makes the dropped region invisible.

---

## 9.5 The context-epoch ↔ compaction seam (v2 only)

§1 noted that `context-epoch.ts` (v2 core) versions the *system context* while compaction (§6) windows the *message history*, and left their interaction as an open question. Traced fully, here is the seam. All anchors are under `packages/core/src/session/` and `packages/core/src/system-context/` unless noted.

### 9.5.1 Load-bearing fact: v1 has no seam

The live v1 loop (`packages/opencode/src/session/prompt.ts`) rebuilds the system prompt **statelessly every request** — `SystemPrompt.provider()` (§2) + `instruction.system()` are re-rendered each turn (`prompt.ts:1327-1333`), and compaction only rewrites *message* history (§6). There is no persistent system-context object to reconcile, so there is **nothing for compaction to interact with**. `context-epoch` is imported by exactly zero v1 files. The seam therefore exists in precisely one place — the v2 runner — and is explicit and bidirectional.

### 9.5.2 Shared substrate: one seq space, two lower bounds

Both subsystems carve the same monotonic message-`seq` space. The actual coupling is a single SQL `WHERE` in `SessionHistory.messageRows` (`history.ts:24-53`). Decoded with both bounds present (a `latestCompaction` row and an epoch `baselineSeq`):

```
include row ⟺
  (seq >= compaction.seq  OR  (type = "system" AND seq > baselineSeq))   -- compaction branch
  AND
  (type != "system"       OR  seq > baselineSeq)                          -- baseline branch
```

Which collapses to a crisp split:

| message kind | included iff | windowed by |
|---|---|---|
| non-system (user / assistant / tool / shell / synthetic) | `seq >= latestCompaction.seq` | **compaction** |
| system (context-update deltas) | `seq > baselineSeq` | **context-epoch** |

So compaction summarizes the *conversation* but is blind to system-context deltas; the epoch governs those independently. `load` (`history.ts:66-80`) reads both bounds concurrently; `entriesForRunner` (`:90-99`) is the runner's variant that also returns each row's `seq`.

### 9.5.3 SystemContext: a cache-stable baseline + cheap deltas

`SystemContext` (`system-context/index.ts`) composes typed **sources**, each a `Source<A>` with a `Key` (`:32-39`). The live sources are `core/instructions` (AGENTS.md/CLAUDE.md files, `instruction-context.ts:19`), `core/skill-guidance` (`skill/guidance.ts:60`), and `core/reference-guidance` (`reference/guidance.ts:53`); the runner `combine`s them (`runner/llm.ts:170-173`). Each source can render two things (`:36-37`): a full `baseline(current)` and a small `update(previous, current)` delta. Three transition functions consume them:

- **`initialize`** (`:194-211`) — observe all sources, render every baseline, `join("\n\n")` → the immutable **baseline string** + a durable **snapshot** (per-key comparison state). If any source loads `unavailable`, returns `InitializationBlocked` (`:198`) — it refuses to build a partial baseline.
- **`reconcile`** (`:214-276`) — re-observe and compare each source to the stored snapshot: all equal → `Unchanged`; some changed-but-compatible → `Updated{ text: render(deltas), snapshot }` (note: emits only `source.update` **deltas**, `:264-265`, not full baselines); incompatible value or a non-removable source vanished → falls through to `replace` (`:218-219, 235, 240`).
- **`replace`** (`:279-287`) — re-`initialize` a fresh full baseline, unless an admitted source is now unavailable → `ReplacementBlocked` (`:284-285`): wait rather than silently shrink the baseline.

### 9.5.4 The epoch state machine maps those onto the DB

`context-epoch.ts` persists one row per session in `session_context_epoch` (`sql.ts:167-176`: `baseline`, `snapshot`, `baseline_seq`, `replacement_seq`, `revision`, `agent`). `prepareOnce` (`:67-110`), invoked per turn by the runner (`runner/llm.ts:201-210`), branches:

- replacing agent **or** `replacement_seq != null` → `SystemContext.replace`; otherwise → `SystemContext.reconcile` (`context-epoch.ts:86-89`).
- `Unchanged` / `ReplacementBlocked` → `fence()` (verify revision unchanged), return the existing baseline (`:94-97`).
- `ReplacementReady` → `replace()` DB write: new `baseline` at `baseline_seq = replacement_seq` (or `latestSeq`), `replacement_seq` cleared, `revision + 1` (`:98-102, 241-278`) — a **full rebaseline**.
- `Updated` → publish `SessionEvent.ContextUpdated{ text: delta }`, committed via `advance()` (`:104-109, 323-343`): snapshot + `revision + 1`, **`baseline_seq` unchanged**. The delta becomes a *system message* sitting after the baseline.

The net effect between rebaselines: the model sees a fixed baseline string plus a growing tail of small system-message deltas — never a re-rendered full baseline.

### 9.5.5 The seam event: `Compaction.Ended → requestReplacement`

The bidirectional coupling is one projector handler (`projector.ts:438-446`):

```ts
events.project(SessionEvent.Compaction.Ended, (event) => {
  if (event.version === 1) return Effect.void                  // v1 compaction never touches the epoch
  const seq = event.seq
  if (seq === undefined) return Effect.die("…missing aggregate sequence")
  return Effect.gen(function* () {
    yield* run(db, event)                                       // insert the compaction (summary) message at `seq`
    yield* SessionContextEpoch.requestReplacement(db, event.data.sessionID, seq)   // schedule rebaseline at the same seq
  })
})
```

`requestReplacement` (`context-epoch.ts:159-176`) sets `replacement_seq = seq` (guarded so it only moves forward) and bumps `revision`. So a v2 compaction performs **two writes at the same `seq`**: it inserts the summary message (moving the non-system history floor) *and* arms an epoch rebaseline (which, on the next `prepare`, moves the system floor to the same point). The `version === 1` guard (`:439`) is what makes this v2-exclusive — legacy v1 compaction events are projected as plain messages with no epoch effect.

The same `requestReplacement` is fired by three other transitions that also justify a full re-render: `AgentSwitched` (`projector.ts:343`), `ModelSwitched` (`:357`), and replay-flagged `ContextUpdated` (`:415-419`). `SessionEvent.Moved` instead calls `SessionContextEpoch.reset` (`:258`) — delete the epoch row entirely (the session changed directory/workspace, so the whole context is invalid).

### 9.5.6 What happens to the two windows across one compaction

```
                       seq →
 before:   … sys_delta_a  sys_delta_b  user  asst  tool  … (overflow)
            └─ system floor = OLD baselineSeq ─┘
            └──────── non-system floor = OLD compaction.seq ────────┘

 v2 Compaction.Ended @ seq=N:
   • insert compaction(summary, recent) message  @ N
   • requestReplacement(N)  → replacement_seq = N

 next turn prepare(): replacement_seq != null → REPLACE branch
   • SystemContext.replace → fresh baseline, baseline_seq := N, replacement_seq := null

 after:     [compaction summary @ N]  user'  asst'  …
            non-system floor = latestCompaction.seq = N
            system floor      = baselineSeq          = N      ← aligned
```

The two independently-moving floors **converge at the compaction boundary**: every pre-`N` delta system message drops below the new baseline (folded into one freshly-rendered baseline string), and conversation history below the summary disappears behind `latestCompaction`. After compaction, baseline and history share a floor; they drift apart again only as new context deltas accrue.

### 9.5.7 Why it is shaped this way — prompt-cache prefix + free rebaseline

The runner pins a prompt-cache key and places the baseline in the stable `system` slot (`runner/llm.ts:218-227`):

```ts
const promptCacheKey = /^ses_[0-9a-f]{64}$/.test(session.id) ? session.id.slice(4) : session.id
const request = LLM.request({
  model,
  providerOptions: { openai: { promptCacheKey } },
  system: [agent.info?.system, system.baseline].filter(Boolean).map(SystemPart.make),
  messages: toLLMMessages(context, model),
  tools: toolMaterialization.definitions,
})
```

A stable `baseline` string ⇒ a stable cache prefix. Emitting context changes as **deltas appended after the prefix** preserves the cache; re-rendering the baseline would bust it. So rebaselining is deferred to the four moments that already break the prefix anyway — agent switch, model switch, replay-context-update, and **compaction**. Compaction is the cleanest of these: it already rewrites history and discards the cache, so collapsing accumulated system deltas into a fresh baseline *at the same seq* is essentially free, and it prevents delta system-messages from accumulating unboundedly across a long session.

### 9.5.8 Concurrency fence (revision / `current`)

Because preparation, compaction, agent/model switches, and (eventually) multiple nodes can race, every epoch mutation runs under optimistic concurrency: the `retryRevisionMismatch` loop (`context-epoch.ts:27-34`) re-attempts on a `RevisionMismatch` defect, and writes are conditioned on `revision = expectedRevision` (`advance`/`replace`, `:331-342, 264-273`). The runner closes the loop just before streaming (`runner/llm.ts:243-244`):

```ts
if (!(yield* SessionContextEpoch.current(db, session.id, agent.id, system.revision)))
  return yield* Effect.die(rebuildPreparedTurn())
```

If the epoch's `revision` (or selected agent) moved between `prepare` and the stream — e.g. a concurrent compaction armed a replacement — the prepared turn is thrown away and rebuilt from durable state rather than streaming a stale system prompt against a now-summarized history. This is what guarantees the seam stays consistent: the two floors can only be observed in an aligned state, never mid-update.

---

## 10. Design principles & synthesis

1. **Two orthogonal subsystems.** Conciseness = prompt engineering (`system.ts` + `prompt/*.txt`); focus = a token-budget state machine (`overflow.ts` + `compaction.ts` + the loop). They share no logic. This is the cleanest takeaway and the one most worth imitating.
2. **Per-model prompts beat one global prompt.** The brevity mechanism is model-specific (Gemini: hard line cap + few-shot length demos; Claude: task-decomposition + anti-sycophancy). Routing is a cheap `switch` on the model id.
3. **Reserve headroom before you need it.** `overflow.ts` keeps a ~20k buffer so a summary can always be written; you cannot summarize your way out of an already-full window. Cache tokens count against the budget.
4. **Overflow is a control-flow signal.** `Stream.takeUntil(() => ctx.needsCompaction)` aborts a step the instant it overflows; the step's return value (`compact`/`stop`/`continue`) drives the loop. No silent mid-turn bloat.
5. **A cheap tier before the expensive one.** `prune` (mark-old-tool-output, no LLM) runs before `select`+summarize (one LLM call). Pruning is gated on ≥20k savings to avoid churn; the most recent 40k of tool output and all `skill` outputs are protected.
6. **Nothing is destructively deleted.** Pruned/compacted content is flagged (`time.compacted`) and swapped for a placeholder at *render* time (`message-v2.ts:304-305`); media is downgraded to text notes on replay. The store stays a full audit log while the model's view shrinks.
7. **Fixed-schema, update-in-place summaries.** The §7 template's "keep every section even when empty" + carry-forward `<previous-summary>` makes repeated compactions converge (edit, not rewrite) and keeps the summary itself terse.
8. **Seamless continuation.** Auto-compaction replays the user's last message so the agent resumes without user intervention; the summary explicitly must not mention that compaction happened.

---

## 11. Constants quick-reference (at HEAD)

| Constant | Value | File:line | Role |
|---|---|---|---|
| `COMPACTION_BUFFER` / `DEFAULT_BUFFER` | 20,000 | `overflow.ts:8` / `core/…/compaction.ts:12` | reserved headroom below context limit |
| `PRUNE_MINIMUM` | 20,000 | `compaction.ts:38` | min token savings to bother pruning |
| `PRUNE_PROTECT` | 40,000 | `compaction.ts:39` | most-recent tool-output tokens never pruned |
| `TOOL_OUTPUT_MAX_CHARS` | 2,000 | `compaction.ts:40`, `core/…:14` | per-tool cap when rendering for the summary |
| `PRUNE_PROTECTED_TOOLS` | `["skill"]` | `compaction.ts:41` | tools exempt from pruning |
| `DEFAULT_TAIL_TURNS` | 2 | `compaction.ts:42` | verbatim recent turns kept |
| `MIN/MAX_PRESERVE_RECENT_TOKENS` | 2,000 / 8,000 | `compaction.ts:43-44` | clamp on the 25%-of-usable tail budget |
| preserve-recent fraction | 0.25 | `compaction.ts:93` | tail budget = 25% of usable, clamped |
| `DEFAULT_KEEP_TOKENS` | 8,000 | `core/…/compaction.ts:13` | v2 tail budget |
| `SUMMARY_OUTPUT_TOKENS` | 4,096 | `core/…/compaction.ts:15` | summarizer max output |

---

## 12. Open questions / unverified

- **Migration end-state.** v1 (`packages/opencode/src/session/`) is the live loop; v2 (`packages/core/src/session/`) is the Effect rewrite and already owns the summary template, context-epoch versioning, and the epoch↔compaction seam (§9.5). Which orchestration tier the TUI drives after the migration completes is not settled at this HEAD — but the two compaction designs differ materially (v1 prune→select→summarize→replay over messages only; v2 reconcile-based system-context rebaseline aligned with compaction), so the cutover is a behavior change, not a refactor.
- **~~`context-epoch.ts` interaction with compaction~~ — RESOLVED (§9.5).** Traced fully: v1 has no seam (stateless system prompt); in v2 a `Compaction.Ended` (version ≠ 1) event both inserts the summary message and arms `SessionContextEpoch.requestReplacement(seq)` (`projector.ts:438-446`), so the history floor (`latestCompaction.seq`) and the system-context floor (`baselineSeq`) converge at the compaction boundary. Open sub-question: whether the **delta-vs-rebaseline** cache-preservation strategy (§9.5.7) measurably improves prompt-cache hit rates in practice was not benchmarked — it is asserted from the code's intent, not measured.
- **`summary.ts` scope.** `packages/opencode/src/session/summary.ts` is a *session-title / git-diff* summarizer (`computeDiff`/`summarize`/`diff`, `:82-148`), not part of the context-compaction path despite the name. Confirmed read; flagged to prevent confusion with `compaction.ts`.
- **No spill/recovery tier.** Unlike co's content-addressed spill, OpenCode's tool-output reduction is lossy at render time (placeholder string). Whether a recovery tier is planned is unknown.
