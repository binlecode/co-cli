# DESIGN — Session Port Drift Analysis (co-cli vs claude-code)

Source of porting: `~/workspace_genai/fork-claude-code` (Anthropic claude-code decompiled source).

This document compares co-cli's session and transcript implementation against claude-code's production implementation, identifies every drift point, and justifies each deviation based on co-cli's specific constraints and design principles.

## 1. Storage Layout

| Aspect | claude-code | co-cli | Drift? |
|--------|-------------|--------|--------|
| **Base dir** | `~/.claude/projects/{sanitized-cwd}/` (user-global, keyed by project path via `sanitizePath()`) | `<cwd>/.co-cli/sessions/` (workspace-local) | **Yes** |
| **Transcript file** | `{sessionId}.jsonl` in project dir | `{sessionId}.jsonl` in sessions dir | Aligned |
| **Metadata file** | No separate file — metadata entries (custom-title, tag, agent-name, summary, worktree-state, PR-link, mode, etc.) are **inline typed JSONL entries** mixed into the transcript | Separate `{sessionId}.json` with metadata dict (`session_id`, `created_at`, `last_used_at`, `compaction_count`) | **Yes** |
| **Sub-agent transcripts** | `{sessionId}/subagents/agent-{agentId}.jsonl` nested under session dir | Not implemented (sub-agents don't persist transcripts) | N/A — future |

**Justification:**

- **Workspace-local vs user-global**: co-cli uses `.co-cli/` as workspace-local config (`.gitignore`d per-project). claude-code uses `~/.claude/projects/` with `sanitizePath(cwd)` as subdirectory keys. claude-code has multiple fixed bugs around `sanitizePath` and symlink resolution causing sessions to be invisible when working directory involves symlinks or when `originalCwd` diverges from the resolved path. co-cli's workspace-local approach avoids this class of bugs entirely — `sessions/` lives next to the project, no cross-project path resolution needed. Workspace deletion naturally cleans up sessions.

- **Separate metadata JSON vs inline JSONL entries**: claude-code stores 15+ entry types in one JSONL (transcript messages + `summary`, `custom-title`, `ai-title`, `last-prompt`, `task-summary`, `tag`, `agent-name`, `agent-color`, `agent-setting`, `pr-link`, `mode`, `worktree-state`, `content-replacement`, `file-history-snapshot`, `attribution-snapshot`, `context-collapse-commit`, `context-collapse-snapshot`). Metadata entries are read from a tail window (`readHeadAndTail`, `LITE_READ_BUF_SIZE`) on session listing, and `reAppendSessionMetadata()` must fire on shutdown to push metadata back into the tail window after long conversations push it out. co-cli's two-file approach (`{id}.json` overwritten on every turn + `{id}.jsonl` append-only) eliminates this complexity: metadata is always a clean JSON read, transcript is always a clean JSONL append.

## 2. Session ID Format

| Aspect | claude-code | co-cli | Drift? |
|--------|-------------|--------|--------|
| **Generation** | `randomUUID()` (Node `crypto`) → branded `SessionId` type | `str(uuid.uuid4())` (Python stdlib) | Aligned (both standard UUID v4 with dashes) |
| **Type safety** | Branded type: `SessionId = string & { __brand: 'SessionId' }` prevents compile-time mixup with `AgentId` | Plain `str` | **Minor drift** |
| **Validation** | `validateUuid()` utility for disk-loaded IDs | `_is_valid_uuid()` guard in `find_latest_session()` | Aligned |

**Justification:** Python lacks TypeScript's branded types. The runtime UUID validation guard provides equivalent safety at the boundary where untrusted data enters (disk reads).

## 3. Session Discovery and Resume

| Aspect | claude-code | co-cli | Drift? |
|--------|-------------|--------|--------|
| **Startup restore** | Stat-based progressive loading across all `~/.claude/projects/*/` dirs; `getSessionFilesLite()` reads only file stats, `enrichLogs()` loads content on demand | `find_latest_session()` — mtime scan of local `sessions/*.json`, load first valid | **Yes** |
| **Listing count** | 50 most recent sessions (configurable `INITIAL_ENRICH_COUNT`), progressive enrichment on scroll | All sessions in workspace (no limit) | **Minor drift** |
| **Title extraction** | First non-skipped user prompt from JSONL head; `SKIP_FIRST_PROMPT_PATTERN` regex skips XML tags, interrupt markers, IDE context | First `user-prompt` part from first 4KB of JSONL via `_extract_title()` | **Partial drift** |
| **Resume picker** | React/Ink `LogSelector` component with keyboard shortcuts: P (preview), R (rename), copy session ID, git branch display, message count | `prompt_selection()` arrow-key menu with formatted items (`title (date · N msgs)`) | **Yes — UX scope** |
| **CLI flags** | `--resume <name\|id>`, `--continue` (most recent), `--fork-session`, `--session-id <custom>`, `--name <display-name>` | `/resume` slash command only (no CLI flags) | **Yes — scope** |
| **Cross-project resume** | `loadAllProjectsMessageLogs()` scans all project dirs; `switchSession()` atomically sets `sessionId` + `sessionProjectDir` | Single workspace only — no cross-project support | **Yes — by design** |

**Justification:**

- co-cli is a single-project REPL. claude-code is a multi-project tool used across git worktrees, monorepos, and remote containers. Stat-based progressive loading across all project dirs is necessary for claude-code's scale but over-engineering for co-cli's single `sessions/` directory.
- The 50-session limit and progressive enrichment exist because claude-code users accumulate thousands of sessions across projects. co-cli's workspace-local design caps session count naturally per project.
- CLI flags (`--resume`, `--continue`) are future work for co-cli — slash commands are the MVP entry point, matching the REPL-first interaction model.

## 4. Transcript Format

| Aspect | claude-code | co-cli | Drift? |
|--------|-------------|--------|--------|
| **Line format** | `SerializedMessage` — extends `Message` with `cwd`, `userType`, `sessionId`, `timestamp`, `version`, `gitBranch`, `slug`, `entrypoint` fields | `ModelMessagesTypeAdapter.dump_json([msg])` — pydantic-ai native single-element list per line | **Yes** |
| **Entry types in JSONL** | Mixed: transcript messages (`user`, `assistant`, `attachment`, `system`) + 15+ metadata/state entry types (summary, title, tag, worktree, PR-link, mode, file-history, attribution, content-replacement, context-collapse, etc.) | Pure transcript only — one `ModelMessage` per line; metadata in separate `.json` | **Yes** |
| **parentUuid chain** | Each message has `uuid` + `parentUuid` forming a linked chain; `isChainParticipant()` type guard; `buildConversationChain()` reconstructs from last leaf; progress entries excluded from chain | Position-ordered (no UUID chain) — pydantic-ai messages ordered by list position | **Yes** |
| **Compaction boundary** | `SystemCompactBoundaryMessage` entries mark compaction points in JSONL; `SKIP_PRECOMPACT_THRESHOLD` optimizes load by skipping pre-compaction messages | Not in transcript (compaction is in-memory only; `compaction_count` tracked in metadata JSON) | **Yes** |
| **Legacy handling** | `isLegacyProgressEntry()` bridge for pre-PR#24099 transcripts; `isEphemeralToolProgress()` for transient UI entries | N/A — no legacy format | N/A |

**Justification:**

- **pydantic-ai native serialization**: co-cli uses `ModelMessagesTypeAdapter` which handles discriminated union round-trip serialization preserving all part types (`UserPromptPart`, `ToolCallPart`, `ToolReturnPart`, `TextPart`, `ThinkingPart`, etc.). This is pydantic-ai's canonical approach. Using custom serialized types would fight the framework's type guarantees and create a maintenance burden on pydantic-ai version upgrades.

- **No parentUuid chain**: claude-code needs UUID chains because it supports conversation forking (`--fork-session` creates a child session branching from a parent), sidechains (sub-agent conversations branch from the main thread and rejoin), message removal (tombstones remove orphaned messages by UUID), and `buildConversationChain()` reconstruction from any leaf. co-cli has none of these — conversations are linear, sub-agents don't persist transcripts, there's no fork, and there are no tombstones. Position-ordered lists are the correct simpler model for linear conversations.

- **No mixed entry types**: claude-code's single-file approach with 15+ entry types requires complex parsing (`isTranscriptMessage()` type guard, `readHeadAndTail()` for metadata extraction from tail window, `reAppendSessionMetadata()` on shutdown to prevent metadata from being pushed outside the read window, `progressBridge` rewrite for legacy entries). co-cli's two-file split eliminates this entire class of complexity.

## 5. Write Path

| Aspect | claude-code | co-cli | Drift? |
|--------|-------------|--------|--------|
| **Write mechanism** | `Project` class with batched async write queue (`insertMessageChain`); `flush()` on shutdown via `registerCleanup()` | Synchronous `append_messages()` in `_finalize_turn()` — single write point per turn | **Yes** |
| **Dedup** | `getSessionMessages()` returns UUID set of already-persisted messages; `recordTranscript()` skips messages already in the set | Positional tail slice `messages[len(previous_history):]` — no dedup needed | **Yes** |
| **New message detection** | Iterate all messages, skip those in UUID set, track `startingParentUuid` for chain continuity | `turn_result.messages[len(message_history):]` — pydantic-ai guarantees extension, not replacement | **Simpler** |
| **Shutdown flush** | `registerCleanup()` → `project.flush()` → `reAppendSessionMetadata()` | N/A — synchronous writes complete before `_finalize_turn()` returns | **Simpler** |
| **CCR integration** | Optional internal event writer for remote session persistence (`registerCCRv2EventWriter`) | N/A (no remote sessions) | N/A |

**Justification:**

- co-cli's synchronous write is correct for a single-threaded local REPL — one write point per turn, no concurrency, no remote sessions. claude-code batches because it has parallel sub-agents writing sidechains, remote CCR sessions, and high-frequency progress messages that need queuing.
- The positional tail slice is correct because pydantic-ai guarantees that `turn_result.messages` extends (never replaces) the input `message_history`. No UUID-based dedup is needed.
- No shutdown flush needed because writes are synchronous and complete within `_finalize_turn()`.

## 6. Session Rotation

| Aspect | claude-code | co-cli | Drift? |
|--------|-------------|--------|--------|
| **Mechanism** | `regenerateSessionId()` in bootstrap state module; `clearConversation` resets app state; `sessionProjectDir` reset to null | `new_session()` + rotate `deps.session.session_id` + save new `.json` to disk | Aligned in intent |
| **Knowledge checkpoint** | No automatic knowledge checkpoint on `/clear` | `/new` summarizes session via LLM and persists to knowledge store before rotating | **co-cli addition** |
| **Parent tracking** | `parentSessionId` preserved for fork genealogy (`--fork-session` sets current as parent) | No parent tracking | **Yes — intentional** |
| **Session data sync** | `switchSession()` atomically sets `sessionId` + `sessionProjectDir` in module state; all callers read from the same singleton | REPL loop syncs local `session_data` variable after `ReplaceTranscript` when `deps.session.session_id` diverges | **Different mechanism, same goal** |

**Justification:**

- co-cli's `/new` combines two operations (checkpoint + rotate) because co-cli has a knowledge system that benefits from session summaries as persistent memories. claude-code separates `/clear` (reset context) from session naming/tagging — it has no equivalent knowledge persistence layer.
- No parent tracking because co-cli has no `--fork-session` feature. Sessions are independent.
- The dual-track sync (in-memory `deps.session.session_id` + local `session_data` dict) is a co-cli-specific pattern arising from pydantic-ai's `RunContext[CoDeps]` architecture where session_id lives on deps but the metadata dict is a REPL loop local. claude-code's singleton module state avoids this by having all state in one place.

## 7. Error Handling

| Aspect | claude-code | co-cli | Drift? |
|--------|-------------|--------|--------|
| **Corrupt transcripts** | `parentUuid` cycle detection (caused hangs on resume); `progressBridge` rewrite for legacy progress entries; tombstone removal for orphaned messages; 50MB `MAX_TOMBSTONE_REWRITE_BYTES` limit | Skip malformed JSONL lines with `logger.warning` | **Simpler** |
| **Large file guard** | `MAX_TRANSCRIPT_READ_BYTES = 50MB` — bail before OOM on multi-GB session files | No size guard | **Gap** |
| **Path validation** | `validateUuid()` + `sanitizePath()` for project directory derivation | `_is_valid_uuid()` on session IDs from disk | Aligned for session IDs |
| **Graceful shutdown** | `registerCleanup()` → flush + reappend metadata; handles SSH disconnect, SIGTERM | Synchronous writes — no shutdown handler needed | **Simpler** |

**Justification:**

- co-cli's simpler error handling matches its simpler transcript format (no UUID chains → no cycle detection, no mixed entry types → no progress bridging, no tombstones → no rewrite). The complexity claude-code handles is created by its own format choices.
- The 50MB read guard is a valid gap. co-cli's daemon cleanup plan (500MB threshold at `sessions/` dir level, in `TODO-daemon-util-1-knowledge-compaction.md` section 2.5) will prevent sessions from growing to dangerous sizes, but a per-file guard on `load_transcript()` would be a good defense-in-depth addition.

## 8. Features in claude-code Not Ported to co-cli

| Feature | claude-code impl | co-cli status | Rationale |
|---------|-----------------|---------------|-----------|
| `--continue` (auto-resume most recent) | CLI flag, auto-loads last session transcript | Banner hint + `/resume` (opt-in) | Intentional — auto-resume is confusing for topically diverse co-cli usage (PO decision PO-M-1 in TODO) |
| `--fork-session` (branch conversation) | Creates child session with parent tracking, UUID chain branching | Not planned | No use case — co-cli conversations are linear |
| `/rename` (custom session titles) | `CustomTitleMessage` + `AiTitleMessage` entry types in JSONL | Future | Would add to metadata `.json` (not JSONL entry) |
| `/tag` (session tags) | `TagMessage` entry type, searchable in `/resume` | Future | Same — metadata `.json` |
| AI-generated session titles | `AiTitleMessage`, generated within seconds of first message | First-prompt extraction only | Intentional — simpler, avoids LLM call on every new session |
| Session search (agentic) | `agenticSessionSearch()` with LLM-powered query | Keyword substring filter on title | Intentional — sufficient at co-cli's scale |
| Sub-agent transcript persistence | `agent-{agentId}.jsonl` in `subagents/` dir; `.meta.json` sidecar | Not implemented | Future — sub-agents don't currently persist state |
| Remote session sync (CCR) | Full CCR bridge with external metadata, session state events, OAuth | Not planned | co-cli is local-first only |
| Concurrent instance safety | Atomic writes (`appendFile`), `sessionProjectDir` locking, PID file with session ID | None | Future (file locking / PID guard noted in out-of-scope) |
| Transcript search (`/` in transcript mode) | React/Ink UI with regex search, `n`/`N` navigation | Not applicable | co-cli has no transcript-mode TUI |
| Worktree session persistence | `WorktreeStateEntry` in JSONL, `restoreWorktreeForResume()` | Not applicable | co-cli has no worktree management |
| Content replacement tracking | `ContentReplacementRecord` for prompt cache stability on resume | Not applicable | co-cli doesn't use Anthropic prompt caching |
| Context collapse persistence | `ContextCollapseCommitEntry` + `ContextCollapseSnapshotEntry` | Not applicable | co-cli uses simpler in-memory compaction |
| File history / attribution snapshots | `FileHistorySnapshotMessage`, `AttributionSnapshotMessage` persisted in JSONL | Not applicable | co-cli doesn't track file history or commit attribution |

## 9. Actionable Gaps

| Gap | Risk | Recommendation |
|-----|------|----------------|
| No `MAX_TRANSCRIPT_READ_BYTES` guard on `load_transcript()` | OOM on very large sessions (multi-GB) | Add 50MB size check before reading; return empty + warning if exceeded |
| No session listing limit | Slow `/sessions` and `/resume` with hundreds of sessions | Add configurable limit (default 50), matching claude-code's `INITIAL_ENRICH_COUNT` |
| No `--continue` CLI flag | Minor UX gap for power users who always want to resume | Future — `/resume` is sufficient for MVP |
| No concurrent instance guard | Two `co` instances in same project could corrupt session files | Future — file locking or PID guard (already noted in out-of-scope) |

## 10. Summary

co-cli's session implementation is a **deliberate, justified simplification** of claude-code's production system, adapted for co-cli's constraints:

1. **pydantic-ai native serialization** (`ModelMessagesTypeAdapter`) instead of custom `SerializedMessage` — framework-idiomatic, correct for Python, preserves all discriminated union part types
2. **Two-file split** (`.json` metadata + `.jsonl` transcript) instead of mixed-entry JSONL — avoids claude-code's tail-window, reappend-on-shutdown, and 15+ entry type parsing complexity
3. **Workspace-local storage** (`<cwd>/.co-cli/sessions/`) instead of user-global `~/.claude/projects/{sanitized-cwd}/` — matches co-cli's per-project convention, avoids path resolution bugs
4. **No UUID chain** — correct for linear conversations without forking, sidechains, or tombstones
5. **Synchronous writes** — correct for single-threaded REPL without remote sessions or parallel sub-agent persistence
6. **Knowledge checkpoint on `/new`** — co-cli-specific addition leveraging the knowledge system that claude-code doesn't have

Every drift from claude-code either (a) follows from pydantic-ai's different architecture, (b) removes complexity that only exists because of claude-code features co-cli doesn't need, or (c) adds co-cli-specific value (knowledge checkpoint). The two actionable gaps (transcript size guard, session listing limit) are low-risk at current scale and have mitigations planned (daemon cleanup).
