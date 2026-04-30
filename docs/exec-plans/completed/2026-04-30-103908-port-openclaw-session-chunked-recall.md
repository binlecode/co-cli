# Plan: Port Openclaw Session Chunked Recall

Task type: code-feature

## Context

The sessions channel today routes through a separate FTS-only store
(`~/.co-cli/session-index.db`) with a 3-way LLM summarization fan-out per
recall. On a local Ollama deployment that fan-out serializes at the GPU and
dominates wall time (~9–24s per `memory_search` hit involving sessions). The
artifacts channel already runs the openclaw-shape unified pipeline
(`chunks` + `chunks_fts` + `chunks_vec` + RRF + cross-encoder rerank) with
citable chunk bounds. This plan unifies sessions onto the same pipeline as
`source='session'` and **deletes the legacy FTS-summarize path entirely** —
no fallback, no toggles, single recall path.

**Current-state validation (file:line):**

- `co_cli/memory/knowledge_store.py:76-85` — `chunks` table already has
  `source` discriminator + `start_line`/`end_line`. No schema add.
- `co_cli/memory/knowledge_store.py:44-65` — `docs` table keyed by
  `(source, path, chunk_id)` via UNIQUE; sessions plug in as a new source.
- `co_cli/memory/knowledge_store.py:425-482` — `index_chunks(source, doc_path, chunks)`
  is generic over source and atomically replaces chunks (delete + insert).
- `co_cli/memory/knowledge_store.py:679-685, 814-820` — `_run_chunks_fts` and
  `_vec_chunks_search` already push `source IN (?)` into both SQL legs
  pre-RRF. Source filter is complete; no audit needed.
- `co_cli/memory/knowledge_store.py:1113-1121` — `needs_reindex(source, path, hash)`
  exists; reuse it for session hash-skip.
- `co_cli/memory/knowledge_store.py:1123-1196` — `sync_dir(source, directory, glob)`
  is the model for a new `sync_sessions(directory)` method.
- `co_cli/memory/session_store.py:38-70` — legacy `messages` + `messages_fts`
  schema in `session-index.db`. Entire module deletes in TASK-7.
- `co_cli/memory/indexer.py:88-120` — `extract_messages` currently emits ONLY
  `user-prompt` + `text` parts, dropping `tool-call`/`tool-return`. Needs
  extension before chunking can render `Tool[name]:` lines.
- `co_cli/memory/session.py:30-47` — `parse_session_filename(name)` returns
  `tuple[str, datetime] | None` (uuid8, created_at). Plan code must unpack
  the tuple, not access attributes.
- `co_cli/memory/chunker.py:1-11` — `Chunk` dataclass has fields
  `index, content, start_line, end_line`. Reuse it; the index field is
  populated by the chunker, not the caller.
- `co_cli/tools/memory/recall.py:200-258` — `_search_sessions` runs the
  FTS+summarize pipeline; rewritten to single-call against `KnowledgeStore`.
- `co_cli/tools/memory/recall.py:30-48` — `_SUMMARIZATION_TIMEOUT_SECS=60`,
  `_SESSIONS_CHANNEL_CAP=3`, `_SESSION_FALLBACK_PREVIEW_CHARS=500`,
  `_SESSION_SUMMARY_PREVIEW_CHARS=300`. Summarization-only constants are
  retired in TASK-7.
- `co_cli/memory/summary.py:1-232` — full module is dedicated to session
  summarization (prompt, retry, truncation around match anchor). Deleted
  in TASK-7 along with `prompts/session_summarizer.md`.
- `co_cli/bootstrap/core.py:306-328` — `init_session_store` opens
  `session-index.db` and calls `SessionStore.sync_sessions`. Replaced by
  `init_session_index` calling `KnowledgeStore.sync_sessions`.

**Real co-cli session JSONL shape** (verified via
`~/.co-cli/sessions/2026-04-16-T235238Z-b8445d2b.jsonl`): each line is a
`list[dict]` where each dict has `kind: "request" | "response"` (NOT `role`)
and a `parts` array; each part has a `part_kind` in
`{user-prompt, text, thinking, tool-call, tool-return, system-prompt, retry-prompt}`.
Top-level `kind: session_meta` and `compact_boundary` rows appear as control
lines (dict, not list). The original openclaw plan's sanitization rules
(`record["role"]`, `record["kind"] == "session_meta"`) would no-op against
this shape; sanitization here is rewritten in co-cli's part-kind shape.

**Existing tests touching the legacy path** (will be rewritten or deleted in TASK-7):

- `tests/memory/test_memory_index.py` — `SessionStore.index_session`,
  `messages_fts` row counts, `SessionStore.search` — all retired.
- `tests/memory/test_session_summary.py` — `summarize_session_around_query`
  pipeline — retired.
- `tests/memory/test_session_search_tool.py` — `_search_sessions` end-to-end —
  rewritten against the chunked path in TASK-5.
- `tests/memory/test_format_conversation.py` — `_format_conversation` —
  decision: keep the helper if any caller outside summary.py uses it; else
  retire with summary.py.
- `tests/memory/test_truncate_around_matches.py` — `_truncate_around_matches`
  helper — retired with summary.py.
- `tests/memory/test_memory_search_browse.py` — empty-query browse mode;
  unchanged (still reads filesystem via `session_browser.py`).

**Workflow artifact hygiene:** Existing plan file
`docs/exec-plans/active/2026-04-30-103908-port-openclaw-session-chunked-recall.md`
is being rewritten in place to fold deprecation into Phase 1 and fix the
shape-mismatch blockers raised at Gate 1. No other stale artifacts noted.

---

## Problem & Outcome

**Problem:** Sessions and artifacts use disjoint retrieval pipelines.
Sessions get FTS-only recall plus a mandatory N×LLM summarization step that
dominates wall time on local models, drops content via 100×-compression
summaries, regenerates non-deterministic prose per call, and offers no
drill-down to verbatim turns. Two SQLite files (`session-index.db` plus
`co-cli-search.db`) maintain near-duplicate indexes over the same content.

**Failure cost:** Every session-touching `memory_search` blocks the agent
loop for ~9–24s on Ollama. Paraphrase recall (e.g. "the docker bug" →
"container build failed") fails entirely because the sessions side has no
vector leg. Summaries are non-reproducible — identical queries return
different prose, fidelity to source unobservable. The agent has no path
from a hit back to the verbatim turn (line numbers, exact commands, error
strings). Maintenance burden of two parallel indexes ships every session
edit twice.

**Outcome:** Sessions index into the existing
`chunks`/`chunks_fts`/`chunks_vec` pipeline as `source='session'`.
`memory_search` returns chunks with citations
(`session_id`, `start_line`, `end_line`, `score`) instead of LLM-rewritten
summaries. A new `memory_read_session_turn` tool drills from a chunk hit
back to verbatim transcript lines on demand. The legacy
FTS+summarize path (`SessionStore`, `messages_fts`,
`summarize_session_around_query`, `session-index.db`) is **deleted**, not
gated. End-to-end recall on a hybrid-configured local stack drops from
~9–24s to ~150–250ms, deterministic and citable.

---

## Scope

**In scope:**

- TASK-1: Extend `extract_messages` to emit all retained part-kinds
  (user-prompt, text, tool-call, tool-return) with optional `tool_name`,
  drop noise (thinking, system-prompt, retry-prompt).
- TASK-2: New module `co_cli/memory/session_chunker.py` — sanitize +
  flatten-with-role-prefix + token-uniform chunking with line-map remapping.
- TASK-3: `KnowledgeStore.index_session(path)` + `sync_sessions(directory)`.
  Per-session dedup of search results stays in `_search_sessions` (TASK-5);
  no MMR or recency-weight in this delivery — see Backlog.
- TASK-4: Replace `init_session_store` → `init_session_index` calling
  `KnowledgeStore.sync_sessions`. Remove `SessionStore` from `CoDeps`.
  One-shot migration: unlink `~/.co-cli/session-index.db` if present.
- TASK-5: Rewrite `_search_sessions` to a single chunked-recall call against
  `KnowledgeStore.search(source=['session'])`. Update result rendering.
- TASK-6: New tool `memory_read_session_turn` in
  `co_cli/tools/memory/read.py`.
- TASK-7: Delete `co_cli/memory/session_store.py`, summarization helpers in
  `co_cli/memory/summary.py` (`summarize_session_around_query`,
  `_truncate_around_matches`, `_format_conversation` if unused after TASK-5),
  `co_cli/memory/prompts/session_summarizer.md`,
  retired tests under `tests/memory/` (per Context list above), and stale
  imports/refs.
- TASK-8: Config additions in `co_cli/config/knowledge.py`
  (`session_chunk_tokens`, `session_chunk_overlap`) + corresponding env-map
  entries. The chunker module exposes module-level defaults so TASK-2 lands
  before TASK-8 wires the settings through.

**Out of scope:**

- Per-personality session scoping (no `personality` filter on
  chunks/docs).
- CJK trigram tokenizer.
- Live (active) session search overlay.
- Per-chunk timestamp + per-chunk decay (today: session-level via
  `docs.created`).
- Embedding tool-call arguments — only the tool-name and tool-return content
  are indexed.
- Changes to `dream` cycle, `memory_create`, `memory_modify`, `memory_list`,
  empty-query browse mode.
- A `memory_summarize_results` tool to LLM-compress retrieved chunks — defer
  until usage shows it is needed.

---

## Behavioral Constraints

- **No artifact regression.** Existing `memory_search` over artifacts must
  return identical chunks, scores, and ordering on the artifact corpus when
  no sessions are present. With sessions indexed, an artifact-only query
  (`source='knowledge'` filter) must produce results identical to today's
  behavior — no new ranking pass is introduced in this delivery, so the
  artifact path is byte-for-byte unchanged.
- **Single recall path for sessions.** All session recall goes through
  `KnowledgeStore.search(source=['session'])`. There is no `legacy` /
  `auto` toggle, no `session_search_path` config, no
  `bridge_to_chunks` flag. The legacy code path is deleted, not gated.
- **Verbatim transcript leak surface is explicit.** Session chunk text is
  the raw transcript: `User:` / `Assistant:` / `Tool[name](return):` lines
  exactly as the user typed and tools emitted. Tool-call arguments are
  dropped at index time (chunker emits `Tool[name](call)` with no args);
  tool-return bodies ARE indexed verbatim. The renderer's 300-char preview
  cap and `memory_read_session_turn`'s 200-line / 16 KB ceiling are the
  only redaction surfaces today. Any future PII-redaction layer hooks into
  `flatten_session` in `session_chunker.py`, not the storage layer.
- **Hybrid degradation is internal to KnowledgeStore.** When the embedder is
  unreachable at construction time, `_discover_knowledge_backend` already
  degrades the store to FTS5-only mode. In that mode, `index_chunks` writes
  chunk content+FTS but skips embeddings; `_hybrid_search` falls back to
  doc-level FTS results (`knowledge_store.py:586-595`). Sessions inherit this
  path — they are still searchable BM25-only when TEI is down.
- **`chunks` schema is shared.** Sessions and artifacts use the same `chunks`
  table; no parallel-table fork. `source='session'` discriminates.
- **`doc_path` for sessions = `uuid8`.** Use the 8-char UUID returned by
  `parse_session_filename` (the same value used as the display short ID).
  Guarantees PRIMARY KEY uniqueness against artifacts whose `path` values
  are real filesystem paths.
- **Line-map consumed at index time.** Per-chunk `start_line`/`end_line` are
  remapped to original 1-indexed JSONL line numbers before insert. The
  line-map itself is not persisted — no schema add.
- **Sanitization is deterministic.** `chunk_session(p)` called twice on an
  unchanged file must produce identical chunk content + bounds (used for
  `KnowledgeStore.needs_reindex` hash skip).
- **Append-only respected.** Re-indexing must not delete the JSONL.
  On size-change, all chunks for that session are replaced atomically by
  `index_chunks` (delete-then-insert).
- **Idempotent indexing at chunk level, with partial-write recovery.**
  `index_session(p)` must short-circuit before touching `index_chunks` when
  the session content hash is unchanged AND the chunk rows exist for that
  session. The chunk-count probe is required because a prior crash between
  `index()` and `index_chunks()` would leave the docs row with the new hash
  and zero chunks; without the probe, `needs_reindex` returns False and the
  session is permanently un-indexed. Implementation:
  ```python
  if not self.needs_reindex("session", uuid8, content_hash):
      chunk_count = self._conn.execute(
          "SELECT COUNT(*) FROM chunks WHERE source='session' AND doc_path=?",
          (uuid8,),
      ).fetchone()[0]
      if chunk_count > 0:
          return
      # else fall through and re-index
  ```
- **No tool-surface break for renderer-only callers.** `memory_search` continues
  to return one flat list with `channel='sessions'` entries. Field shape
  changes: instead of `{summary}`, sessions carry
  `{chunk_text, start_line, end_line, score}`. Callers that only display
  `tool_output.display` need no change; the renderer in `recall.py` ships
  the new fields.
- **One-shot legacy DB removal.** On first bootstrap after the cutover, if
  `~/.co-cli/session-index.db` exists, log + unlink it. Idempotent on
  subsequent runs.

---

## High-Level Design

### ✓ DONE — TASK-1 — `extract_messages` extension

**File:** `co_cli/memory/indexer.py`

Today `extract_messages` returns only `user-prompt` and `text` parts. The
chunker needs `tool-call` / `tool-return` too (for `Tool[name]:` line
prefixes). Extend the function to emit all retained kinds, with a new
`tool_name: str | None` field on `ExtractedMessage`.

```python
@dataclass
class ExtractedMessage:
    line_index: int                # 0-indexed JSONL line number
    part_index: int
    role: str                      # 'user' | 'assistant' | 'tool-call' | 'tool-return'
    content: str
    timestamp: str | None
    tool_name: str | None = None   # populated for tool-call/tool-return only
```

Default `= None` keeps existing construction sites trivially default-safe.

**Transient-state note.** TASK-1 lands before TASK-7 deletes
`SessionStore`. In the interval, `SessionStore.index_session` will
receive new `role='tool-call' | 'tool-return'` rows and write them into
`messages_fts`. This is benign: the legacy index is fully retired in the
same delivery before any user upgrade and never read again. No-op for
external callers.

Drop part_kinds: `thinking`, `system-prompt`, `retry-prompt` (always noise
for recall). Drop empty/whitespace-only content. Top-level non-list rows
(`session_meta`, `compact_boundary`) are already skipped by the existing
`_extract_from_line` guard. No new sanitization rules — the chunker layers
its own (TASK-2).

`SessionStore.index_session` is the only current caller and is being
deleted in TASK-7, so the signature change has no migration burden inside
this delivery.

### ✓ DONE — TASK-2 — `co_cli/memory/session_chunker.py`

Three pure functions plus an orchestrator. No DB I/O.

```python
SESSION_CHUNK_TOKENS = 400          # config-overridable
SESSION_CHUNK_OVERLAP = 80
SESSION_LINE_WRAP_CHARS = 800

@dataclass
class SessionChunk:
    text: str               # multi-line, role prefixes preserved
    start_jsonl_line: int   # 1-indexed (post +1 from extract_messages 0-index)
    end_jsonl_line: int     # 1-indexed inclusive

def flatten_session(messages: list[ExtractedMessage]) -> tuple[list[str], list[int]]:
    """Render each message as one or more role-prefixed lines, wrapping at
    SESSION_LINE_WRAP_CHARS on word boundaries.

    Prefix rules:
      role == 'user'        → 'User: <content>'
      role == 'assistant'   → 'Assistant: <content>'
      role == 'tool-call'   → 'Tool[<tool_name>](call)'  (args dropped — see Open Q3)
      role == 'tool-return' → 'Tool[<tool_name>](return): <content>'

    Sanitization (drop the message before flattening):
      - assistant content len <= 10 with no following tool-call → heartbeat
      - tool-return content len < 10 → empty/ack tool result
    Returns (flat_lines, line_map) where line_map[i] is the 1-indexed JSONL
    line number for flat_lines[i] (slice replicates lineMap entries).
    """

def chunk_flattened(
    flat_lines: list[str],
    line_map: list[int],
    *,
    chunk_tokens: int = SESSION_CHUNK_TOKENS,
    overlap_tokens: int = SESSION_CHUNK_OVERLAP,
) -> list[SessionChunk]:
    """Token-uniform sliding window. Token estimate = len(text) // 4
    (matches co_cli/memory/chunker.py). On chunk close, remap to JSONL bounds:
        start = min(line_map[i..j]); end = max(line_map[i..j])"""

def chunk_session(jsonl_path: Path) -> list[SessionChunk]:
    """High-level: extract_messages → flatten → chunk."""
```

Long-line wrapping at 800 chars: split priority `\n\n` > `\n` > `. ` > word
boundary > hard cut. line_map entries are duplicated for each wrap slice.

Sanitization filters are signal-based, not name-based — no maintained
drop-set of tool names. This avoids drift as new tools land.

### ✓ DONE — TASK-3 — `KnowledgeStore` indexing

**File:** `co_cli/memory/knowledge_store.py`

#### `index_session(self, session_path: Path) -> None`

```python
def index_session(self, session_path: Path) -> None:
    """Index a session JSONL into chunks/chunks_fts/chunks_vec.

    Idempotent — content hash skip avoids re-embedding unchanged sessions.
    """
    from co_cli.memory.session_chunker import chunk_session
    from co_cli.memory.chunker import Chunk

    parsed = parse_session_filename(session_path.name)
    if parsed is None:
        logger.warning("Unrecognised session filename: %s", session_path.name)
        return
    uuid8, created_at = parsed

    sess_chunks = chunk_session(session_path)
    if not sess_chunks:
        return

    full_text = "\n\n".join(c.text for c in sess_chunks)
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()

    if not self.needs_reindex("session", uuid8, content_hash):
        chunk_count = self._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE source='session' AND doc_path=?",
            (uuid8,),
        ).fetchone()[0]
        if chunk_count > 0:
            return  # hash-skip — content unchanged AND chunks present
        # else: partial-write recovery — fall through and re-index

    self.index(
        source="session",
        kind="session",
        path=uuid8,
        title=uuid8,
        content=full_text,
        mtime=session_path.stat().st_mtime,
        hash=content_hash,
        created=created_at.isoformat(),
        updated=created_at.isoformat(),
    )

    chunk_records = [
        Chunk(
            index=i,
            content=c.text,
            start_line=c.start_jsonl_line,
            end_line=c.end_jsonl_line,
        )
        for i, c in enumerate(sess_chunks)
    ]
    self.index_chunks(source="session", doc_path=uuid8, chunks=chunk_records)
```

Notes vs. the prior plan: hash-skip happens before `index_chunks` (which
embeds in hybrid mode and is the expensive leg) AND only short-circuits
when chunks exist (partial-write recovery), `parse_session_filename`
returns are unpacked correctly, `Chunk` is reused with explicit `index` set
by enumeration.

#### `sync_sessions(self, sessions_dir: Path, exclude: Path | None = None) -> int`

Mirrors `sync_dir`'s shape. Iterates `sessions_dir.glob("*.jsonl")`, skips
`exclude`, calls `index_session(p)` per file, removes stale entries via
existing `remove_stale("session", current_paths, directory=sessions_dir)`.
Returns count of newly-indexed sessions.

#### Search-time ranking (unchanged)

No new ranking pass is introduced in this delivery. Sessions ride the
existing FTS+vec+RRF+rerank pipeline that artifacts already use. Same-session
near-duplicates collapse via `seen_sessions: set[str]` in `_search_sessions`
(TASK-5) — the chunk fetch is overfetched (`_SESSIONS_CHUNK_FETCH=30`) and
the dedup loop caps to `_SESSIONS_CHANNEL_CAP=3` distinct sessions. MMR and
recency-weight are deferred to the Backlog pending an eval signal — see
"Out of scope" and Backlog.

### ✓ DONE — TASK-4 — Bootstrap rewire

**File:** `co_cli/bootstrap/core.py`

Replace `init_session_store(deps, current_session_path, frontend)` with
`init_session_index(deps, current_session_path, frontend)`:

```python
def init_session_index(
    deps: CoDeps,
    current_session_path: Path,
    frontend: TerminalFrontend,
) -> None:
    """Sync past sessions into the unified chunks pipeline.

    Replaces the legacy SessionStore. The current session is excluded so
    the in-progress transcript is never indexed mid-session. On first run
    after migration, removes the obsolete session-index.db.
    """
    if deps.knowledge_store is None:
        frontend.on_status("  Session index unavailable — knowledge store missing")
        return
    try:
        legacy_db = deps.sessions_dir.parent / "session-index.db"
        if legacy_db.exists():
            try:
                legacy_db.unlink()
                logger.info("Removed legacy session-index.db (superseded by chunks pipeline)")
            except OSError as exc:
                logger.warning("Could not remove legacy session-index.db: %s", exc)
        deps.knowledge_store.sync_sessions(
            deps.sessions_dir, exclude=current_session_path
        )
    except Exception as exc:
        logger.warning("Session sync failed: %s", exc)
        frontend.on_status(f"  Session index sync failed — {exc}")
```

Update `co_cli/main.py:252` call site. Remove `SessionStore` field from
`CoDeps` (`co_cli/deps.py:200, 272`).

### ✓ DONE — TASK-5 — `_search_sessions` chunked rewrite

**File:** `co_cli/tools/memory/recall.py`

Replace the entire `_search_sessions` body and its helpers
(`_dedup_sessions`, `_prepare_tasks`, `_build_results_payload`):

```python
_SESSIONS_CHUNK_FETCH = 30
_SESSIONS_CHANNEL_CAP = 3
_SESSION_SUMMARY_PREVIEW_CHARS = 300  # retained for renderer

async def _search_sessions(
    ctx: RunContext[CoDeps],
    query: str,
    span: Span,
) -> list[dict]:
    """Chunked recall over indexed session transcripts."""
    if ctx.deps.knowledge_store is None:
        return []

    raw = ctx.deps.knowledge_store.search(
        query,
        source=["session"],
        limit=_SESSIONS_CHUNK_FETCH,
    )
    out: list[dict] = []
    seen_sessions: set[str] = set()
    for r in raw:
        if r.path in seen_sessions:
            continue
        seen_sessions.add(r.path)
        out.append({
            "channel": "sessions",
            "session_id": r.path,
            "when": (r.created or "")[:10],
            "source": r.source,
            "chunk_text": r.snippet or "",
            "start_line": r.start_line,
            "end_line": r.end_line,
            "score": r.score,
        })
        if len(out) >= _SESSIONS_CHANNEL_CAP:
            break
    span.set_attribute("memory.sessions.count", len(out))
    return out
```

Update the renderer at `recall.py:373-385`:

```python
if session_results:
    lines.append("\n**Past sessions:**")
    for idx, entry in enumerate(session_results, 1):
        preview = (entry.get("chunk_text") or "")[:_SESSION_SUMMARY_PREVIEW_CHARS]
        line_range = ""
        if entry.get("start_line") and entry.get("end_line"):
            line_range = f" @ L{entry['start_line']}–{entry['end_line']}"
        lines.append(
            f"  {idx}. [{entry['when']}] {entry['session_id']}{line_range}\n     {preview}"
        )
```

Drop these top-of-file imports (orphaned after rewrite):
- `from co_cli.memory.session_store import SessionSearchResult`
- `from co_cli.memory.summary import ...` (entire line)
- `from co_cli.memory.transcript import load_transcript`
- `from pathlib import Path` (if unused after `_dedup_sessions` removal)

Drop these constants from `recall.py:30-42`: `_SUMMARIZATION_TIMEOUT_SECS`,
`_SESSION_FALLBACK_PREVIEW_CHARS`. Keep `_SESSIONS_CHANNEL_CAP` and
`_SESSION_SUMMARY_PREVIEW_CHARS` — they remain recall-policy constants
independent of the retrieval mechanism (channel cap and renderer width).

`memory_search` docstring updated: session-result fields are now
`channel, session_id, when, source, chunk_text, start_line, end_line, score`
(was `summary`).

### ✓ DONE — TASK-6 — `memory_read_session_turn` tool

**File:** `co_cli/tools/memory/read.py`

```python
_SESSION_TURN_MAX_LINES = 200
_SESSION_TURN_MAX_BYTES = 16 * 1024

@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_read_only=True,
    is_concurrent_safe=True,
)
async def memory_read_session_turn(
    ctx: RunContext[CoDeps],
    session_id: str,
    start_line: int,
    end_line: int,
) -> ToolReturn:
    """Read verbatim turns from a past session by JSONL line range.

    Use after memory_search returns a session chunk hit when you need the
    exact turn content — commands, file paths, error messages, tool args —
    rather than the chunk-level snippet. Line numbers are 1-indexed JSONL
    lines as reported in the search hit's start_line/end_line.

    Refuses ranges over 200 lines or content over 16KB to keep context tight.

    Returns: {session_id, lines: [...], truncated: bool}
        lines[i] = {line, role, content_preview, tool_name|None}
    """
```

Implementation: locate JSONL via `parse_session_filename` over
`deps.sessions_dir.glob("*.jsonl")` (uuid8 match); read the requested line
range; parse each line; emit one entry per (line, role, content) tuple
extracted via `extract_messages`'s line-walking (reuse, don't duplicate).
Bounds-check: `start_line < 1` or `end_line < start_line` returns a
`ToolReturn` with an error string and empty lines list (NOT an exception).

### ✓ DONE — TASK-7 — Delete legacy code

**Files removed:**
- `co_cli/memory/session_store.py`
- `co_cli/memory/prompts/session_summarizer.md`
- `tests/memory/test_memory_index.py`
- `tests/memory/test_session_summary.py`
- `tests/memory/test_truncate_around_matches.py`
- `tests/memory/test_format_conversation.py` (only if no caller outside
  `summary.py` exists; verify with grep before delete)

**Files edited (delete only the named symbols):**
- `co_cli/memory/summary.py` — delete `summarize_session_around_query`,
  `_truncate_around_matches`, `_format_conversation`, `_render_user_prompt`,
  `_render_tool_return`, `_find_match_positions`, `_best_window_start`,
  `MAX_SESSION_CHARS`, `_TOOL_OUTPUT_*`, `_SESSION_*` constants. If the
  file becomes empty, delete it. (`co_cli/memory/dream.py` may still import
  some helpers — grep before deleting; relocate any survivor to
  `co_cli/memory/_window.py`.)
- `co_cli/deps.py:25, 200, 272` — drop `SessionStore` import, field, fork-copy.
- `co_cli/main.py:17, 252` — switch `init_session_store` →
  `init_session_index` import + call site.
- `tests/memory/test_session_search_tool.py` — rewrite assertions against
  the chunked path (chunk_text/start_line/end_line; no LLM call recorded).

**Verification:** after this task, `grep -rn "SessionStore\|messages_fts\|session_store\|summarize_session_around_query\|_SUMMARIZATION_TIMEOUT_SECS" co_cli/ tests/` returns only references in this plan file or in
`docs/specs/memory.md` (the spec is updated post-delivery via `/sync-doc`,
not here).

### ✓ DONE — TASK-8 — Config additions

**File:** `co_cli/config/knowledge.py`

```python
session_chunk_tokens: int = Field(default=400, ge=64)
session_chunk_overlap: int = Field(default=80, ge=0)
```

Add corresponding entries to `KNOWLEDGE_ENV_MAP`. Defaults are conservative:
40 % smaller chunks than artifacts (transcripts are denser than markdown
prose). MMR lambda and recency half-life are not introduced here — those
ranking knobs land with the future MMR/recency follow-up plan, not this
delivery.

**Wiring path.** TASK-2 ships `co_cli/memory/session_chunker.py` with
module-level constants `SESSION_CHUNK_TOKENS = 400`, `SESSION_CHUNK_OVERLAP = 80`
that `chunk_session` reads as defaults. TASK-3 calls `chunk_session` with
those defaults. TASK-8 then reads the config in `KnowledgeStore.__init__`,
stores `self._session_chunk_tokens` / `self._session_chunk_overlap`, and
modifies `index_session` to call
`chunk_session(p, chunk_tokens=self._session_chunk_tokens, overlap_tokens=self._session_chunk_overlap)`.
Chunker stays pure — no config import there.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Extend `extract_messages` to emit tool turns

**files:**
- `co_cli/memory/indexer.py`
- `tests/memory/test_indexer_extract.py` (new — replaces a slice of the
  retired `test_memory_index.py`)

**done_when:**
```
uv run pytest tests/memory/test_indexer_extract.py -x -q 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task1.log
```
exits 0 with tests asserting:
- A real fixture session JSONL containing at least one user-prompt, one
  assistant text, one tool-call, and one tool-return — committed at
  `tests/memory/fixtures/session_with_tool_turns.jsonl` (copy a known-rich
  session from `~/.co-cli/sessions/` and trim) — produces at least one
  `ExtractedMessage` per kind (assert
  `set(m.role for m in msgs) >= {"user", "assistant", "tool-call", "tool-return"}`).
- `tool_name` is populated (non-None) on every tool-call/tool-return
  message and None on every user/assistant message.
- `thinking`, `system-prompt`, `retry-prompt` parts produce zero
  `ExtractedMessage` entries.
- `compact_boundary` and `session_meta` top-level dict lines produce zero
  entries.
- Empty/whitespace-only content produces zero entries.
- `line_index` is the 0-indexed JSONL line number; `part_index` is the
  position within `parts`.

**success_signal:** Calling `extract_messages(session_jsonl)` on a real
co-cli session returns turns covering user prompts, assistant text, and
tool invocations with tool names — verifiable by manual inspection of the
returned list against `grep -n` on the JSONL.

### ✓ DONE — TASK-2 — `co_cli/memory/session_chunker.py`

**files:**
- `co_cli/memory/session_chunker.py` (new)
- `tests/memory/test_session_chunker.py` (new)

**done_when:**
```
uv run pytest tests/memory/test_session_chunker.py -x -q 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task2.log
```
exits 0 with tests asserting:
- `flatten_session` emits `User: …`, `Assistant: …`, `Tool[<name>](call)`,
  `Tool[<name>](return): …` lines for the corresponding `role` values, and
  the returned `line_map` is monotone non-decreasing.
- Long content (≥ 800 chars) wraps onto multiple lines preserving the role
  prefix and replicating the line-map entry.
- Heartbeat assistant messages (≤10 chars, no following tool-call) are
  dropped.
- Tool-return content shorter than 10 chars is dropped.
- `chunk_flattened` produces chunks of ~400 token-equivalent (assert each
  `len(chunk.text) // 4` is between 320 and 480 inclusive, except possibly
  the last) with 80-token overlap (assert non-empty content overlap between
  consecutive chunks).
- Per-chunk `start_jsonl_line`/`end_jsonl_line` come from the line_map and
  are 1-indexed (assert `start >= 1` and `end >= start` for every chunk).
- `chunk_session` is deterministic: two calls on an unchanged JSONL return
  lists with byte-equal `text`, `start_jsonl_line`, `end_jsonl_line`.
- `chunk_session` on a real fixture session JSONL produces ≥1 chunk whose
  bounds, when looked up via `linecache`, contain `Tool[bash](return)` or a
  `User:` / `Assistant:` line — proves the bounds are real and not synthetic.

**success_signal:** N/A (pure helper module).

**prerequisites:** [TASK-1]

### ✓ DONE — TASK-3 — `KnowledgeStore.index_session` + sync_sessions

**files:**
- `co_cli/memory/knowledge_store.py`
- `tests/memory/test_knowledge_store_sessions.py` (new — additive, doesn't
  touch existing `test_knowledge_store.py`)

**done_when:**
```
uv run pytest tests/memory/test_knowledge_store_sessions.py tests/memory/test_knowledge_store.py -x -q 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task3.log
```
exits 0 with tests asserting:
- `index_session(p)` writes one `docs` row with
  `(source='session', path=uuid8, kind='session', created=<ISO>)` —
  assert via direct SQL.
- `index_session(p)` writes ≥1 rows in `chunks` with
  `(source='session', doc_path=uuid8)`, monotone `chunk_index`, and
  `start_line`/`end_line` ≥ 1.
- **Idempotency on unchanged content.** Calling `index_session(p)` twice
  on the same file produces no new embedding writes and no chunk row
  rewrites on the second call. Implementation (real-dependency only —
  no monkeypatch, no attribute reassignment): snapshot
  `SELECT COUNT(*) FROM embedding_cache` and
  `SELECT COUNT(*) FROM chunks WHERE source='session' AND doc_path=?` and
  the max `rowid` in `chunks` for that doc_path before the second call;
  call `index_session(p)` again; assert both counts are unchanged AND
  the max rowid is unchanged (a re-insert would bump rowid even if count
  is the same).
- **Partial-write recovery.** Pre-seed the docs row for a session
  (call `index_session` once, then manually `DELETE FROM chunks WHERE
  source='session' AND doc_path=?` to simulate a crash between `index()`
  and `index_chunks()`), then call `index_session(p)` again. Assert
  chunks are re-populated (`SELECT COUNT(*) FROM chunks ...) > 0`)
  even though the docs hash is unchanged.
- `index_session(p)` after the file grows replaces all chunks (assert
  `chunk_index` 0..N for new content; old chunk_index entries gone).
- `sync_sessions(dir, exclude=p)` indexes every other session in `dir` and
  skips `p` (assert no `docs` row for `p`).
- `KnowledgeStore.search(query, source=['session'])` returns only session
  rows (assert `all(r.source == 'session' for r in results)`).
- `KnowledgeStore.search(query, source='knowledge')` returns no session
  rows when sessions are indexed (assert `all(r.source != 'session' …)`).
- Artifact-only search: results are byte-identical between
  before-sessions-indexed and after-sessions-indexed runs on the same
  artifact corpus and same query — assert by deep-equal on the `SearchResult`
  list.

**success_signal:** Indexing a fixture session and querying via
`KnowledgeStore.search("<phrase>", source=['session'])` returns chunk-level
hits whose `start_line`/`end_line` cite verbatim transcript turns
(verifiable via `linecache.getline(jsonl_path, start_line)`).

**prerequisites:** [TASK-2]

### ✓ DONE — TASK-4 — Bootstrap rewire

**files:**
- `co_cli/bootstrap/core.py`
- `co_cli/main.py`
- `co_cli/deps.py`
- `tests/bootstrap/test_init_session_index.py` (new)

**done_when:**
```
uv run pytest tests/bootstrap/test_init_session_index.py -x -q 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task4.log
```
exits 0 with tests asserting:
- After `init_session_index(deps, current, frontend)`, every JSONL in
  `deps.sessions_dir` except `current` produces a `docs` row with
  `source='session'` (count via `KnowledgeStore._conn.execute(SELECT COUNT…)`).
- A pre-existing `~/.co-cli/session-index.db` (write a small empty SQLite
  file at the expected path under `tmp_path`) is unlinked after
  `init_session_index` runs.
- A second call to `init_session_index` on the same dir does not re-embed.
  Real-dependency check: snapshot `SELECT COUNT(*) FROM embedding_cache`
  before the second call; assert the count is unchanged after.
- `CoDeps` no longer has a `session_store` field — assert
  `"session_store" not in {f.name for f in dataclasses.fields(CoDeps)}`.

**success_signal:** `co chat` startup logs "  Session index synced — N
sessions" (or similar) and no longer reports "Session index unavailable"
when the knowledge store is healthy. Verifiable via
`uv run co chat --probe-only` (or the equivalent existing dry-run path) —
fall back to `init_session_index` direct call in test if no CLI dry-run.

**prerequisites:** [TASK-3]

### ✓ DONE — TASK-5 — `_search_sessions` chunked rewrite + renderer

**files:**
- `co_cli/tools/memory/recall.py`
- `tests/memory/test_session_search_tool.py` (rewritten in place)

**done_when:**
```
uv run pytest tests/memory/test_session_search_tool.py -x -q 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task5.log
```
exits 0 with tests asserting:
- `memory_search("<phrase from a fixture session>")` returns a result list
  with at least one `channel='sessions'` entry carrying `chunk_text`,
  `start_line`, `end_line`, and `score` fields, and **no** `summary` field.
- The same call records `memory.sessions.count` on the active span (read
  via the existing test span recorder pattern in this file).
- No LLM call is reachable on the recall path. Two-layer real-dependency
  check: (a) the symbol-absence gate at TASK-7 done_when proves
  `summarize_session_around_query` is not importable from co_cli/, so the
  prior LLM hop cannot be invoked; (b) on the active span recorder
  (existing pattern in this test file), assert that no child span with
  name matching `^pydantic_ai\.|llm\.call` is emitted under the
  `memory_search` span for the test query. No attribute reassignment, no
  monkeypatching.
- The rendered `tool_output.display` for a session hit contains a single
  line of the form `[YYYY-MM-DD] <uuid8> @ L<start>–<end>` followed by a
  preview line — assert via regex match.
- Sessions cap: 5 matching chunks across 5 distinct sessions returns
  exactly 3 entries (cap = 3); 5 matching chunks across 1 session returns
  exactly 1 entry (dedup).
- `memory_search("")` (empty-query browse) is unchanged — same list shape
  as before this delivery (deep-equal against the expected payload built
  from `list_sessions`).

**success_signal:** On a hybrid-configured stack with 50+ indexed sessions,
`memory_search "<phrase only present in past sessions>"` returns within
1.0s wall-clock — measurable via `time.perf_counter` in a smoke eval added
to `evals/eval_session_recall.py` (eval, not test — does not gate this task,
but documented in success_signal).

**prerequisites:** [TASK-3, TASK-4]

### ✓ DONE — TASK-6 — `memory_read_session_turn` tool

**files:**
- `co_cli/tools/memory/read.py`
- `tests/memory/test_read_session_turn.py` (new)

**done_when:**
```
uv run pytest tests/memory/test_read_session_turn.py -x -q 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task6.log
```
exits 0 with tests asserting:
- For a real fixture session, calling
  `memory_read_session_turn(session_id=uuid8, start_line=K, end_line=K+5)`
  returns a payload whose `lines` list has length ≤ 6 with one entry per
  retained line, each carrying `line` (matching the requested range),
  `role`, `content_preview`, and `tool_name`.
- `end_line - start_line + 1 > 200` returns `truncated=True` with exactly
  200 lines.
- A range whose total content exceeds 16KB returns `truncated=True` with
  the byte budget respected.
- Unknown `session_id` returns a `ToolReturn` whose `display` contains an
  error message (no exception raised).
- `start_line < 1` or `end_line < start_line` returns a `ToolReturn` whose
  `display` contains a validation error.

**success_signal:** After a `memory_search` chunk hit, the agent can call
`memory_read_session_turn(uuid8, start_line, end_line)` and receive the
verbatim turns that the chunk covered.

**prerequisites:** [TASK-3]

### ✓ DONE — TASK-7 — Delete legacy code

**files:**
- (removed) `co_cli/memory/session_store.py`
- (removed) `co_cli/memory/prompts/session_summarizer.md`
- (removed) `tests/memory/test_memory_index.py`
- (removed) `tests/memory/test_session_summary.py`
- (removed) `tests/memory/test_truncate_around_matches.py`
- (conditionally removed) `tests/memory/test_format_conversation.py`
- `co_cli/memory/summary.py` (edits — see High-Level Design)
- `co_cli/deps.py`
- `co_cli/main.py`
- `co_cli/tools/memory/recall.py` (orphaned-import cleanup only — primary
  edits in TASK-5)

**done_when:**
```
grep -rn "SessionStore\|messages_fts\|session_store\|summarize_session_around_query\|_SUMMARIZATION_TIMEOUT_SECS\|_SESSION_FALLBACK_PREVIEW_CHARS\|_truncate_around_matches\|_format_conversation\|MAX_SESSION_CHARS\|session_summarizer" co_cli/ tests/ 2>&1 | grep -v __pycache__ | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task7-grep.log
```
returns zero lines, AND
```
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task7-full.log
```
exits 0 — full suite green after the deletes.

**success_signal:** `~/.co-cli/session-index.db` is removed on the next
`co chat` startup (smoke-checked in TASK-4 already; this task verifies the
codebase no longer references the symbols).

**prerequisites:** [TASK-3, TASK-4, TASK-5, TASK-6]

### ✓ DONE — TASK-8 — Config additions

**files:**
- `co_cli/config/knowledge.py`
- `tests/config/test_knowledge_settings.py` (additive)

**done_when:**
```
uv run pytest tests/config/test_knowledge_settings.py -x -q 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task8.log
```
exits 0 with tests asserting:
- `KnowledgeSettings()` exposes `session_chunk_tokens=400` and
  `session_chunk_overlap=80` by default.
- Each new field round-trips through its `KNOWLEDGE_ENV_MAP` env var
  (set env, build settings, assert overridden value).
- `extra='forbid'` rejects a typo like `session_chuck_tokens` with
  `ValidationError`.
- Bound checks: `session_chunk_tokens=32` raises (`ge=64`);
  `session_chunk_overlap=-1` raises (`ge=0`).
- **Propagation.** Set `CO_KNOWLEDGE_SESSION_CHUNK_TOKENS=256`, build a
  `Settings`, build a `KnowledgeStore` from it, and assert two-layer:
  (a) config-readback — `store._session_chunk_tokens == 256` (proves the
  store actually saw the override); (b) call `index_session(p)` on a
  fixture session and assert that the longest resulting chunk has
  `len(chunk.content) // 4 <= 256 * 1.2` (allow 20 % slack for
  token-estimate variance). Both assertions together prove the env value
  reaches the chunker — the readback alone catches `min(env, default)`
  bugs that the size-only assertion would silently pass.

**success_signal:** A user can tune session chunking via `settings.json`
(e.g. shrink chunks to 256 tokens for a smaller embedding model or grow to
600 to match artifacts) and observe the effect at next bootstrap.

**prerequisites:** [TASK-3]

---

## Testing

All changes are unit/integration tested under `tests/memory/`,
`tests/bootstrap/`, and `tests/config/` per the file lists above. Real
fixtures, no mocks (per `agent_docs/testing.md`). The fixtures already used
by the retired session search tests
(`tests/memory/test_session_search_tool.py` reuses real
`~/.co-cli/sessions/`-style JSONLs at `tmp_path`) are reused here — keep
the same generator pattern, just change the assertions.

For tests that need hybrid backend (vec + cross-encoder), follow the
existing `tests/memory/test_knowledge_store.py` pattern: skip with
`pytest.importorskip("sqlite_vec")` when the extension cannot be loaded.
TEI reachability is similarly guarded by the existing
`_discover_knowledge_backend` degradation path; the FTS5-only fallback is
itself a tested path in this delivery.

For TASK-5 "no LLM call on recall" assertion, prefer counter-wrapper over
`deps.model.request` over wall-clock timing — deterministic and immune to
host load.

Full-suite gate after all tasks land:
```
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full-port.log
```

---

## Migration & Rollout

Single-shot, no phases:

1. Ship TASK-1..8 atomically. The first `co chat` after the cutover:
   - Constructs `KnowledgeStore` (existing behavior).
   - `init_session_index` runs `KnowledgeStore.sync_sessions(sessions_dir,
     exclude=current)` — backfills every historical session into `chunks`.
   - `init_session_index` unlinks `~/.co-cli/session-index.db` on success.
2. No backfill script — `sync_sessions` is the backfill.
3. No dual-write phase — single path from cutover.

**Rollback (concrete, zero data loss).** Both `session-index.db` and the
session chunks in `co-cli-search.db` are *derived* artifacts; the
source of truth is the JSONL files under `~/.co-cli/sessions/`, which this
delivery never touches. If the merge needs to be reverted:

1. Revert the commit. `SessionStore` code reappears.
2. Next `co chat` startup: `init_session_store` (the old function) creates
   a fresh `~/.co-cli/session-index.db` and `sync_sessions` rebuilds it
   from the untouched JSONLs.
3. Session chunks left over in `co-cli-search.db` from the post-cutover
   period stay there but are never queried (the reverted `_search_sessions`
   doesn't pass `source=['session']`). They are dead rows; can be cleaned
   with `DELETE FROM chunks WHERE source='session'` if the operator cares.

No data is lost; no JSONL is mutated; rollback is one commit revert plus a
restart.

**Post-merge observation (no soak gate).** The cutover is atomic and
reversible. There is no soak window or trip signal — normal post-merge
observation of `memory.sessions.count` span attribute and end-to-end
recall latency is sufficient. Operators concerned about ranking quality
should run `evals/eval_session_recall.py` (added in this delivery as a
smoke for the success_signal in TASK-5) before and after the cutover.

---

## Open Questions

- **Q1: Per-chunk timestamp vs per-session created.** When recency weight
  is added in the future MMR/recency follow-up plan, it will start from
  `docs.created` (session-level). Long sessions spanning many days would
  benefit from per-chunk `earliest_msg_ts`. **Decision:** if/when recency
  weight ships, start session-level (no schema add). Revisit if recall@K
  eval shows recency-precision regressions on long sessions.

- **Q2: Tool-call argument indexing.** Currently `Tool[name](call)` lines
  drop the args (avoids leaking large blobs into the FTS index).
  Tool-return content IS indexed. **Decision:** ship without args. If
  recall on `bash <specific command>` queries underperforms, revisit by
  adding a sanitized arg snippet (head 100 chars).


---

## Risks

- **Local TEI not running on bootstrap.** `_discover_knowledge_backend`
  already handles this — degrades to FTS5-only mode. In FTS5 mode,
  `index_chunks` writes content + FTS but skips embeddings; session search
  still works (BM25 only, no vector recall). No new risk introduced.

- **Hybrid degradation does not propagate to `index_session`.**
  `index_chunks` already gates embed calls on `self._backend == 'hybrid'`
  (`knowledge_store.py:467`) — same machinery used by artifacts. Mitigation
  in place; verify in TASK-3 tests.

- **DB write contention on shared `co-cli-search.db`.** Bootstrap
  `sync_sessions` can take longer than the prior `SessionStore.sync_sessions`
  for first-time users (hundreds of sessions × N chunks × embedding cost).
  WAL is on. **Mitigation:** hash-skip avoids re-embedding unchanged
  sessions; sustained per-bootstrap cost is bounded by new/grown sessions.
  Worst-case first-run is one-time and visible.

- **Index size growth.** 1024-dim float32 = 4 KB per chunk; 10K session
  chunks = 40 MB. Acceptable. `PRAGMA optimize` and `VACUUM` are out of
  scope for this plan.

- **Sanitization rule drift.** Dropping signal-thresholded messages
  (`<= 10` chars) may evict short-but-meaningful turns ("yes",
  "approved"). **Mitigation:** drop is gated on heartbeat shape (no
  following tool-call); explicit confirmations still get indexed when
  followed by tool activity. If recall regresses on short confirmations,
  loosen the threshold.

- **Renderer regression.** Session result text format changes from a
  multi-line LLM summary block to a one-line citation + chunk preview.
  Prompts that relied on the verbose summary need to adapt. **Mitigation:**
  preview cap at 300 chars (existing `_SESSION_SUMMARY_PREVIEW_CHARS`)
  keeps total output length comparable.

- **Cross-turn synthesis loss within a single session.** Today's path can
  paraphrase across turns 5/12/30 of one session into a single summary;
  the chunked path returns one chunk neighborhood per session (top hit)
  and leaves cross-turn synthesis to the agent calling
  `memory_read_session_turn`. For "overview of this session" queries this
  is a real fidelity loss, accepted in exchange for determinism + drill
  -down + latency. **Mitigation:** `memory_read_session_turn` exists for
  this case; if recall on whole-session-overview queries regresses post
  -merge, the deferred Backlog item `memory_summarize_results`
  (LLM-compress retrieved chunks) is the natural follow-up.

- **`memory_read_session_turn` JSONL re-parse cost.** Reading a 200-line
  range from a 50K-line session re-parses 200 lines from disk. Acceptable —
  this is a drill-down tool, not a hot path.

---

## Backlog — Adjacent Items (Out of Scope)

- **Session ranking refinements (MMR + recency-weight).** Greedy MMR with
  same-session as similarity proxy + multiplicative recency-decay weight
  (30-day half-life). Gate per-row so artifact ordering is provably
  unchanged. Trigger: ship if a session recall@K eval shows
  near-duplicate dominance or recency-precision regression on real
  corpora. Tracks Q1 (per-chunk vs per-session timestamp) as a
  sub-decision. New config knobs at that time:
  `session_mmr_lambda`, `session_recency_half_life_days`.
- Per-personality session scoping (`personality` filter on chunks/docs).
- CJK trigram tokenizer sidecar.
- Live (active-session) search overlay.
- Per-chunk timestamp + per-chunk decay (Q1).
- `memory_summarize_results` LLM-compress tool (re-evaluate if context
  pressure shows up after the chunked path is in production).
- Tool-call argument indexing (Q2).

## Final — Team Lead

Plan approved. C3 stop conditions met: Core Dev `approve` / Blocking: none, PO `approve` / Blocking: none.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev port-openclaw-session-chunked-recall`

---

## Delivery Summary — 2026-04-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/memory/test_indexer_extract.py` exits 0 | ✓ pass |
| TASK-2 | `uv run pytest tests/memory/test_session_chunker.py` exits 0 | ✓ pass |
| TASK-3 | `uv run pytest tests/memory/test_knowledge_store_sessions.py` exits 0 | ✓ pass |
| TASK-4 | `uv run pytest tests/bootstrap/test_init_session_index.py` exits 0 | ✓ pass |
| TASK-5 | `uv run pytest tests/memory/test_session_search_tool.py` exits 0 | ✓ pass |
| TASK-6 | `uv run pytest tests/memory/test_read_session_turn.py` exits 0 | ✓ pass |
| TASK-7 | legacy symbol grep returns zero lines; full suite exits 0 | ✓ pass |
| TASK-8 | `uv run pytest tests/config/test_knowledge_settings.py` exits 0 | ✓ pass |

**Tests:** full suite — 827 passed, 0 failed (307s)
**Doc Sync:** deferred to /review-impl (no public API renames in this delivery)

**Overall: DELIVERED**
All 8 tasks shipped. Sessions now index into the unified chunks pipeline as `source='session'`; `memory_search` returns chunk citations with `start_line`/`end_line`/`score` instead of LLM summaries; legacy `SessionStore`, `session-index.db`, `session_summarizer.md`, and summarization helpers deleted. New `memory_read_session_turn` tool drills to verbatim turns. Config fields `session_chunk_tokens`/`session_chunk_overlap` added with env-var override support.
