# TODO: Memory Write Subagent

**Slug:** `memory-write-subagent`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research and peer source checked before drafting:

- [RESEARCH-tools-fork-cc.md](reference/RESEARCH-tools-fork-cc.md)
- local peer source: `../fork-claude-code/services/extractMemories/prompts.ts`
- local peer source: `../fork-claude-code/memdir/memdir.ts`
- local peer source: `../fork-claude-code/memdir/memoryTypes.ts`
- local peer source: `../fork-claude-code/tools/FileWriteTool/prompt.ts`
- local peer source: `../fork-claude-code/tools/FileEditTool/FileEditTool.ts`

Current-state validation against checked-in `co` source:

- `save_memory`, `update_memory`, and `append_memory` are three separate deferred write tools registered in `co_cli/agent.py`.
- `save_memory` routes through `persist_memory()` in `co_cli/memory/_lifecycle.py`, which runs a separate memory-save agent from `co_cli/memory/_save.py` to decide `SAVE_NEW` vs `UPDATE`.
- `update_memory` and `append_memory` in `co_cli/tools/memory.py` bypass that save agent and mutate files directly.
- Post-turn extraction in `co_cli/main.py` calls `fire_and_forget_extraction()` from `co_cli/memory/_extractor.py`; that path produces structured candidates and then calls `persist_memory()`.
- `co` already has generic file tools (`read_file`, `write_file`, `edit_file`, `list_directory`, `find_in_files`) plus a subagent framework in `co_cli/tools/subagent.py`.
- `make_subagent_deps()` already shares `resource_locks`, config, and workspace paths between parent and child runs, so a write-capable memory subagent can reuse the existing execution scaffold.
- `fork-claude-code` does not expose a `co`-style trio of dedicated memory mutation tools. Its memory write flow has two layers:
  - **Primary path:** the main agent's system prompt includes full memory-write instructions and the existing `MEMORY.md` index; the main agent writes memory files directly using unrestricted `Write`/`Edit` tools during its normal turn. This is the first-class write path.
  - **Background fallback:** a post-turn forked agent (`services/extractMemories/`) scans conversation history and writes memory files using the same generic file tools, but restricted to the memory directory. The fork is **extract + save in one** — no separate save agent follows it; writes happen inside the fork's own tool calls.
  - Mutual exclusion: `hasMemoryWritesSince()` detects if the main agent wrote memory during a turn; if so, the background fork skips that turn entirely and advances its cursor. Main agent writes and background extraction are never concurrent.
  - The extraction fork uses a cursor (`lastMemoryMessageUuid`) so each run processes only new messages since the previous extraction, not a fixed sliding window.

Artifact hygiene: clean. No existing TODO in `docs/` covers this replacement.

---

## Problem & Outcome

Problem: `co` currently splits memory mutation across four layers:

1. user-facing `save_memory`
2. user-facing `update_memory`
3. user-facing `append_memory`
4. background extractor + save-agent upsert pipeline

Those paths do not share one mutation engine. Today:

- create-vs-update is decided by a dedicated save agent in `_save.py`
- exact replace and append live in separate top-level tools
- background extraction is a second model pipeline that emits candidates and then hands off to the save lifecycle

This is structurally different from the checked `fork-claude-code` design, where memory writing is delegated to a purpose-built subagent with file tools and prompt instructions that tell it when to create, when to edit, and how to avoid duplicates.

A second problem specific to `co`: the main agent runs a local 35B model. At that scale, the model can recognize that something is worth saving but is unreliable at executing the full write task inline — correct frontmatter, type classification, and dedup against the manifest all degrade when the model must also compose a response. fork-cc's main agent is a frontier model that can handle direct file writes; `co`'s cannot. The subagent-backed `save_memory` tool compensates for this: the main agent only needs to express intent in natural language, and the subagent — running a focused prompt on a single task — handles the write mechanics. This also explains why background extraction is load-bearing in `co` in a way it is not in fork-cc: the main agent will not reliably self-initiate memory saves, so the post-turn extractor is a necessary safety net, not just an optimization.

Outcome:

- replace the current three dedicated memory mutation tools with one deferred `save_memory` tool
- make that subagent the single write engine for explicit memory saves and background memory extraction
- **adopt fork-claude-code's prompt design as the source template**, adapted to `co`'s file paths, tool names, and current storage shape
- perform memory mutation through the `memory` write-dispatcher tool inside the subagent, not through `persist_memory()`, `_memory_save_agent`, `update_memory()`, or `append_memory()`

---

## Scope

In scope:

- one new deferred public `save_memory` tool backed by a memory subagent
- `_save_memory_agent` module-level singleton with `memory` write-dispatcher tool — not a role in `_subagent_builders.py`
- one prompt bundle for that subagent based directly on the checked `fork-claude-code` memory prompts
- `memory`-tool-driven mutation inside `.co-cli/memory/` (confinement enforced inside the tool)
- replacement of explicit `save_memory`, `update_memory`, and `append_memory` registrations with the new subagent tool
- rewiring post-turn memory extraction to use the same memory subagent machinery
- removal of the dedicated memory save agent and the now-unused explicit mutation paths once all callers are migrated
- **`MEMORY.md` live index** — maintained by the save_memory subagent after every write; injected into the main agent's system prompt every session so the agent always has a complete inventory of what it has remembered
- tests covering explicit use, background extraction use, approval behavior, MEMORY.md maintenance, and migration cleanup

Out of scope:

- changing memory recall/search semantics (`search_memories`, `_recall_for_context`, `load_always_on_memories`)
- redesigning the frontmatter schema
- redesigning article storage
- changing `/memory list` or `/memory forget` slash-command UX except where tool removal requires prompt/discovery updates

Recommended v1 boundary:

- keep the current flat `.md` memory-file storage and current recall code
- **add `MEMORY.md`** as a session-start index injected into the main agent system prompt — this is load-bearing: without it the main agent has no complete picture of existing memories and cannot make informed save or update decisions
- save_memory subagent maintains `MEMORY.md` as a two-step write: update topic file + update index entry, identical to fork-cc's protocol

---

## Behavioral Constraints

- **Prompt-source constraint:** do not invent a fresh memory-management prompt. Start from the checked `fork-claude-code` prompt text and preserve its structure, section order, and operating model wherever `co` can support them.
- **Before writing any prompt text, read these source files in full:**
  - `~/workspace_genai/fork-claude-code/memdir/memoryTypes.ts` — `TYPES_SECTION_INDIVIDUAL`, `WHAT_NOT_TO_SAVE_SECTION`, `WHEN_TO_ACCESS_SECTION`, `TRUSTING_RECALL_SECTION`, `MEMORY_FRONTMATTER_EXAMPLE`
  - `~/workspace_genai/fork-claude-code/memdir/memdir.ts` — `buildMemoryLines()` (main agent memory section assembly)
  - `~/workspace_genai/fork-claude-code/services/extractMemories/prompts.ts` — `opener()`, `buildExtractAutoOnlyPrompt()` (extractor + save mechanics)
- Allowed prompt edits are limited to:
  - substituting `co` tool names for fork-cc tool names
  - substituting `co` memory paths for fork-cc memory paths
  - removing fork-cc-only features that `co` does not have in the same change
  - `MEMORY.md` instructions are carried verbatim — do not remove or rewrite them
- The new memory subagent must mutate memory through the `memory` write-dispatcher tool, not semantic helpers like `persist_memory()`, `overwrite_memory()`, `update_memory()`, or `append_memory()`, and not through generic `write_file`/`edit_file` tools.
- The subagent is confined to `.co-cli/memory/` via `_resolve_memory_path()` inside the `memory` tool. General workspace write tools are not registered.
- The top-level `save_memory` tool must require approval when called by the main agent. Child writes inside the save_memory subagent should not trigger a second user approval layer.
- The async extractor subagent is an LLM subagent that calls the `save_memory` tool with `approval=False` — same tool, same subagent, no approval gate. The approval flag is context-dependent: `True` for the main agent's tool bundle, `False` for the extractor subagent's tool bundle.
- Both the main agent and the extractor subagent call `save_memory` as a tool. The tool is model-visible in both contexts; only the approval behavior differs.
- Background extraction and explicit user-invoked memory writes share the same `save_memory` tool and subagent. No parallel implementations.
- If a small direct-write helper remains for `/new` session-summary artifacts, it must stay internal and out of the model tool surface.
- Remove the three dedicated memory mutation tools from prompt discovery, tool registry tests, and docs in the same delivery. Do not leave dead references.

---

## High-Level Design

### 1. Write-capable memory subagent

The write-capable memory subagent is the `_save_memory_agent` singleton in
`co_cli/memory/_save_agent.py`. It is **not** a role in `_subagent_builders.py` or dispatched
through `SUBAGENT_ROLES` — it follows the module-level singleton pattern of `_save.py` and
`_extractor.py`. Dispatch is `_run_save_memory_agent(ctx, instruction, max_requests)` in
`co_cli/tools/subagent.py`. Structured output: `SaveMemoryAgentOutput` (`summary`,
`files_touched`, `actions`, `confidence`). Budget: `max_requests_memory` in
`co_cli/config/_subagent.py` (default 6).

Public tool name: `save_memory` (registered in `co_cli/agent.py` — TASK-3).

### 2. Memory tools for the save_memory subagent

The save_memory subagent uses a dedicated `memory` write-dispatcher tool rather than generic
`write_file`/`edit_file` tools. Confinement lives inside the tool itself, not in a separate
predicate.

Tool bundle registered in `_save_memory_agent` (all `requires_approval=False`):

- `read_file` — read-only
- `list_directory` — read-only
- `find_in_files` — read-only
- `memory(ctx, action, path, ...)` — write-dispatcher; the only write surface

**`memory` tool actions** (all confined to `ctx.deps.memory_dir` via `_resolve_memory_path()`):

- `create` — atomic write (temp + `os.replace`); raises `FileExistsError` if path exists
- `edit` — `str_replace` with unique-match guard; raises `ValueError` on zero or ambiguous matches
- `append` — strips trailing whitespace, appends content; raises `FileNotFoundError` if missing
- `delete` — removes file; raises `FileNotFoundError` if missing

**Confinement**: `_resolve_memory_path(ctx, path)` resolves `(memory_dir / path).resolve()` and
raises `ValueError` before any I/O if the result is outside `memory_dir.resolve()`. No `can_use_tool`
predicate is needed.

**Prompt is the enforcement layer for file format.** The save_memory subagent prompt must explicitly specify:
- The required frontmatter fields (`name`, `description`, `type`) and their constraints
- The `type` enum values (`user`, `feedback`, `project`, `reference`) — invalid values must not be written
- The body structure for `feedback` and `project` types (`**Why:**` / `**How to apply:**` lines)
- The dedup rule: always `list_directory` + `read_file` candidate files before writing — never create a new file if an existing one covers the same topic
- The update rule: when updating, preserve the original `Why:` line unless the new information explicitly supersedes it

These are not enforceable by the tool layer. If the prompt omits them, the subagent will produce malformed or duplicate files regardless of which tools it uses.

### 3. Prompt spec — system prompt, messages, and task framing

There are three agents. Each has distinct context:

#### A. Extractor subagent (post-turn background LLM agent)

| Layer | Content |
|-------|---------|
| System prompt | Main agent's rendered system prompt — extractor inherits co's personality, memory rules, and what-to-save taxonomy without a separate prompt file |
| Messages | New messages since the cursor, formatted as alternating `User: …` / `Co: …` lines — cursor-sliced delta only, not full history |
| User prompt | Extraction task framing (see template below) |
| Tools | `save_memory` with `approval=False` — the only tool the extractor subagent needs |

The extractor subagent's job is **signal detection only**. It does not read or write memory files directly. When it detects a memory-worthy signal, it calls `save_memory(instruction=...)` which delegates to the save_memory subagent.

**Extractor user prompt template** — adapted from `fork-cc/services/extractMemories/prompts.ts` `opener()`:

```
You are now acting as the memory extraction subagent. Analyze the most recent ~{n} messages above.

For each memory-worthy signal you detect, call save_memory with a natural-language instruction describing what to save, e.g.:
  save_memory(“user prefers pytest over unittest for this project”)
  save_memory(“update feedback memory: do not summarize responses”)

You MUST only use content from the last ~{n} messages. Do not investigate source files or verify patterns beyond what the conversation contains.
```

Then carry verbatim from fork-cc (co-specific substitutions only):
- `TYPES_SECTION_INDIVIDUAL` — what each type is, when to save, examples
- `WHAT_NOT_TO_SAVE_SECTION` — exclusions, explicit-save gate

No manifest injection here — dedup is the save_memory subagent's responsibility.

#### B. Save_memory subagent (called by both main agent and extractor subagent)

| Layer | Content |
|-------|---------|
| System prompt | Main agent's rendered system prompt |
| Messages | None |
| User prompt | The natural-language instruction from `save_memory(instruction=...)` |
| Tools | `read_file`, `list_directory`, `find_in_files` (read-only), `memory` (write-dispatcher, confined to memory directory) |

The subagent receives a single instruction, reads existing memory files to check for duplicates, decides create-vs-update, and writes. The manifest is NOT pre-injected — the subagent discovers existing files via its file tools.

**Save_memory subagent prompt** — adapted from `fork-cc/services/extractMemories/prompts.ts` `buildExtractAutoOnlyPrompt()` (write-mechanics sections only):

```
You are the memory save subagent. You have received an instruction to save or update a memory.

Available tools: read_file, list_directory, find_in_files (read-only), memory (write-dispatcher) — only paths inside .co-cli/memory/ are permitted.

Efficient strategy: turn 1 — list_directory and read any candidate files in parallel; turn 2 — memory(action=create/edit/append/delete). Do not interleave reads and writes.
```

Then carry verbatim from fork-cc (co-specific substitutions only):
- `TYPES_SECTION_INDIVIDUAL` — **critical: must be present in the save_memory subagent user prompt even though the main agent system prompt already contains it.** The reason: `<body_structure>` tags are embedded *inside* each type definition, not in a separate section. They are the write-time format enforcement for `feedback` and `project` bodies. When the subagent is composing a file, it needs `<body_structure>` in scope at that moment — if it is only in the system prompt (read at session start, attention-diluted by write time), format compliance degrades. Co-locating the taxonomy with the write task keeps the format rules at the point of use. Specifically:
  - `feedback` `<body_structure>`: "Lead with the rule itself, then a `**Why:**` line (the reason the user gave) and a `**How to apply:**` line (when/where this guidance kicks in)."
  - `project` `<body_structure>`: "Lead with the fact or decision, then a `**Why:**` line (the motivation) and a `**How to apply:**` line (how this should shape your suggestions)."
  - `user` and `reference` have no `<body_structure>` — free-form body is acceptable for those types.
  - The `type` field in `MEMORY_FRONTMATTER_EXAMPLE` is rendered as `{{user, feedback, project, reference}}` — enum values baked into the example, not described separately. No valid type can be invented.
- `WHAT_NOT_TO_SAVE_SECTION` — admission gate; subagent must reject instructions that ask it to save excluded content
- `## How to save memories` — **`skipIndex=false` variant (with MEMORY.md step)**, carried verbatim from fork-cc:
  - **Step 1**: write the memory to its own topic file with required frontmatter (`name`, `description`, `type`)
  - **Step 2**: add or update a one-line pointer entry in `MEMORY.md`: `- [Title](file.md) — one-line hook`. Never write memory content directly into `MEMORY.md`
  - `MEMORY.md` is always loaded into the main agent's session context — keep entries concise (one line, ~150 chars); lines after 200 will be truncated
  - dedup rule: `list_directory` + read candidate files before any write; update an existing file rather than creating a duplicate
  - update rule: when editing an existing memory, preserve the original `**Why:**` line unless the new instruction explicitly supersedes it

**Co-specific adaptations for both prompts** (only these differ from fork-cc):
- tool names: `read_file`, `list_directory`, `find_in_files`, `memory` (replaces fork-cc's generic `Write`/`Edit` tools)
- memory path: `.co-cli/memory/`
- no team memory scope section

Non-goal: paraphrase or reinvent. Treat fork-cc's checked prompt text as the source artifact; diff against it to confirm only the substitutions above were made.

### 4. Replace the current explicit memory tool surface

Remove these top-level model tools:

- `save_memory`
- `update_memory`
- `append_memory`

Replace them with:

- `save_memory`

The model should now express the memory action in natural language through `save_memory(...)`, for example:

- remember this user preference
- update the existing memory about testing policy
- append this new detail to the project-deadline memory if it exists, otherwise create a new memory

The subagent decides whether to read, edit, or write based on the prompt and the files it inspects.

Approval model:

- main agent path: calls public `save_memory(...)` tool
- public `save_memory(...)` tool: deferred approval at tool-entry
- memory-writing child operations: no second approval prompt, because they are confined to `.co-cli/memory/`

### 5. Reuse the same engine for background extraction

The current `_extractor.py` does:

- analyze recent messages
- emit structured candidates
- call `persist_memory()`

Background extraction becomes a proper LLM subagent — not a Python coordinator. It runs the same `save_memory` tool as the main agent, just without the approval gate.

**Call paths:**

```
Main agent (approval=True):
  → save_memory tool → save_memory subagent

Extractor subagent (approval=False):
  → detects signals from message window
  → save_memory tool (approval=False) → save_memory subagent
```

Both paths hit the same subagent. The approval flag is the only difference.

**Cursor-based message slicing**: replace the current fixed 20-line window with a cursor (last processed message UUID or index). Each extraction run passes only messages since the previous cursor as the subagent's messages context. Advance the cursor only on successful completion so failed runs reprocess those messages on the next turn. If the cursor position is lost (e.g. context compaction), fall back to full history. This matches fork-cc's `lastMemoryMessageUuid` / `countModelVisibleMessagesSince` pattern.

**No manifest in extractor**: the extractor subagent does not receive the existing memory manifest — it only detects signals. Dedup is entirely the save_memory subagent's responsibility, handled via file tools on each save call.

**No mutual exclusion between main agent and extractor**: fork-cc suppresses background extraction when the main agent already wrote memory that turn (`hasMemoryWritesSince()`). co does not implement this. The reason: fork-cc's main agent is a frontier model — reliable enough that if it wrote, the extractor has nothing to add. co's main agent and extractor subagent both run small local models that are individually unreliable. Both may detect different memory-worthy signals from the same turn's messages, so running both paths on the same delta is intentional coverage, not redundancy. Dedup at the file level (memory subagent reads before writing) is the correct gate — not short-circuiting at the extractor.

### 6. Prompt content reference — fork-cc source → co adaptation

**Read these files before writing a single line of prompt text:**

```
~/workspace_genai/fork-claude-code/memdir/memoryTypes.ts        ← all TYPES/WHAT_NOT/WHEN/FRONTMATTER constants
~/workspace_genai/fork-claude-code/memdir/memdir.ts             ← buildMemoryLines() assembly
~/workspace_genai/fork-claude-code/services/extractMemories/prompts.ts  ← opener() + buildExtractAutoOnlyPrompt()
```

The table below maps every section to its co equivalent. Diff the rendered co prompt against the fork-cc source line by line. No structural invention is permitted — only the substitutions in the right column.

#### A. Main agent memory section — `buildMemoryLines()` structure

Fork-cc assembles this from constants in `memoryTypes.ts` plus inline strings. Co's equivalent lives in the system prompt builder (wherever the memory section is currently injected).

| Fork-cc section | Co adaptation |
|----------------|---------------|
| `# auto memory` header | `# memory` (or `# co memory` — match existing header convention) |
| `"You have a persistent, file-based memory system at \`{memoryDir}\`. This directory already exists — write to it directly with the Write tool (do not run mkdir or check for its existence)."` | Substitute path `.co-cli/memory/`. Replace "Write tool" with "`save_memory` tool" |
| `"You should build up this memory system over time so that future conversations can have a complete picture of who the user is, how they'd like to collaborate with you, what behaviors to avoid or repeat, and the context behind the work the user gives you."` | Carry verbatim |
| `"If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry."` | Carry verbatim |
| `TYPES_SECTION_INDIVIDUAL` — four-type XML taxonomy (`user`, `feedback`, `project`, `reference`) with `<description>`, `<when_to_save>`, `<how_to_use>`, `<body_structure>`, `<examples>` for each | Carry verbatim. Drop `<scope>` tags (those are from `TYPES_SECTION_COMBINED`, not `INDIVIDUAL`) |
| `WHAT_NOT_TO_SAVE_SECTION` — "Code patterns, conventions, architecture… Git history… Debugging solutions… Anything already in CLAUDE.md… Ephemeral task details…" + explicit-save gate paragraph | Carry verbatim |
| `## How to save memories` — `skipIndex=false` variant (with MEMORY.md): Step 1 write topic file, Step 2 add pointer to `MEMORY.md`; dedup rule; semantic organization | Carry verbatim. Substitute path `.co-cli/memory/MEMORY.md`. `MEMORY.md` is in scope for co. |
| `## MEMORY.md` content injection — `buildMemoryPrompt()` reads existing `MEMORY.md` and appends it to the system prompt under a `## MEMORY.md` section; truncated at 200 lines / 25KB | Implement equivalent: read `.co-cli/memory/MEMORY.md` at session start and inject into main agent system prompt. Show `"Your MEMORY.md is currently empty."` when file does not exist yet. |
| `WHEN_TO_ACCESS_SECTION` — "When memories seem relevant… MUST access when user asks… ignore means proceed as if MEMORY.md empty… staleness caveat" | Carry verbatim |
| `TRUSTING_RECALL_SECTION` — "## Before recommending from memory… check file exists… grep for function/flag… 'memory says X exists' ≠ 'X exists now'… snapshot caveat" | Carry verbatim |
| `## Memory and other forms of persistence` — plans vs memory, tasks vs memory | Carry verbatim |
| `buildSearchingPastContextSection()` — feature-flagged grep instructions | Omit (feature-flagged in fork-cc; not applicable to co v1) |

Frontmatter format (from `MEMORY_FRONTMATTER_EXAMPLE`):
```markdown
---
name: {{memory name}}
description: {{one-line description — used to decide relevance in future conversations, so be specific}}
type: {{user, feedback, project, reference}}
---

{{memory content — for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines}}
```

#### B. Extraction user prompt — `buildExtractAutoOnlyPrompt()` structure

This is the user prompt appended after the cursor-sliced message window. Build via `build_extraction_user_prompt(n: int, manifest: str) -> str`.

| Fork-cc section | Co adaptation |
|----------------|---------------|
| Opener line 1: `"You are now acting as the memory extraction subagent. Analyze the most recent ~{n} messages above and use them to update your persistent memory systems."` | Carry verbatim; substitute "memory systems" → "memories" if co uses singular |
| Opener line 2 (available tools): `"Available tools: Read, Grep, Glob, read-only Bash (ls/find/cat/stat/wc/head/tail and similar), and Edit/Write for paths inside the memory directory only."` | Replace entirely: `"Available tool: save_memory — call it with a natural-language instruction for each memory-worthy signal you detect."` (co extractor is signal-detection only; it does not write files directly) |
| Opener line 3 (two-phase advice): `"You have a limited turn budget… turn 1 — issue all Read calls in parallel… turn 2 — issue all Write/Edit calls in parallel."` | Omit — co extractor has no file tools and no two-phase strategy. Replace with scope constraint from line 4 only. |
| Opener line 4 (scope constraint): `"You MUST only use content from the last ~{n} messages to update your persistent memories. Do not waste any turns attempting to investigate or verify that content further — no grepping source files, no reading code to confirm a pattern exists, no git commands."` | Carry verbatim |
| Manifest block (when non-empty): `"## Existing memory files\n\n{manifest}\n\nCheck this list before writing — update an existing file rather than creating a duplicate."` | Carry verbatim |
| `"If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry."` | Carry verbatim |
| `TYPES_SECTION_INDIVIDUAL` | Same as main agent section — carry verbatim, no scope tags |
| `WHAT_NOT_TO_SAVE_SECTION` | Carry verbatim |
| `## How to save memories` — `skipIndex=true` variant | Extractor subagent detects signals only — it does not write files directly. Omit the how-to-save section entirely; the extractor calls `save_memory` and delegates writing to the save_memory subagent. |

### 7. Retire the dedicated save lifecycle once callers are gone

After explicit writes and background extraction both use the memory subagent:

- delete `_memory_save_agent` in `co_cli/memory/_save.py`
- remove upsert-routing logic from `co_cli/memory/_lifecycle.py` if no longer needed
- remove top-level `update_memory()` / `append_memory()` tool functions from `co_cli/tools/memory.py`

If `/new` still needs a direct internal file writer for `session_summary`, keep only the minimal helper needed for that internal path and rename it to reflect that narrower responsibility.

---

## Implementation Plan

### ✓ DONE — TASK-1: Add the memory subagent role and scoped file-tool bundle

Delivered in v0.7.78. See delivery summary and review verdict in the now-deleted `TODO-memory-refactor-task-1.md`.

prerequisites: []

### TASK-2: Implement the save-agent and extractor prompt content

files: `co_cli/memory/prompts/memory_save_agent.md` (fill stub), `co_cli/memory/prompts/memory_extractor.md` (replace), `tests/test_memory_subagent.py` (new)

TASK-1 delivered the `_save_memory_agent` singleton, `_run_save_memory_agent()` dispatch, and a stub `memory_save_agent.md`. TASK-2 fills in prompt content for both the save agent and the background extractor.

Implementation:

- **Save agent prompt** (`memory_save_agent.md`): replace the stub with content sourced from fork-cc's memory-write instructions. Before writing, read these source files in full:
  - `~/workspace_genai/fork-claude-code/memdir/memoryTypes.ts` — `TYPES_SECTION_INDIVIDUAL`, `WHAT_NOT_TO_SAVE_SECTION`, `WHEN_TO_ACCESS_SECTION`, `TRUSTING_RECALL_SECTION`, `MEMORY_FRONTMATTER_EXAMPLE`
  - `~/workspace_genai/fork-claude-code/memdir/memdir.ts` — `buildMemoryLines()`
  - `~/workspace_genai/fork-claude-code/services/extractMemories/prompts.ts` — `opener()`, Section 3B template
  The prompt must include: frontmatter schema example (name, description, type), type enum values with descriptions, feedback/project body structure (**Why:** / **How to apply:**), dedup rule (read before create, prefer edit/append over create), MEMORY.md two-step write protocol (update topic file then update MEMORY.md index pointer). Substitute only tool names (`memory`, `read_file`, `list_directory`, `find_in_files`) and path references; preserve section order and operating model from fork-cc.
- **Extractor prompt** (`memory_extractor.md`): replace with content sourced from fork-cc's `opener()` + `buildExtractAutoOnlyPrompt()`. Substitute only tool names and path references. Omit how-to-save and MEMORY.md write sections — the extractor detects candidates and delegates writes to `save_memory`, it does not write files itself.
- Implement `build_extraction_user_prompt(n: int, manifest: str) -> str` builder in `co_cli/memory/prompts/` sourced from the fork-cc Section 3A template.
- Implement `build_save_user_prompt(instruction: str) -> str` builder for the explicit save path (Section 3B template).
- Keep all prompt text in standalone `.md` files or builder functions — no inline f-strings at call sites.
- Confirm the diff between implemented prompts and fork-cc source shows only the allowed substitutions.

done_when: |
  `build_extraction_user_prompt(n)` renders a prompt that matches the Section 3A template;
  `build_save_user_prompt(instruction)` renders a prompt that matches the Section 3B template;
  tests assert for the save prompt: frontmatter example is present, all three required fields (name, description, type) are named, type enum values are listed, feedback/project body structure (**Why:** / **How to apply:**) is present, dedup rule is present, MEMORY.md two-step write is present;
  tests assert for the extractor prompt: types taxonomy present, what-not-to-save present, tool names are co names, no how-to-save section;
  `memory_save_agent.md` diff against fork-cc source shows only tool-name and path substitutions — no structural invention
success_signal: prompt content sourced from fork-cc with only co-specific substitutions; MEMORY.md two-step write protocol present in save agent prompt; extractor prompt confined to signal detection with no write instructions
prerequisites:
- TASK-1

### TASK-3: Replace explicit `save_memory` / `update_memory` / `append_memory` with subagent-backed `save_memory` and update main agent memory prompt

files: `co_cli/tools/memory.py`, `co_cli/agent.py`, `co_cli/context/` (system prompt builder), `tests/test_memory.py`, `tests/test_agent.py`, `tests/test_tool_registry.py`, `tests/test_tool_prompt_discovery.py`

Implementation:

- Remove `update_memory` and `append_memory` from registration.
- Replace the existing `save_memory` implementation/contract with: `save_memory(ctx, instruction: str, max_requests: int = 0) -> ToolReturn`.
- Keep read/list/search memory tools unchanged.
- Update tool discovery expectations, prompt discovery tests, and agent-registry tests.
- **Update the main agent's memory system prompt section** to follow fork-cc's `buildMemoryLines()` design (`memdir/memdir.ts`). Specifically:
  - Replace the current three-tool instruction surface with `save_memory` as the single write tool.
  - Adopt fork-cc's when-to-save guidance: save proactively during the turn when something memory-worthy is observed, not only when the user explicitly asks.
  - Adopt fork-cc's what-to-save / what-not-to-save rules (already defined in `memoryTypes.ts`) — these are the same rules injected into the extractor user prompt; having them in both places keeps the main agent and extractor aligned on save criteria.
  - Omit MEMORY.md index instructions (not applicable to co's flat-file layout).
  - Source from fork-cc's checked prompt text; substitute only tool name and path references.

This update is load-bearing: the extractor subagent inherits the main agent's system prompt, so the quality of the main agent's memory section directly determines the extractor's extraction criteria as well.

done_when: |
  `uv run pytest tests/test_memory.py tests/test_agent.py tests/test_tool_registry.py tests/test_tool_prompt_discovery.py -x` passes;
  the registered tool surface includes one subagent-backed `save_memory` tool and no `update_memory` or `append_memory`;
  the main agent system prompt memory section references `save_memory` only and includes when/what/what-not guidance
success_signal: a single memory-write tool appears in `/tools` as `save_memory`; `update_memory` / `append_memory` are gone; main agent prompt diff against fork-cc `buildMemoryLines()` shows only tool-name and path substitutions
prerequisites:
- TASK-1
- TASK-2

### TASK-4: Replace background extraction with an extractor subagent that calls save_memory

files: `co_cli/memory/_extractor.py`, `co_cli/main.py`, `co_cli/memory/_save.py`, `co_cli/memory/_lifecycle.py`, `tests/test_memory_lifecycle.py`, `tests/test_history.py`

Implementation:

- Replace `_extraction_agent` (current structured-output agent) with a proper LLM subagent built via the subagent framework. The extractor subagent:
  - receives the **main agent's system prompt** as its system prompt
  - receives the cursor-sliced message window as its messages context
  - receives the extraction task framing as its user prompt (see Section 3A template)
  - has `save_memory` in its tool bundle with **`approval=False`**
  - has no file tools — signal detection only; file writes are delegated to the save_memory subagent via `save_memory` calls
- Add a **cursor** (last processed message UUID or index) to `_extractor.py`. Each run slices only messages since the cursor. Advance only on success; fall back to full history if cursor is lost.
- Remove `_memory_save_agent` once no active path depends on it.
- Remove or shrink `persist_memory()` once explicit writes and background extraction no longer use it.
- Keep any internal-only direct-write helper needed for `/new` session summaries narrowly scoped and clearly named.
- Update history/tool-result tests that currently assume structured `ExtractionResult` candidates — the extractor subagent now emits `save_memory` tool calls instead.

done_when: |
  `uv run pytest tests/test_memory_lifecycle.py tests/test_history.py -x` passes;
  background extraction no longer imports or calls `_memory_save_agent` or `persist_memory`;
  extractor subagent tool bundle contains `save_memory` with `approval=False` and no file tools
success_signal: both main agent and extractor subagent call save_memory as a tool; approval=True for main agent, approval=False for extractor; one subagent handles both
prerequisites:
- TASK-1
- TASK-2
- TASK-3

### TASK-5: Cleanup, rename, and doc sync

files: `co_cli/memory/_save.py`, `co_cli/memory/_lifecycle.py`, `co_cli/tools/memory.py`, `docs/DESIGN-context.md`, `docs/DESIGN-tools.md`

Implementation:

- Delete dead code left behind by the removed three-tool design.
- Grep the repo for stale references to:
  - `save_memory`
  - `update_memory`
  - `append_memory`
  - `persist_memory`
  - `_memory_save_agent`
- Update DESIGN docs to describe the new single-tool memory write flow and the fork-cc-derived prompt/subagent model.

done_when: |
  `rg -n "save_memory|update_memory|append_memory|_memory_save_agent" .` only returns intentional historical references;
  `/sync-doc` updates DESIGN docs to match the shipped code
success_signal: no stale documentation or test assertions describe the removed three-tool design
prerequisites:
- TASK-4

---

## Testing

During implementation, scope to affected files and log pytest output:

```bash
mkdir -p .pytest-logs && uv run pytest \
  tests/test_subagent_tools.py \
  tests/test_memory.py \
  tests/test_memory_lifecycle.py \
  tests/test_agent.py \
  tests/test_tool_registry.py \
  tests/test_tool_prompt_discovery.py \
  tests/test_history.py \
  -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-memory-write-subagent.log
```

Before shipping:

```bash
scripts/quality-gate.sh full
```

---

## Open Questions

- Whether `/new` session-summary artifact writes should stay on a tiny internal helper or also go through the memory subagent. Recommended v1: keep them internal and out of the model tool surface.
- Whether to add a memory-dir-scoped delete tool now so the subagent can fully mirror fork-cc “forget” behavior. Recommended v1: only if the prompt keeps explicit forget instructions; otherwise leave `/memory forget` as the deletion path.
- Whether to retain `find_in_files` in the subagent bundle or rely only on manifest injection + `read_memory_file`. Recommended v1: include it only if tests show the manifest alone is not enough for reliable targeted updates.
- Whether explicit save and background extraction need divergent prompt instructions. fork-cc uses two prompt variants (`buildExtractAutoOnlyPrompt` vs `buildExtractCombinedPrompt`) — explicit save receives a direct instruction while extraction receives a conversation window and must detect signals itself. The Behavioral Constraint above says "same prompt and write engine"; confirm whether a shared base prompt with a variable injection point (explicit instruction vs. conversation window) satisfies both paths without degrading extraction signal quality.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev memory-write-subagent`
