# TODO: Openclaw Adoption Action Plan

P3 items remaining from the openclaw gap analysis. P1/P2 items (§1–§7) shipped with the openclaw-skills-adoption-review delivery.

**Source:** openclaw @ 33f30367e (pulled 2026-02-18).

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
      before dedup; the decay multiplier (§3, shipped) should be applied to scores before MMR input

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

**Note:** This is a significant effort (L). Do not start until §8 (MMR) and
tag junction table (shipped) are done.

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

§7 (shipped) adds inline security checks to `co status`. A dedicated audit command can run a broader
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
Requires session persistence (§6, shipped) first — isolated agent sessions reuse per-job session IDs
across runs.

**Dependency:** §6 (session persistence) shipped.

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
| P3 | §8 MMR re-ranking | S | §3 decay scores (shipped) |
| P3 | §9 Embedding provider layer | L | §8, tag junction table (shipped) |
| P3 | §10 Process registry / backgrounding | L | `docs/TODO-background-execution.md` |
| P3 | §11 Security audit command | M | §7 (shipped) |
| P3 | §12 Skills system | L | `docs/TODO-skills-system.md` |
| P3 | §13 Cron scheduling | XL | §6 (shipped) |
| P3 | §14 Config includes | S | — |

---

## Files

| File | Action | Section |
|------|--------|---------|
| `co_cli/tools/memory.py` | Modify — MMR re-ranking | §8 |
| `co_cli/tools/_embeddings.py` | New — embedding provider ABC + backends | §9 |
| `co_cli/knowledge_index.py` | Modify — embedding cache table, hybrid search | §9 |
| `co_cli/config.py` | Modify — `embedding_model` | §9, §14 |
| `co_cli/deps.py` | Modify — `embedding_provider` | §9 |
| `co_cli/main.py` | Modify — `detect_provider` at startup, `co doctor` command | §9, §11 |
| `co_cli/_process_registry.py` | New — process registry for background commands | §10 |
| `co_cli/tools/shell.py` | Modify — background threshold, `get_process_output` tool | §10 |
| `co_cli/status.py` | Modify — `run_security_audit()` | §11 |
| `tests/test_memory.py` | Modify — MMR re-ranking cases | §8 |
| `tests/test_embeddings.py` | New | §9 |
| `tests/test_process_registry.py` | New | §10 |
| `tests/test_status.py` | Modify — security audit cases | §11 |
