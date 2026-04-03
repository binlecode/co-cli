# DESIGN — Session & Transcript Persistence

## What & How

The session subsystem manages identity, metadata, and conversation transcript persistence across REPL restarts. Each session produces a pair of files under `.co-cli/sessions/`: a JSON metadata file (`{session-id}.json`) and an append-only JSONL transcript (`{session-id}.jsonl`). On startup, the most recent session is restored by mtime scan — no TTL. Users start new sessions via `/new`, resume past sessions via `/resume`, and browse history via `/sessions`.

```text
.co-cli/sessions/
├── 6e67ce1a-4b2f-4d1e-8a3c-9f0b1c2d3e4f.json     ← metadata
├── 6e67ce1a-4b2f-4d1e-8a3c-9f0b1c2d3e4f.jsonl     ← transcript
├── a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d.json
└── a1b2c3d4-5e6f-7a8b-9c0d-1e2f3a4b5c6d.jsonl

Startup
  restore_session()
    → find_latest_session(sessions_dir)    # mtime scan over *.json
    → deps.session.session_id = found_id   # or new_session() if none
  resume hint banner
    → if {session_id}.jsonl exists: "Previous session available — /resume to continue"

Per-turn persistence (_finalize_turn)
  → touch_session(session_data) + save_session(sessions_dir, next_session)
  → append_transcript(sessions_dir, session_id, new_messages)

Session rotation (/new)
  → checkpoint knowledge → new_session() → rotate deps.session.session_id
  → next turn's transcript write goes to new file (writer is stateless)

Session resume (/resume)
  → list_sessions() → prompt_selection() → load_transcript()
  → set deps.session.session_id → ReplaceTranscript(history=messages)
```

## Core Logic

### Session Identity and Metadata

A session is a dict with four fields, persisted as `sessions/{session_id}.json` (mode 0o600):

```text
{
  "session_id": "6e67ce1a-4b2f-4d1e-8a3c-9f0b1c2d3e4f",  ← str(uuid.uuid4()), standard dashes
  "created_at": "2026-04-03T10:30:00+00:00",
  "last_used_at": "2026-04-03T11:45:00+00:00",
  "compaction_count": 2
}
```

Session IDs use standard `str(uuid.uuid4())` format (36 chars with dashes). The `_is_valid_uuid()` guard in `find_latest_session()` rejects malformed session IDs from disk to prevent path traversal — a crafted `session_id` like `../../etc/cron` would fail UUID validation and be skipped.

Key functions in `context/_session.py`:

| Function | Purpose | Mutates input? |
|----------|---------|---------------|
| `new_session()` | Create session dict with fresh UUID and UTC timestamps | N/A (factory) |
| `save_session(sessions_dir, session)` | Write `{session_id}.json` to sessions dir, chmod 0o600 | No |
| `load_session(path)` | Read a single `.json` file → dict or None | No |
| `find_latest_session(sessions_dir)` | Glob `*.json`, sort by mtime desc, return first valid (UUID-checked) | No |
| `touch_session(session)` | Return copy with `last_used_at` updated to now | No (returns copy) |
| `increment_compaction(session)` | Return copy with `compaction_count` + 1 | No (returns copy) |

**No TTL.** Sessions persist indefinitely. `is_fresh()` and `session_ttl_minutes` were removed. The most recent session (by file mtime) is always restored on startup. New sessions are created only by explicit user action (`/new`).

### Transcript Format and Serialization

Transcripts are stored as JSONL at `sessions/{session_id}.jsonl` (mode 0o600). Each line is a single-element list serialized via pydantic-ai's `ModelMessagesTypeAdapter`:

```jsonl
[{"kind":"request","parts":[{"content":"fix the bug","part_kind":"user-prompt"}],"timestamp":"2026-04-03T10:30:00+00:00"}]
[{"kind":"response","parts":[{"content":"I'll look at it...","part_kind":"text"}],"timestamp":"2026-04-03T10:30:05+00:00","model_name":"qwen3.5:35b"}]
```

Each line wraps one `ModelMessage` in a list because `ModelMessagesTypeAdapter` operates on `list[ModelMessage]`. This preserves discriminated union part types (`UserPromptPart`, `ToolCallPart`, `ToolReturnPart`, `TextPart`, `ThinkingPart`, etc.) across round-trip serialization. No custom entry types, no UUID chain, no metadata entries — session metadata stays in the `.json` file.

Key functions in `context/_transcript.py`:

| Function | Purpose |
|----------|---------|
| `append_messages(sessions_dir, session_id, messages)` | Append ModelMessage entries as JSONL lines; no-op on empty list; chmod 0o600 |
| `load_transcript(sessions_dir, session_id)` | Read full `.jsonl`, deserialize each line via `validate_json()`, skip malformed lines with warning |
| `list_sessions(sessions_dir)` | Glob `*.jsonl`, sort by mtime desc, extract title + line count per session |
| `_extract_title(path, max_bytes=4096)` | Read first 4KB, find first `user-prompt` part, truncate at 80 chars |

`SessionSummary` is a frozen dataclass returned by `list_sessions()`:

```text
SessionSummary(session_id, title, last_modified, message_count)
```

- `title`: first user prompt content, truncated to 80 chars + "..."
- `last_modified`: derived from file mtime (not from JSON content)
- `message_count`: count of non-empty JSONL lines (both requests and responses)

### Startup Flow

`restore_session()` in `bootstrap/_bootstrap.py`:

```text
restore_session(deps, frontend)
  1. find_latest_session(deps.config.sessions_dir)
       → glob *.json, sort by mtime desc
       → for each file: load JSON, validate session_id is a UUID
       → return first valid dict, or None
  2. if found:
       deps.session.session_id = session_data["session_id"]
       frontend.on_status("Session restored — {short_id}...")
     else:
       session_data = new_session()   # str(uuid.uuid4()) with dashes
       deps.session.session_id = session_data["session_id"]
       save_session(sessions_dir, session_data)   # may fail (OSError caught)
       frontend.on_status("Session new — {short_id}...")
  3. return session_data
```

After `restore_session()` returns, the REPL loop in `main.py` checks for a resume hint:

```text
transcript_path = sessions_dir / f"{session_id}.jsonl"
if transcript_path.exists():
    console.print("Previous session available — /resume to continue")
```

This fires when the restored session has a prior transcript (i.e. the user had a conversation before). On first-ever launch, no `.jsonl` exists, so no banner is shown.

### Per-Turn Persistence

`_finalize_turn()` in `main.py` is the single persistence point per turn:

```text
_finalize_turn(turn_result, message_history, session_data, deps, ...)
  1. next_history = turn_result.messages
  2. signal detection (on clean turns)
  3. next_session = touch_session(session_data)       # update last_used_at
     save_session(sessions_dir, next_session)          # overwrite {id}.json
  4. new_messages = turn_result.messages[len(message_history):]   # positional tail slice
     append_transcript(sessions_dir, session_id, new_messages)   # append to {id}.jsonl
  5. compactor.on_turn_end(next_history, deps)
  6. return (next_history, next_session)
```

The positional tail slice (`messages[len(previous_history):]`) captures exactly the new messages added by this turn — model responses, tool calls, and tool returns. It is not a content diff; it relies on pydantic-ai's guarantee that `turn_result.messages` extends (never replaces) the input `message_history`.

The transcript writer is **stateless**: it derives the file path from `deps.session.session_id` on every call. When `/new` rotates the session ID, the next `_finalize_turn()` write automatically goes to a new `.jsonl` file — no explicit file handle management.

### Session Rotation (`/new`)

`_cmd_new()` in `commands/_commands.py`:

```text
/new
  1. if history is empty → "Nothing to checkpoint" → return
  2. summarize current session via LLM (_index_session_summary)
  3. persist summary as session-{timestamp}.md in knowledge store
  4. new_session() → fresh UUID, current timestamps
  5. ctx.deps.session.session_id = new_id
  6. save_session(sessions_dir, new_session_data) → new {id}.json on disk
  7. return [] → REPL loop receives ReplaceTranscript(history=[])
```

Back in the REPL loop, the `ReplaceTranscript` handler detects that `deps.session.session_id` diverges from `session_data["session_id"]` and syncs `session_data` from the newly-created `.json` file:

```text
if deps.session.session_id != session_data.get("session_id"):
    rotated = load_session(sessions_dir / f"{deps.session.session_id}.json")
    if rotated:
        session_data = rotated
```

Without this sync, subsequent `_finalize_turn()` calls would `touch_session()` the old session's `.json` file (because `session_data` is a local variable in the REPL loop), making it the most-recently-modified file and causing the next startup to restore the wrong session.

### Session Resume (`/resume`)

`_cmd_resume()` in `commands/_commands.py`:

```text
/resume
  1. list_sessions(sessions_dir) → list[SessionSummary] sorted by mtime desc
  2. if empty → "No past sessions found." → return None
  3. format each as picker item: "{title} ({date} · {count} msgs)"
  4. prompt_selection(items, title="Resume session") → interactive arrow-key menu
  5. if cancelled → return None
  6. map selection back to SessionSummary via index
  7. load_transcript(sessions_dir, selected.session_id) → list[ModelMessage]
  8. ctx.deps.session.session_id = selected.session_id
  9. return ReplaceTranscript(history=messages)
```

The REPL loop's `session_data` sync (same mechanism as `/new`) detects the session ID change and reloads `session_data` from the selected session's `.json` file.

### Session Listing (`/sessions`)

`_cmd_sessions()` in `commands/_commands.py`:

```text
/sessions [keyword]
  1. list_sessions(sessions_dir) → all sessions sorted by mtime desc
  2. if keyword arg: filter summaries where keyword.lower() in title.lower()
  3. if empty → "No sessions found." → return None
  4. build rich Table with columns: Title (accent style), Date, Messages
  5. format date as %Y-%m-%d %H:%M, messages as line count string
  6. console.print(table)
```

Title extraction reads only the first 4KB of each `.jsonl` file (via `_extract_title()`), avoiding full transcript deserialization. Message count is a JSONL line count (both requests and responses).

### Session ID in Telemetry and Sub-agents

`deps.session.session_id` is used in three contexts beyond persistence:

1. **OTel trace spans**: `restore_session()` sets `span.set_attribute("session_id", short_id)` during bootstrap.
2. **Agent metadata**: `_orchestrate.py` passes `metadata={"session_id": deps.session.session_id}` to `agent.run_stream()` so traces carry the session context.
3. **Sub-agent metadata**: `subagent.py` passes the parent session ID in run metadata. Sub-agents receive a fresh `CoSessionState` with `session_id = ""` — they do not inherit the parent's session identity.
4. **`/capabilities` display**: shows the first 8 chars of the current session ID.

### Session Data Dual-Track Architecture

Session state lives in two synchronized locations:

| Location | What | Mutated by |
|----------|------|-----------|
| `deps.session.session_id` (in-memory) | Current session identity; used by transcript writer, telemetry, sub-agents | `restore_session()`, `/new`, `/resume` |
| `session_data` (local var in `_chat_loop`) | Session metadata dict; used by `touch_session()` / `save_session()` | `_finalize_turn()`, `/compact`, session-data sync |

These must stay in sync. The sync mechanism in the REPL loop (after `ReplaceTranscript`) detects divergence and reloads `session_data` from disk:

```text
if deps.session.session_id != session_data.get("session_id"):
    rotated = load_session(sessions_dir / f"{deps.session.session_id}.json")
    if rotated:
        session_data = rotated
```

### Error Handling

All session and transcript operations handle failures gracefully:

| Operation | Failure mode | Behavior |
|-----------|-------------|----------|
| `save_session()` in `restore_session()` | OSError (permissions, disk full) | Log status message; session_id still set in memory; session won't persist across restarts |
| `save_session()` in `/new` | OSError | `logger.warning`; session_id rotated in memory; old session restored on next startup |
| `append_messages()` | OSError | `logger.warning`; conversation continues without transcript persistence |
| `load_transcript()` malformed line | JSON decode error | Skip line, `logger.warning`; remaining lines still loaded |
| `load_transcript()` | OSError | `logger.warning`; return empty list |
| `find_latest_session()` corrupt `.json` | JSON decode error | Skip file, try next by mtime |
| `find_latest_session()` invalid UUID in `.json` | UUID validation fails | Skip file, try next by mtime |

### Security

- **Path traversal guard**: `_is_valid_uuid()` validates every `session_id` loaded from disk before it is used in file path construction. A crafted `.json` file with `session_id: "../../etc/cron"` would fail validation and be skipped.
- **File permissions**: both `.json` and `.jsonl` files are `chmod 0o600` (user read/write only). Session metadata and transcripts may contain sensitive conversation content.
- **No user input in paths**: session IDs are always generated by `uuid.uuid4()` internally. The `/resume` command selects from existing files via an interactive picker — it never accepts raw user input as a session ID.

### Behavioral Constraints

- Transcript files are **append-only** — never rewritten, never truncated.
- `/clear` clears in-memory history only — does not affect the transcript file.
- `/compact` compacts in-memory history and increments `compaction_count` — does not affect the transcript file.
- No TTL on sessions or transcripts — permanent until user deletes the `sessions/` directory manually.
- On startup, the REPL always starts with **empty** `message_history`. The banner hints at `/resume` but does not auto-load the transcript.
- The knowledge system (FTS5/hybrid search) remains separate — transcripts serve continuity (resume), not intelligence (recall).

### What Is Not Supported

- **Concurrent-instance safety**: two `co` processes in the same workspace each create their own session. Resuming the same session from two instances is unsupported (future work: file locking or PID guard).
- **Session deletion/cleanup command**: sessions accumulate indefinitely. A volume-based cleanup daemon is planned in `TODO-daemon-util-1-knowledge-compaction.md` section 2.5 (threshold: 500 MB, hotness-scored pruning).
- **Cross-session context injection**: the agent only sees the current session's history. No transcript content is injected into the knowledge store or system prompt.
- **Remote session sync**: not planned.

## Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `sessions_dir` | n/a | `<cwd>/.co-cli/sessions` | Per-project sessions directory; resolved from `CoConfig.from_settings()` |

`sessions_dir` is a `CoConfig` field (frozen, read-only after bootstrap). It is workspace-relative so each project directory maintains its own sessions — switching projects starts fresh context automatically. Not configurable via `settings.json` or env var — always derived from `cwd`.

## Files

| File | Purpose |
|------|---------|
| `co_cli/context/_session.py` | Session metadata: create, load, save, find latest, touch, increment compaction; UUID validation guard |
| `co_cli/context/_transcript.py` | JSONL transcript: append, load, list sessions with title extraction; `SessionSummary` dataclass |
| `co_cli/bootstrap/_bootstrap.py` | `restore_session()`: startup session restore via mtime scan |
| `co_cli/main.py` | `_finalize_turn()`: per-turn session touch + transcript append; `_chat_loop()`: resume hint banner, session_data sync after `/new`/`/resume` |
| `co_cli/commands/_commands.py` | `/new`: session rotation + knowledge checkpoint; `/resume`: interactive picker + transcript load; `/sessions`: table listing with keyword filter |
| `co_cli/deps.py` | `CoConfig.sessions_dir`, `CoSessionState.session_id`, `DEFAULT_SESSIONS_DIR` |
| `tests/test_bootstrap.py` | Session restore tests: existing session, empty dir, most recent, corrupt JSON, OSError on save |
| `tests/test_transcript.py` | Transcript round-trip, incremental append, malformed line skip, list_sessions mtime + title, empty dir, title truncation |
