# TODO: Openclaw Adoption Action Plan

Actionable improvements derived from studying openclaw's production patterns. Work is
ordered P1 → P3 with per-section checklists and verification steps.

**Source:** openclaw @ 33f30367e (pulled 2026-02-18).

**Coordination note — §4 `/new` command:**
§4 requires `_index_session_summary()` in `co_cli/_history.py` (session summary indexing,
currently deferred — no timeline). Do not start §4 until that infra exists.

**Detailed implementation tasks** for §1–§7 are in `docs/TODO-openclaw-adoption.md`.

---

## §1 — Shell Arg Validation on Auto-Approve `[P1]`

### Problem

`_is_safe_command()` in `co_cli/_approval.py` checks two things: (1) no shell chaining operators
(`;`, `&`, `|`, `>`, `<`, `` ` ``, `$(`, newline), and (2) command string starts with a prefix
from `shell_safe_commands`. Argument content is never inspected. `git diff --no-index /etc/passwd /dev/null`
passes the current check — `git diff` matches the prefix, no chaining operators are present, and
the path argument escapes unnoticed. The prefix list is a UX convenience, not a security boundary;
approval is the security boundary — but coarse arg bypass still narrows the effective approval gate.

### Fix

Extend `_is_safe_command()` with an arg-level validation pass that runs after the prefix match:

- Extract tokens after the matched prefix
- Reject any arg containing: glob chars `*?[]{}`, path separator `/` or `\`, path starters
  `./` `~/` `..`, or null bytes
- Single-letter flags (`-v`, `-n`) and `--word-flags` with no path chars: pass
- Keep the existing no-chaining check as the first gate (unchanged; fast path)

No changes to `safe_commands` list or config schema.

### Implementation

#### `co_cli/_approval.py`

- [ ] Add `_validate_args(args_str: str) -> bool` — returns True if args are safe, False if any
      arg contains glob chars, path chars, or null bytes
  - Split `args_str` on whitespace
  - For each token: reject if it contains `* ? [ ] { }`, starts with `/`, `./`, `~/`, `..`,
    contains `\`, or contains null byte `\x00`
  - `--flag` and `-f` style tokens with no path chars pass
- [ ] In `_is_safe_command()`, after the prefix match succeeds: extract the portion of `cmd`
      after the matched prefix; if non-empty, call `_validate_args(remainder)` and return its result
- [ ] If no prefix matches, return False unchanged

#### Tests — `tests/test_approval.py`

- [ ] `git diff --no-index /etc/passwd /dev/null` → rejected (path arg)
- [ ] `git diff HEAD~1` → approved (no path chars, `~1` is a rev not a tilde-path)
- [ ] `ls /etc` → rejected (path arg)
- [ ] `grep -r pattern` → rejected (`-r` is fine but use a case with a path to verify rejection)
- [ ] `git status --short` → approved (flag only, no path)
- [ ] `git diff` (no args) → approved
- [ ] `grep foo*` → rejected (glob char)
- [ ] `wc -l` → approved (flag only)
- [ ] `find . -name "*.py"` → rejected (path arg `.` and glob in arg)

### Verification

```bash
uv run pytest tests/test_approval.py -v
```

---

## §2 — Exec Approval Persistence `[P1]`

### Problem

Approval is per-session only. Every `uv run co chat` re-prompts for every command, including
ones the user approved in every previous session. `git status`, `pytest`, `uv run pytest` —
all require re-approval. There is no way to say "always allow this pattern" that survives a
restart. This is the highest daily friction point in the UX.

### Fix

Persist approved patterns to `.co-cli/exec-approvals.json` (mode 0o600). On approval prompt,
check persisted patterns first — if a match is found, auto-approve silently. When the user
chooses "a" (always), write the pattern to the file. Expose `/approvals list` and
`/approvals clear [id]` slash commands for management.

Pattern matching: `fnmatch.fnmatch(cmd, pattern)` — shell glob on the full command string.

**File schema:**
```json
{
  "approvals": [
    {
      "id": "uuid4-string",
      "pattern": "git status*",
      "created_at": "2026-02-28T12:00:00+00:00",
      "last_used_at": "2026-02-28T14:30:00+00:00",
      "last_used_command": "git status --short"
    }
  ]
}
```

### Implementation

#### `co_cli/_exec_approvals.py` (new file)

- [ ] `load_approvals(path: Path) -> list[dict]` — read and parse JSON; return `[]` on missing/corrupt
- [ ] `save_approvals(path: Path, approvals: list[dict]) -> None` — write JSON, create file with
      mode 0o600 on first write; subsequent writes preserve permissions
- [ ] `find_approved(cmd: str, approvals: list[dict]) -> dict | None` — return first approval
      where `fnmatch.fnmatch(cmd, entry["pattern"])` is True; return None if no match
- [ ] `add_approval(approvals: list[dict], pattern: str, cmd: str) -> list[dict]` — append new
      entry with `uuid4()` id, `created_at` now, `last_used_at` now, `last_used_command` cmd;
      return updated list
- [ ] `update_last_used(approvals: list[dict], approval_id: str, cmd: str) -> list[dict]` — set
      `last_used_at` now and `last_used_command` on the matching entry; return updated list
- [ ] `prune_stale(approvals: list[dict], max_age_days: int = 90) -> list[dict]` — remove entries
      where `last_used_at` is older than `max_age_days`; return pruned list

#### `co_cli/_orchestrate.py`

- [ ] At the start of the tool approval check: call `find_approved(cmd, load_approvals(deps.exec_approvals_path))`
- [ ] If found: auto-approve, call `update_last_used()` + `save_approvals()`; skip user prompt
- [ ] On user choosing "a" (always): call `add_approval()` + `save_approvals()` with the full
      command as both the pattern and `last_used_command`; show confirmation
  - Note: prompt the user for an optional pattern (default: exact command) — e.g. "git status*"
    is more useful than "git status --short" as a persisted pattern. If the user just presses
    enter, use the exact command string

#### `co_cli/deps.py`

- [ ] Add `exec_approvals_path: Path = field(default_factory=lambda: Path.cwd() / ".co-cli/exec-approvals.json")`

#### `co_cli/config.py`

- [ ] No new field needed — `exec_approvals_path` is a derived path, not user-configurable.
      If future use cases require overriding, add `exec_approvals_path: str | None = None` then.

#### `co_cli/main.py`

- [ ] Pass `exec_approvals_path=Path.cwd() / ".co-cli/exec-approvals.json"` into `CoDeps`
      in `create_deps()`

#### `co_cli/_commands.py`

- [ ] Add `_cmd_approvals(ctx, args)` handler — subcommands: `list` and `clear [id]`
  - `list`: load and display all approvals (id, pattern, last used, last command)
  - `clear` with no id: confirm then wipe all approvals
  - `clear <id>`: remove entry with matching UUID prefix (first 8 chars is enough for UX)
- [ ] Register as `SlashCommand("approvals", "Manage persistent exec approvals", _cmd_approvals)`
      in `COMMANDS`

#### Tests — `tests/test_exec_approvals.py` (new file)

- [ ] `add_approval()` then `find_approved()` with exact command → match
- [ ] `find_approved()` with glob pattern `"git *"` matches `"git status"` and `"git diff HEAD"`
- [ ] `find_approved()` with no matching pattern → None
- [ ] `prune_stale()` removes entries older than `max_age_days`; keeps recent entries
- [ ] `save_approvals()` + `load_approvals()` round-trip: data intact, file mode 0o600
- [ ] `update_last_used()` updates timestamp and last command on correct entry

### Verification

```bash
uv run pytest tests/test_exec_approvals.py -v
uv run co chat
# 1. Run a shell command that requires approval
# 2. Choose "a" (always) — confirm .co-cli/exec-approvals.json is written
# 3. Exit and restart: co chat
# 4. Run the same command — confirm no approval prompt appears
```

---

## §3 — Temporal Decay Scoring on Retrieval `[P2]`

### Problem

FTS5 BM25 score is the only ranking signal in `recall_memory`. A memory written 6 months ago
competes equally with one written yesterday for the same query. `_touch_memory()` refreshes
`updated` on recall (gravity), but this only affects future grep-path ordering — it has no effect
on FTS ranking scores. Stale preferences and superseded decisions rank the same as today's
corrections.

### Fix

Apply an exponential decay multiplier to BM25 scores post-retrieval, before dedup and display.
Formula: `final_score = bm25_score * exp(-ln(2) / half_life_days * age_days)`
Default half-life: 30 days (configurable). Items with `decay_protected: true` bypass decay —
they are evergreen. Applied only on the FTS path; grep path uses recency-sort already.

### Implementation

#### `co_cli/tools/memory.py`

- [ ] Add `_decay_multiplier(updated_iso: str, half_life_days: int) -> float`:
  - Parse `updated_iso` to datetime (use `_parse_created` helper, same ISO8601 format)
  - Compute `age_days = (now - updated).total_seconds() / 86400`
  - Return `math.exp(-math.log(2) / half_life_days * age_days)`
  - Clamp to `[0.0, 1.0]` — decay never amplifies scores
- [ ] In `recall_memory()`, FTS path: after `fts_results` are returned and `matches` list is built,
      apply decay to reorder:
  - For each match, compute `_decay_multiplier(m.updated or m.created, ctx.deps.memory_recall_half_life_days)`
  - Assign a decay-adjusted rank: lower rank = higher decayed score
  - Re-sort `matches` by decayed score descending before slicing to `max_results`
  - Skip decay for entries where `m.decay_protected is True`
- [ ] Import `math` at top of file (stdlib, no new dep)

#### `co_cli/config.py`

- [ ] Add `memory_recall_half_life_days: int = Field(default=30, ge=1)` to `Settings`
- [ ] Add to `env_map`: `"memory_recall_half_life_days": "CO_MEMORY_RECALL_HALF_LIFE_DAYS"`

#### `co_cli/deps.py`

- [ ] Add `memory_recall_half_life_days: int = 30`

#### `co_cli/main.py`

- [ ] Pass `memory_recall_half_life_days=settings.memory_recall_half_life_days` into `CoDeps`
      in `create_deps()`

#### Tests — `tests/test_memory_decay.py`

- [ ] Two memories with identical content but different `updated`: the newer one ranks first
      after decay scoring
- [ ] A memory with `decay_protected: true` has the same effective score regardless of age
      (decay multiplier not applied)
- [ ] `_decay_multiplier` with `age_days=0` returns 1.0 (no decay at creation)
- [ ] `_decay_multiplier` with `age_days == half_life_days` returns approximately 0.5
- [ ] `_decay_multiplier` never returns > 1.0 regardless of input

### Verification

```bash
uv run pytest tests/test_memory_decay.py -v
```

---

## §4 — `/new` Slash Command `[P2, blocked on letta §4]`

### Dependency

**Do not start until `_index_session_summary()` exists in `co_cli/_history.py`.** That function (session summary indexing at compaction time) is currently deferred — no timeline. The `/new` command reuses that infra — no parallel LLM call machinery needed.

### Problem

No explicit session-close hook exists. Conversation knowledge is ephemeral — a good exchange
that yielded a decision or preference is lost when the session ends. `/new` covers the active
user-intent path: "I want to checkpoint this session now and start fresh."

### Fix

`/new` summarizes the last N messages (up to 15) into a dated knowledge file via LLM, writes
it to `.co-cli/knowledge/session-{timestamp}.md`, then clears history and resets the session
(new context window). Uses `summarize_messages()` + `_index_session_summary()` from `co_cli/_history.py`.

### Implementation

#### `co_cli/_commands.py`

- [ ] Add `_cmd_new(ctx, args)`:
  - If `ctx.message_history` is empty: print `[dim]Nothing to checkpoint — history is empty.[/dim]`
    and return None (no-op)
  - Take last min(15, len(history)) messages
  - Call `_run_summarization_with_policy(recent_msgs, model)` from `co_cli/_history.py`
  - If summary is None: print failure message, do not clear history
  - If summary succeeds: call `_index_session_summary(ctx.deps, summary, len(recent_msgs))`; await it
  - Clear history: return `[]`
  - Print confirmation: `[success]Session checkpointed and history cleared.[/success]`
- [ ] Register `SlashCommand("new", "Checkpoint session to memory and start fresh", _cmd_new)`
      in `COMMANDS`
- [ ] Import `_run_summarization_with_policy` from `co_cli._history` inside the handler
      (lazy import to avoid circular; same pattern as `_cmd_compact`)
- [ ] Import `_index_session_summary` from `co_cli._history` inside the handler

#### Tests — (add to appropriate test file after `_index_session_summary` ships)

- [ ] `/new` with history: writes a `session-*.md` file in `.co-cli/knowledge/` with
      `provenance: session` frontmatter and indexes it; returns `[]` (clears history)
- [ ] `/new` with empty history: no file written, returns None (history unchanged)

### Verification

```bash
uv run co chat
# Run some turns, then: /new
# Confirm session-*.md written in .co-cli/knowledge/
# Confirm history is cleared (next turn starts fresh)
```

---

## §5 — Model Fallback `[P2]`

### Problem

A single model is configured per provider. If the API is rate-limited, down, or the model
is unavailable, the entire session fails with no recovery path. The user must restart and
manually switch.

### Fix

`fallback_models: list[str]` in `Settings`. In the agent run, on a provider error
(`ModelRetry` or HTTP 429/503), rebuild the agent with the next model in the list and retry.
No cooldown or probe logic for MVP — simple sequential fallback is sufficient.

### Implementation

#### `co_cli/config.py`

- [ ] Add `fallback_models: list[str] = Field(default_factory=list)` to `Settings`
- [ ] Add to `env_map`: `"fallback_models": "CO_FALLBACK_MODELS"` with comma-split validator
      (same pattern as `shell_safe_commands` / `_parse_safe_commands`)

#### `co_cli/deps.py`

- [ ] Add `fallback_models: list[str] = field(default_factory=list)`

#### `co_cli/main.py`

- [ ] Pass `fallback_models=settings.fallback_models` into `CoDeps` in `create_deps()`

#### `co_cli/agent.py`

- [ ] In `get_agent()` or the agent run wrapper in `_orchestrate.py`: wrap the agent run in
      a retry loop over `deps.fallback_models`
  - On `ModelRetry` or provider HTTP error: log the switch with `logger.warning()`; rebuild
    agent model to the next fallback; retry the same turn
  - If all fallbacks exhausted: re-raise the original exception
  - Context overflow errors: do NOT retry (inner compaction handles those — a different model
    won't help)

#### Tests — `tests/test_agent.py`

- [ ] Verify `fallback_models` is a field in `CoDeps` with correct type and default
- [ ] Verify `fallback_models` is passed from `create_deps()` (no mock needed — inspect `CoDeps`)

### Verification

```bash
uv run pytest tests/test_agent.py -v
# Manual: set fallback_models to a known-good model in settings.json,
# then trigger a provider error — confirm session switches to fallback instead of crashing
```

---

## §6 — Session Persistence `[P2]`

### Problem

`uv run co chat` always starts with a blank session. Compaction count, model used last session,
and the session UUID are lost between invocations. Multi-turn memory across restarts relies
entirely on the memory tools. There is no session continuity — not even metadata — making
features like cron scheduling (§13) impossible to implement cleanly.

### Fix

Write `.co-cli/session.json` (mode 0o600) with a lightweight metadata record on each turn.
Restore on startup if the session is still fresh (within `session_ttl_minutes`). Conversation
history is NOT restored (memory tools handle cross-session knowledge); only metadata is restored.
This is the foundation required by §13 (cron scheduling).

**File schema:**
```json
{
  "session_id": "uuid4-hex-string",
  "model": "gemini-2.0-flash",
  "last_used_at": "2026-02-28T14:30:00+00:00",
  "compaction_count": 0
}
```

**Freshness policy:** if `last_used_at` is older than `session_ttl_minutes` (default 60),
start a new session (new UUID, reset compaction_count). Within the TTL, restore last session
metadata only.

### Implementation

#### `co_cli/_session.py` (new file)

- [ ] `load_session(path: Path) -> dict | None` — parse JSON; return None on missing/corrupt
- [ ] `save_session(path: Path, session: dict) -> None` — write JSON with mode 0o600 on first write
- [ ] `is_fresh(session: dict, ttl_minutes: int) -> bool` — return True if `last_used_at` is
      within the TTL window; False if expired or field missing
- [ ] `new_session(model: str) -> dict` — return a new session dict with `uuid4().hex` id,
      given model, `last_used_at` now, `compaction_count=0`
- [ ] `touch_session(session: dict) -> dict` — update `last_used_at` to now; return updated dict

#### `co_cli/main.py`

- [ ] At `chat_loop()` startup: call `load_session(session_path)`. If session is not None and
      `is_fresh(session, settings.session_ttl_minutes)`: use `session["session_id"]` as
      `deps.session_id`. Else: call `new_session(model_name)` and save immediately.
- [ ] After each turn: call `touch_session(session)` + `save_session(session_path, session)`.
      Update `compaction_count` when compaction fires.
- [ ] `session_path = Path.cwd() / ".co-cli/session.json"`

#### `co_cli/deps.py`

- [ ] `session_id: str = ""` already exists — no change to field type or default

#### `co_cli/config.py`

- [ ] Add `session_ttl_minutes: int = Field(default=60, ge=1)` to `Settings`
- [ ] Add to `env_map`: `"session_ttl_minutes": "CO_SESSION_TTL_MINUTES"`

#### Tests — `tests/test_session.py` (new file)

- [ ] `new_session()` returns dict with all required keys and valid UUID hex
- [ ] `is_fresh()` returns True for recent `last_used_at` within TTL
- [ ] `is_fresh()` returns False for `last_used_at` older than TTL
- [ ] `is_fresh()` returns False when session is None (guard)
- [ ] `save_session()` + `load_session()` round-trip: data intact, file mode 0o600
- [ ] `touch_session()` updates `last_used_at` to a newer value

### Verification

```bash
uv run pytest tests/test_session.py -v
uv run co chat
# Exit and restart within TTL — session_id in logs should be the same
# Wait past TTL — session_id should be different
```

---

## §7 — Doctor Security Checks `[P2]`

### Problem

`co status` checks LLM connectivity and tool availability but has no security posture checks.
A `settings.json` with API keys that is world-readable (0o644 instead of 0o600) will not be
flagged. An exec approvals file with a catch-all `*` pattern (approves every command without
prompting) will not be flagged. No automated way to detect these misconfigurations before they
cause a problem.

### Fix

Add 3 security checks to `get_status()` output via a new `check_security()` function. Minimal,
high-signal checks only for MVP. Display findings in `co status` output under a "Security" section.
Also surface from `co status` slash command in the REPL.

**Checks:**
1. `~/.config/co-cli/settings.json` permissions: warn if world-readable (mode & 0o004)
2. `.co-cli/settings.json` project config: same check
3. Exec approvals list: warn if any pattern is `*` (approves all commands)

### Implementation

#### `co_cli/status.py`

- [ ] Add `SecurityFinding` dataclass:
  ```python
  @dataclass
  class SecurityFinding:
      severity: str  # "warn" | "error"
      check_id: str  # e.g. "config-world-readable"
      detail: str    # human-readable description
      remediation: str  # what to do
  ```
- [ ] Add `check_security() -> list[SecurityFinding]`:
  - Check `SETTINGS_FILE` (`~/.config/co-cli/settings.json`): if exists and
    `stat().st_mode & 0o004`, append `SecurityFinding(severity="warn", check_id="user-config-world-readable", ...)`
  - Check `Path.cwd() / ".co-cli/settings.json"`: same permission check
  - Check `Path.cwd() / ".co-cli/exec-approvals.json"`: if exists, load with
    `_exec_approvals.load_approvals()`; if any entry has `pattern == "*"`, append
    `SecurityFinding(severity="warn", check_id="exec-approval-wildcard", ...)`
  - Return list (empty = clean)
- [ ] Import `_exec_approvals` lazily inside `check_security()` to avoid circular dep at import

#### `co_cli/main.py` (`co status` command)

- [ ] Call `check_security()` and display findings in `render_status_table()` or as a
      separate panel below the status table. If findings list is empty, show nothing
      (no "Security: clean" noise). If findings exist, print each with severity-appropriate
      style (warn = yellow, error = red).

#### `co_cli/_commands.py` (`/status` handler)

- [ ] `_cmd_status`: already calls `render_status_table(info)`. After printing, call
      `check_security()` and display findings the same way as `co status`.

#### Tests — `tests/test_status.py`

- [ ] Create a temp settings file with mode 0o644: `check_security()` returns a finding with
      `check_id="user-config-world-readable"` and `severity="warn"`
- [ ] Create a temp settings file with mode 0o600: no finding for that check
- [ ] Create a temp exec-approvals file with a `*` pattern: `check_security()` returns a finding
      with `check_id="exec-approval-wildcard"`
- [ ] All checks clean: `check_security()` returns `[]`

### Verification

```bash
uv run pytest tests/test_status.py -v
chmod 644 ~/.config/co-cli/settings.json && uv run co status
# Should show security warning about world-readable config
chmod 600 ~/.config/co-cli/settings.json && uv run co status
# Should be clean
```

---

## §8 — MMR Re-Ranking `[P3]`

### Problem

`_dedup_pulled()` deduplicates returned memories with a binary threshold (pairwise
`token_sort_ratio >= 85`). If FTS returns 5 memories on the same topic, dedup may keep all 5
(they're distinct enough at 85%) — but all 5 cover nearly the same ground and crowd out
memories on different aspects. MMR balances relevance with diversity so the returned set
covers more ground.

### Fix

After FTS retrieval and before dedup, apply Maximal Marginal Relevance re-ranking:
`MMR = λ * relevance - (1-λ) * maxSimilarityToAlreadySelected`
Default λ=0.7 (prefer relevance, moderate diversity). Uses Jaccard on token sets. ~50 lines
of Python, no external deps beyond tokenization.

**Dependency:** FTS path must be in place (already shipped). Applied only on FTS path.

### Implementation

#### `co_cli/tools/memory.py`

- [ ] Add `_token_set(text: str) -> set[str]` — lowercase, split on non-alphanumeric chars;
      used for Jaccard computation
- [ ] Add `_jaccard(a: set[str], b: set[str]) -> float` — `len(a & b) / len(a | b)` if
      `a | b` is non-empty, else 0.0
- [ ] Add `_mmr_rerank(candidates: list[tuple[MemoryEntry, float]], lmbda: float = 0.7, k: int = 5) -> list[MemoryEntry]`:
  - `candidates` is a list of `(entry, relevance_score)` pairs
  - Iteratively select the candidate that maximizes MMR: `λ * rel_score - (1-λ) * max_sim_to_selected`
  - `max_sim_to_selected`: max Jaccard similarity of candidate tokens to any already-selected entry
  - Stop after `k` items or when candidates is exhausted
- [ ] In `recall_memory()` FTS path: after building `matches` list from FTS results, apply
      `_mmr_rerank([(entry, r.score) for entry, r in zip(matches, fts_results)], k=max_results)`
      before dedup; the decay multiplier (§3) should be applied to scores before MMR input

#### Tests — `tests/test_memory.py`

- [ ] MMR with 5 near-identical candidates: selects fewer (more diverse) results than dedup alone
- [ ] MMR with `λ=1.0` (relevance only): returns candidates sorted by score (no diversity penalty)
- [ ] MMR with `λ=0.0` (diversity only): second pick is maximally different from first

### Verification

```bash
uv run pytest tests/test_memory.py -v -k mmr
```

---

## §9 — Embedding Provider Layer `[P3]`

### Problem

`recall_memory` uses FTS5 BM25 only. Semantic search (finding memories by meaning, not exact
keyword) is not available. A user asking "what's my preference for error handling?" won't find
a memory written as "I dislike catching all exceptions broadly" without keyword overlap.

### Fix

Add an `EmbeddingProvider` ABC with pluggable backends. Auto-detect available providers at
session start. Cache embeddings in SQLite to avoid re-embedding on every search.

**Provider priority order (auto-detect):**
1. `nomic-embed-text` via Ollama (zero config if Ollama is already running; 274M params, 768d, 8k ctx)
2. `mxbai-embed-large` via Ollama (335M params, 1024d, better MTEB retrieval at the cost of 512-token ctx)
3. `qwen3-embedding:0.6b` via Ollama (SOTA MTEB ~70+, 32k ctx, 0.6B params — best quality, heavier)
4. `gemini-embedding-001` via Gemini API (fallback when Ollama is not running; reuses existing key)
5. FTS-only (graceful fallback when no embedding provider is available)

### Implementation

**Note:** This is a significant effort (L). Do not start until §3 (decay), §8 (MMR), and
letta §3a (tag junction table) are shipped.

#### `co_cli/tools/_embeddings.py` (new file)

- [ ] `EmbeddingProvider` ABC: `embed_query(text: str) -> list[float]` and
      `embed_batch(texts: list[str]) -> list[list[float]]`
- [ ] `OllamaEmbeddingProvider(model: str, host: str)` — calls `/api/embeddings`
- [ ] `GeminiEmbeddingProvider(api_key: str, model: str)` — calls Gemini embedding API
- [ ] `detect_provider(settings) -> EmbeddingProvider | None` — probe Ollama for preferred models
      in priority order; fall back to Gemini if key present; return None for FTS-only mode

#### `co_cli/knowledge_index.py`

- [ ] Add `embedding_cache` table: `(provider TEXT, model TEXT, content_hash TEXT, embedding BLOB, created_at TEXT)`
- [ ] Add `get_cached_embedding(provider, model, content_hash) -> list[float] | None`
- [ ] Add `store_embedding(provider, model, content_hash, embedding)`
- [ ] Add `hybrid_search(query, embedding_provider, ...)` — FTS BM25 + cosine similarity merge:
      `score = 0.6 * vector_score + 0.4 * text_score`

#### `co_cli/tools/memory.py`

- [ ] In `recall_memory()`: if `ctx.deps.embedding_provider is not None` and backend is `hybrid`,
      call `knowledge_index.hybrid_search()` instead of `knowledge_index.search()`

#### `co_cli/deps.py`

- [ ] Add `embedding_provider: Any | None = field(default=None, repr=False)`

#### `co_cli/main.py`

- [ ] At startup, call `detect_provider(settings)` and assign to `deps.embedding_provider`

#### `co_cli/config.py`

- [ ] Add `embedding_model: str = Field(default="")` — override auto-detect with specific model name

#### Tests — `tests/test_embeddings.py` (new file)

- [ ] `detect_provider()` returns None when Ollama is offline and no Gemini key
- [ ] Embedding cache round-trip: store then retrieve returns same vector
- [ ] `hybrid_search` score is weighted combination of FTS and vector scores

### Verification

```bash
# Requires Ollama running with a supported embedding model:
OLLAMA_MODEL=nomic-embed-text uv run pytest tests/test_embeddings.py -v
```

---

## §10 — Process Registry / Backgrounding `[P3]`

### Problem

`run_shell_command` uses `asyncio.wait_for` with a timeout. Long-running commands (builds, test
suites, `npm install`) have no path forward: the user either waits with a long timeout or kills
the command and loses output. No partial output streaming, no background execution.

### Fix

Process registry with background execution: if a command is still running after a threshold
(default 10s), detach it to the background and return a session ID to the agent. Agent can
poll via `get_process_output(session_id)`. Output capped at 200KB, last 2000 chars always kept
as tail.

**Note:** This is a large effort (L). Coordinate with `docs/TODO-background-execution.md` before
starting.

### Implementation

#### `co_cli/_process_registry.py` (new file)

- [ ] `ProcessEntry` dataclass: `id: str, cmd: str, pid: int, stdout_buf: str, exit_code: int | None, started_at: str, finished_at: str | None`
- [ ] `ProcessRegistry` class (in-memory singleton):
  - `running: dict[str, ProcessEntry]`
  - `finished: dict[str, ProcessEntry]`
  - `register(entry)`, `finish(id, exit_code)`, `append_output(id, chunk)`, `get(id) -> ProcessEntry | None`
  - TTL-based cleanup: entries in `finished` expire after 30 min
- [ ] Output cap: `append_output` truncates `stdout_buf` to 200KB total; always keeps last 2000 chars

#### `co_cli/tools/shell.py`

- [ ] Add `background_threshold_seconds: int = 10` to `run_shell_command`
- [ ] If command exceeds threshold: register in `ProcessRegistry`, return
      `{"display": f"Command running in background. Session: {id}\nUse get_process_output('{id}') to check status.", ...}`
- [ ] Add `get_process_output(ctx, session_id: str) -> dict[str, Any]` tool — returns stdout,
      exit code, running/finished status

#### Tests — `tests/test_process_registry.py` (new file)

- [ ] Output cap: appending >200KB truncates to last 2000 chars tail
- [ ] TTL cleanup: finished entries expire after 30 min

### Verification

```bash
uv run pytest tests/test_process_registry.py -v
# Manual: run a long command (sleep 15) and verify background session is returned
```

---

## §11 — Security Audit Command `[P3]`

### Problem

§7 adds inline security checks to `co status`. A dedicated audit command can run a broader
set of checks that are too verbose or slow for the inline status display.

### Fix

Add `co doctor` (or `co security-check`) subcommand that runs an extended audit:
all §7 checks plus additional checks that require more computation or produce more output.

**Additional checks beyond §7:**
- Memory directory permissions (warn if world-readable)
- Whether `exec_approvals.json` is world-readable
- Whether any configured safe command includes a glob or path separator (misconfiguration)
- Shell history check: warn if `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` appear in `~/.zsh_history` or `~/.bash_history`

### Implementation

#### `co_cli/status.py`

- [ ] Add `run_security_audit() -> list[SecurityFinding]` — superset of `check_security()`:
  - All 3 checks from §7
  - Memory dir permissions check
  - exec-approvals.json permissions check
  - Safe command list: flag any entry containing `/` or `*`
  - Shell history scan (best-effort; skip if history file not readable)

#### `co_cli/main.py`

- [ ] Add `@app.command("doctor")` subcommand: calls `run_security_audit()`, prints all findings
      with severity styles; if clean, print `[success]No issues found.[/success]`

#### Tests — `tests/test_status.py`

- [ ] Add cases for the new checks in `run_security_audit()`

### Verification

```bash
uv run co doctor
```

---

## §12 — Skills System `[P3]`

### Problem

No user-extensible skill layer. Behavior customization requires editing source code. Users
cannot add domain-specific prompts without forking the project. OS-aware conditional loading
is not possible.

### Fix

`skills/` directory in `.co-cli/` for user-defined SKILL.md files. Frontmatter controls
requirements and invocation policy. At session start, scan, filter eligible skills, inject
into system prompt within a token budget. See `docs/TODO-skills-system.md` for full spec.

### Implementation

Tracked in `docs/TODO-skills-system.md`. Not detailed here — coordinate with that doc.

---

## §13 — Cron Scheduling `[P3, blocked on §6]`

### Problem

No scheduling. All execution is synchronous/interactive. Cannot automate recurring tasks
(daily summaries, reminders, data fetches).

### Fix

Full cron service with `at`, `every`, and `cron` schedule kinds. Jobs persist to `cron.json`.
Requires session persistence (§6) first — isolated agent sessions reuse per-job session IDs
across runs.

**Dependency:** §6 (session persistence) must ship first.

### Implementation

Tracked in `docs/TODO-background-execution.md`. Not detailed here — coordinate with that doc.

---

## §14 — Config Includes `[P3]`

### Problem

Flat `settings.json`. Team setups (shared base config + personal overrides) require manual
duplication or separate config management.

### Fix

`$include` key in settings JSON that accepts a path or array of paths. Deep merge semantics.
Path confinement enforced: includes must stay under config root (CWE-22 mitigation). Not urgent
for single-user CLI but needed for team deployment contexts.

### Implementation

#### `co_cli/config.py`

- [ ] In `load_config()`: detect `$include` key in loaded dict before calling `Settings.model_validate()`
- [ ] Resolve included paths relative to the settings file's directory; reject paths outside root
      (path traversal guard)
- [ ] Deep merge: arrays concatenate, objects recurse, scalars from later layer override earlier
- [ ] Circular detection: depth limit = 10, track visited paths by realpath
- [ ] Remove `$include` key from merged dict before passing to `Settings.model_validate()`

#### Tests

- [ ] `$include` pointing to a second file: merged result has both files' settings
- [ ] `$include` with path traversal (`../../etc/passwd`): raises ValueError
- [ ] Circular include (`a.json` includes `b.json` includes `a.json`): raises at depth limit

### Verification

```bash
uv run pytest tests/test_config.py -v -k include
```

---

## Sequencing Table

| Priority | Item | Effort | Dependency |
|----------|------|--------|------------|
| P1 | §1 Shell arg validation | S | — |
| P1 | §2 Exec approval persistence | M | — |
| P2 | §3 Temporal decay scoring | S | — |
| P2 | §4 `/new` slash command | S | `_index_session_summary` in `_history.py` (deferred) |
| P2 | §5 Model fallback | S | — |
| P2 | §6 Session persistence | M | — |
| P2 | §7 Doctor security checks | S | §2 (for approvals check) |
| P3 | §8 MMR re-ranking | S | §3 (decay scores as input) |
| P3 | §9 Embedding provider layer | L | §8, tag junction table (shipped) |
| P3 | §10 Process registry / backgrounding | L | `docs/TODO-background-execution.md` |
| P3 | §11 Security audit command | M | §7 |
| P3 | §12 Skills system | L | `docs/TODO-skills-system.md` |
| P3 | §13 Cron scheduling | XL | §6 |
| P3 | §14 Config includes | S | — |

---

## End-to-End Verification

After implementing §1–§7:
```bash
uv run pytest tests/test_approval.py tests/test_exec_approvals.py \
    tests/test_memory_decay.py tests/test_session.py tests/test_status.py -v

uv run co status   # should show Security section if any findings exist
uv run co chat     # approve a command with "a"; restart; verify not re-prompted
```

---

## Files

| File | Action | Section |
|------|--------|---------|
| `co_cli/_approval.py` | Modify — arg-level validation in `_is_safe_command()` | §1 |
| `co_cli/_exec_approvals.py` | New — persistence layer for exec approvals | §2 |
| `co_cli/_session.py` | New — session metadata persistence | §6 |
| `co_cli/_process_registry.py` | New — process registry for background commands | §10 |
| `co_cli/tools/_embeddings.py` | New — embedding provider ABC + backends | §9 |
| `co_cli/_orchestrate.py` | Modify — check persisted approvals; save on "a" choice | §2 |
| `co_cli/_commands.py` | Modify — add `/new`, `/approvals list`, `/approvals clear` | §2, §4 |
| `co_cli/tools/memory.py` | Modify — temporal decay multiplier, MMR re-ranking | §3, §8 |
| `co_cli/tools/shell.py` | Modify — background threshold, `get_process_output` tool | §10 |
| `co_cli/knowledge_index.py` | Modify — embedding cache table, hybrid search | §9 |
| `co_cli/config.py` | Modify — `session_ttl_minutes`, `fallback_models`, `memory_recall_half_life_days`, `embedding_model` | §3, §5, §6, §9, §14 |
| `co_cli/deps.py` | Modify — same fields + `exec_approvals_path`, `embedding_provider` | §2, §3, §5, §6, §9 |
| `co_cli/agent.py` | Modify — model fallback retry loop | §5 |
| `co_cli/status.py` | Modify — `SecurityFinding`, `check_security()`, `run_security_audit()` | §7, §11 |
| `co_cli/main.py` | Modify — session persistence wiring, security display, create_deps fields | §5, §6, §7 |
| `tests/test_approval.py` | Modify — arg-validation cases | §1 |
| `tests/test_exec_approvals.py` | New | §2 |
| `tests/test_memory_decay.py` | Modify — retrieval decay + MMR cases | §3, §8 |
| `tests/test_session.py` | New | §6 |
| `tests/test_status.py` | Modify — security check cases, audit command cases | §7, §11 |
| `tests/test_embeddings.py` | New | §9 |
| `tests/test_process_registry.py` | New | §10 |
