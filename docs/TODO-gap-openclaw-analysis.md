# Gap Analysis: co-cli vs openclaw

**Date**: 2026-02-18
**Source**: openclaw @ 33f30367e (pulled 2026-02-18)
**Focus**: Tooling and skills gaps — actionable for co-cli MVP+

This doc captures specific patterns from openclaw that co-cli should adopt,
organized by domain and priority. Each item includes what openclaw does,
what co-cli does today, and what specifically to borrow.

---

## 1. Memory System

### 1.1 Hybrid Search (FTS5 + Vector) — P1

**What openclaw does:**
Three modalities merged via linear combination:
- FTS5 BM25 (SQLite built-in, no deps)
- sqlite-vec cosine similarity (loadable extension)
- Merge: `score = 0.6 * vectorScore + 0.4 * textScore`

Graceful degradation: if no embedding provider is configured, falls back to
FTS5-only mode with keyword extraction from conversational queries
(`extractKeywords()` strips stopwords, handles CJK).

**What co-cli does today:**
Substring grep on content + tags. O(n) scan over all `.md` files.
`rapidfuzz.token_sort_ratio` for dedup only — not used in retrieval ranking.

**Gap:**
- No FTS index → full scan on every recall
- No semantic/vector retrieval at all
- No graceful degradation path

**What to borrow:**
1. Add SQLite + FTS5 as the next retrieval tier (zero extra deps, SQLite is stdlib)
2. Schema: `chunks` table + `chunks_fts` virtual table. Index chunk text + tags
3. FTS-first implementation: no embeddings required, pure BM25 ranking
4. Keyword extraction for conversational queries (strip stopwords before FTS)
5. Embedding + sqlite-vec as opt-in phase-2 once FTS is stable

**Files in openclaw:** `src/memory/hybrid.ts`, `src/memory/memory-schema.ts`,
`src/memory/query-expansion.ts`, `src/memory/sqlite.ts`

---

### 1.2 Temporal Decay Scoring — P2

**What openclaw does:**
Exponential decay applied post-retrieval: `score *= exp(-ln(2)/halfLife * ageDays)`
Default half-life: 30 days. Evergreen files (non-dated slugs like `memory.md`)
bypass decay. Applied to search scores, not to file deletion.

**What co-cli does today:**
Decay is count-based deletion: oldest memories deleted or consolidated when
`memory_max_count` is exceeded. No per-retrieval relevance weighting by age.

**Gap:**
Search results don't rank fresh memories higher than stale ones. A 6-month-old
preference competes equally with yesterday's correction.

**What to borrow:**
Apply a recency multiplier to search scores rather than (only) deleting old
memories. Add `decay_protected` flag already in the schema to mark evergreen
items. Half-life can be a setting with a 30-day default.

---

### 1.3 MMR Re-Ranking — P3

**What openclaw does:**
After candidate retrieval, applies Maximal Marginal Relevance:
`MMR = λ * relevance - (1-λ) * maxSimilarityToAlreadySelected`
Default λ=0.7 (prefer relevance, moderate diversity). Uses Jaccard on token sets.
Net effect: avoids returning 5 nearly-identical memories on the same topic.

**What co-cli does today:**
`_dedup_pulled()` deduplicates by pairwise `token_sort_ratio ≥ 85` — eliminates
near-duplicates but doesn't rank for diversity.

**Gap:**
Co-cli's dedup is binary (include/exclude) at a high threshold. MMR is a
continuous re-ranking that maximizes coverage across returned results.

**What to borrow:**
Port the MMR algorithm: it's ~50 lines of Python, no external deps beyond
tokenization. Apply after FTS retrieval once that's in place.

---

### 1.4 Embedding Provider Layer — P3

**What openclaw does:**
Uniform `EmbeddingProvider` interface with four backends:
- OpenAI `text-embedding-3-small` (default), batch API support
- Gemini `gemini-embedding-001`, batch API support
- Voyage `voyage-4-large`, 32k tokens
- Local `node-llama-cpp` (quantized model, no API key needed)

Provider auto-detected from available keys; falls back to FTS-only if none.
Embedding cache: SQLite table keyed by `(provider, model, providerKey, contentHash)`.
LRU eviction. Result: re-embedding only on content change.

**What co-cli does today:**
No embedding layer at all.

**Gap:**
Full gap. Semantic search is blocked until FTS is in place (1.1).

**What to borrow (post-FTS):**
1. `EmbeddingProvider` ABC with `embed_query(text)` and `embed_batch(texts)`
2. Provider priority order (reuses existing Ollama base URL, no new credentials):
   1. `nomic-embed-text` — 274M, 768 dims, 8192-token context, 54.5M Ollama pulls;
      zero-config if Ollama is already running; memories fit in one chunk
   2. `mxbai-embed-large` — 335M, 1024 dims, MTEB retrieval 64.68 (vs 53 for nomic);
      better quality at the cost of 512-token context limit per chunk
   3. `qwen3-embedding:0.6b` — SOTA MTEB scores (~70+), 32k context, 0.6B params;
      best quality but meaningfully heavier alongside an LLM session
   4. Gemini API (`gemini-embedding-001`) — fallback when Ollama is not running;
      requires `gemini_api_key` already in settings
3. Embedding cache in the same SQLite DB (add `embedding_cache` table)
4. Auto-detect: probe Ollama first (`nomic-embed-text`), then Gemini API, then FTS-only

---

### 1.5 Session-to-Memory Hook — P2

**What openclaw does:**
On `/new` command, the hook reads the last 15 messages, generates a slug via LLM,
and saves a dated memory file: `memory/YYYY-MM-DD-<slug>.md`. Sessions are also
indexed as a searchable source alongside memory files.

**What co-cli does today:**
`/new` is not a slash command. Session history is not persisted as a retrievable
memory source.

**Gap:**
Conversation knowledge is entirely ephemeral. A good exchange that yielded a
decision or a preference is lost when the session ends.

**What to borrow:**
1. Add `/new` slash command that triggers a session-close hook
2. Hook generates a dated summary memory from the recent conversation
3. LLM generates a 4-6 word slug for the filename
4. Fits directly into existing `save_memory` infrastructure

---

### 1.6 Chunking with Overlap — P3

**What openclaw does:**
Line-based chunking with configurable tokens/overlap. Each chunk has
`(startLine, endLine, hash)`. Enforces provider `maxInputTokens` by re-splitting
oversized chunks. Chunks are indexed and retrieved, not whole files.

**What co-cli does today:**
Whole-file storage and retrieval. No chunking.

**Gap:**
For long memories or session transcripts, FTS matches the whole file. Chunking
provides finer-grained retrieval with line-level citations.

**What to borrow:**
Adopt line-based chunking once the SQLite tier is in place. At MVP scale
(<200 files), chunking is not urgent but the schema should be designed to
support it so migration is cheap later.

---

## 2. Shell / Exec

### 2.1 Safe Binary Trust — P1

**What openclaw does:**
Two-layer auto-approval:
1. **Trusted directories**: `/bin`, `/usr/bin`, `/usr/local/bin`, `/opt/homebrew/bin` —
   executables resolved to these directories don't need allowlist entries
2. **Safe bins list**: `jq grep cut sort uniq head tail tr wc` — auto-approved
   when called with scalar arguments only (no file references, no glob patterns,
   no path-like args starting with `./ ~/ /`)
3. Argument validation rejects: glob chars (`*?[]`), path-like args, file references

**What co-cli does today:**
Prefix-based auto-approval: if the command string starts with a token in
`_DEFAULT_SAFE_COMMANDS`, it's approved automatically. No argument validation.
`git diff --no-index /etc/passwd /dev/null` would auto-approve.

**Gap:**
Prefix matching is coarse — it approves the command name but not the arguments.
A malicious argument can bypass the approval gate entirely.

**What to borrow:**
1. Add argument validation to auto-approval: reject commands where any argument
   contains glob chars, path separators, or looks like a file reference
2. Validate resolved path of executable is in a trusted directory list
3. Keep prefix list as an additional allow layer, but layer on argument checks

**Files in openclaw:** `src/infra/exec-safe-bin-trust.ts`

---

### 2.2 Dangerous Environment Variable Filtering — P1

**What openclaw does:**
Explicit blocklist of env vars that can be used for privilege escalation:
```
LD_PRELOAD, LD_LIBRARY_PATH, LD_AUDIT,
DYLD_INSERT_LIBRARIES, DYLD_LIBRARY_PATH,
NODE_OPTIONS, NODE_PATH,
PYTHONPATH, PYTHONHOME,
RUBYLIB, PERL5LIB,
BASH_ENV, ENV,
GCONV_PATH, IFS, SSLKEYLOGFILE
```
Plus prefix blocks: `DYLD_*`, `LD_*`. Applied at exec time, not at approval time.

**What co-cli does today:**
`restricted_env()` in `_shell_env.py` — but I could not read its content to
confirm it blocks the same set. Config mentions sanitized environment.

**Action:**
Read `co_cli/_shell_env.py` and verify coverage against the openclaw blocklist.
Add any missing vars to the restricted env. This is a security correctness check,
not a new feature.

---

### 2.3 Exec Approval Persistence — P2

**What openclaw does:**
Allowlist stored in `~/.openclaw/exec-approvals.json` (mode 0o600):
```json
{
  "agents": {
    "main": {
      "allowlist": [
        { "id": "uuid", "pattern": "git *", "lastUsedAt": "...", "lastUsedCommand": "..." }
      ]
    }
  }
}
```
Once approved, pattern is persisted — user is not re-prompted for the same class
of commands across sessions. Patterns are UUID-keyed for surgical removal.

**What co-cli does today:**
Approval is per-session only. Every new session re-prompts for every command,
including ones the user approved 100 times before.

**Gap:**
High friction: the user must re-approve `git status`, `pytest`, `npm test` every
session. Approval history is lost on restart.

**What to borrow:**
1. Persist approved patterns to `.co-cli/exec-approvals.json` (mode 0o600)
2. Schema: per-agent list of `{ id, pattern, lastUsedAt, lastUsedCommand }`
3. Pattern matching: shell glob (`fnmatch`) on command string
4. Add `lastUsedAt` so patterns can age out (stale approval hygiene)
5. Expose via a `co exec-approvals list/clear` subcommand

---

### 2.4 Process Registry (Background Commands) — P3

**What openclaw does:**
Full in-memory registry for active processes:
- `runningSessions`: live processes with stdout/stderr buffer + tail (last 2000 chars)
- `finishedSessions`: completed processes with exit code, output, duration
- TTL-based cleanup (30 min default, 1 min–3 hr range)
- Backgrounding: process runs in background after 10s; user gets session ID to poll
- Max output: 200 KB aggregated, 30 KB pending buffer

**What co-cli does today:**
`asyncio.wait_for` with timeout. No background support. If a command exceeds
timeout, it's killed and output is lost. No partial output streaming.

**Gap:**
Long-running commands (builds, tests, downloads) have no path forward. User must
either wait with a long timeout or kill the command and lose output.

**What to borrow:**
1. Process registry dataclass: `{ id, cmd, pid, stdout_buf, exit_code, started_at }`
2. Background flag: if command is still running after N seconds, return a session
   ID to the model with instructions to poll later
3. Poll tool: `get_process_output(session_id)` checks if still running, returns
   accumulated output
4. Output cap: truncate at 200 KB, always keep last 2000 chars as tail

---

## 3. Security

### 3.1 SSRF Protection (Web Fetch) — P1

**What openclaw does:**
Full SSRF guard in `src/infra/net/ssrf.ts`:
- Blocks: localhost, `*.local`, `*.internal`, `metadata.google.internal`
- Private IPv4: `10/8`, `172.16/12`, `192.168/16`, `169.254/16`
- Private IPv6: link-local `fe80::/10`, site-local `fec0::/10`, unique local `fc00::/7`
- **IPv6 embedded IPv4 extraction**: detects `::ffff:`, `64:ff9b::`, `2002::`, `2001:0000::` wrappers
- **DNS pinning**: resolves all A/AAAA records before fetch; blocks if ANY resolved IP is private;
  wraps fetch dispatcher with pinned IPs to prevent TOCTOU

**What co-cli does today:**
`tools/_url_safety.py` is referenced but does not exist as a separate file —
it may have been deleted or never created. `web.py` imports `is_url_safe` from it.

**Gap:**
This is a critical security gap. If `_url_safety.py` doesn't exist or doesn't
block private IPs, `web_fetch` can be used to probe the internal network.

**Immediate action:**
1. Confirm whether `_url_safety.py` exists and what it implements
2. If missing or incomplete, port the private IP detection from openclaw:
   - Block `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`
   - Block `::1`, `fe80::/10`
   - Block hostname patterns: `*.local`, `*.internal`, `localhost`
   - DNS resolution before fetch to catch CNAME → private IP redirects
3. IPv6 embedded IPv4 extraction is optional for MVP but should be noted

**Files in openclaw:** `src/infra/net/ssrf.ts`

---

### 3.2 Security Audit Command — P3

**What openclaw does:**
`co doctor` runs a multi-category security audit:
- Filesystem permissions (state dir, config file world-readable/writable)
- Gateway auth config (bind addr, token length, trusted proxies)
- Exec approvals (allowFrom wildcards, oversized allowlists)
- Logging config (sensitive data redaction)
- Returns structured findings: `{ severity, checkId, title, detail, remediation }`

**What co-cli does today:**
`co status` checks LLM connectivity, memory dir, tool availability. No security
posture checks.

**Gap:**
No automated check that config is secure (e.g., overly broad safe-command list,
world-readable settings.json with API keys).

**What to borrow:**
Add a `co doctor` or `co security-check` subcommand that checks:
1. `~/.config/co-cli/settings.json` file permissions (should be 0o600)
2. Memory directory permissions
3. Whether API keys appear in shell history or env vars
4. Whether exec approval list contains overly broad patterns (`*`)

---

## 4. Agent Architecture

### 4.1 Model Fallback — P2

**What openclaw does:**
`runWithModelFallback()` pattern:
- Primary model + explicit fallback list in config
- On failure: try next model in list
- Context overflow: NOT retried (inner compaction handles it)
- Cooldown: tracks failed providers, probes after recovery window
- Probe throttle: min 30s between probes per provider

**What co-cli does today:**
Single model configured in `settings.json`. If the model fails, the whole session
fails. No fallback, no cooldown tracking.

**Gap:**
API outages or rate limits terminate the session with no recovery path.

**What to borrow:**
1. Add `fallback_models: list[str]` to `Settings`
2. In the agent loop, catch provider errors and retry with the next model
3. Simple implementation: no cooldown or probe logic needed for MVP

---

### 4.2 Skills System — P3

**What openclaw does:**
SKILL.md files in `skills/*/SKILL.md` directories. Frontmatter controls:
- OS requirements (`os: [darwin, linux]`)
- Required binaries (`requires.bins: [jq, git]`)
- Required env vars (`requires.env: [GITHUB_TOKEN]`)
- Invocation policy (user-invocable, model-invocable)

Skills are included in the system prompt at session start. Ineligible skills
(missing bins, wrong OS) are silently filtered. Budget: 150 skills, 30 KB max.

**What co-cli does today:**
No skills system. System prompt is assembled from personality + memory + date.
Tool docs serve as the "skills" layer implicitly.

**Gap:**
No way for users to extend co's behavior with domain-specific prompts without
editing source. No OS-aware conditional skill loading.

**What to borrow (phase 2):**
1. `skills/` directory in `.co-cli/` for user-defined SKILL.md files
2. Frontmatter: `requires.bins`, `os`, `invocation` at minimum
3. At session start, scan, filter eligible skills, inject into system prompt
4. Token budget: respect context limits on skill injection

---

### 4.3 Cron / Background Scheduling — P3

**What openclaw does:**
Full cron service with three schedule kinds (`at`, `every`, `cron`), two session
targets (`main`, `isolated`), and exponential backoff on job errors. Jobs persist
to `cron.json`. Isolated agent sessions reuse a per-job session ID across runs
for conversational continuity.

**What co-cli does today:**
No scheduling. All execution is synchronous/interactive.

**Gap:**
Cannot automate recurring tasks (daily summaries, reminders, data fetches).

**What to borrow:**
Deferred to post-MVP. The scheduler requires session persistence infrastructure
(gap 4.4) first. Log as a roadmap item.

---

### 4.4 Session Persistence — P2

**What openclaw does:**
Per-agent `sessions.json` with `SessionEntry` records (sessionId, model, channel,
token counts, last delivery target, compaction count). Sessions survive across
processes. Freshness policy: idle > 60 min → new session. Pruning by count and age.

**What co-cli does today:**
Conversation history is in-memory only. Each `uv run co chat` starts a blank
context. Multi-turn memory across restarts relies entirely on the memory tools.

**Gap:**
No session continuity. Context window compaction count, model preferences, and
token usage are lost between invocations.

**What to borrow:**
1. `.co-cli/session.json` with at minimum: `{ sessionId, lastUsedAt, compactionCount, model }`
2. Restore history from the last session (up to context window) on startup
3. Prune on idle > N minutes (configurable, default 60)
4. This is the foundation for cron/subagent support (4.3) and eval continuity

---

## 5. Config System

### 5.1 Config Includes (`$include`) — P3

**What openclaw does:**
`$include` key in config JSON5 accepts a path or array of paths. Deep merge
semantics (arrays concatenate, objects recurse, scalars override). Path
confinement enforced: includes must stay under config root (CWE-22 mitigation).
Circular detection with depth limit = 10.

**What co-cli does today:**
Flat `settings.json`. All config in one file. No composition.

**Gap:**
Team setups (shared base config + personal overrides) require manual duplication.

**What to borrow:**
Not urgent for single-user CLI. Useful when co-cli is deployed in team contexts.
Add `$include` support once settings model is stable.

---

### 5.2 Doctor / Health Check Enhancements — P2

**What openclaw does:**
`co doctor` runs: config validation, auth profile health, legacy state migration,
session lock cleanup, security checks, sandbox health. Structured findings with
remediation hints.

**What co-cli does today:**
`co status` checks LLM API, memory dir, tool deps. No migration, no security
posture, no structured findings.

**Gap:**
Config errors (wrong provider key name, deprecated field) silently fail or produce
cryptic errors. No upgrade path detection.

**What to borrow:**
1. Schema validation with unknown key detection + user-facing hint
2. API key presence check per configured provider (not just the active one)
3. Memory dir permissions check (warn if world-readable)
4. Structured output: `{ severity, checkId, detail, remediation }`

---

## 6. Skills (New: Latest openclaw diff)

The 2026-02-18 pull added several patterns worth noting immediately:

### 6.1 `exec-safe-bin-trust.ts` (new file in pull) — P1

Hardcoded trusted directories for executable resolution + argument-level validation.
See section 2.1 — this is the specific file that implements that pattern.

### 6.2 `install-source-utils.ts` (new file in pull) — P2

Validates plugin/extension installation sources. Key pattern: `npm pack
--ignore-scripts` to download packages without executing postinstall hooks.
Relevant when co-cli adds any plugin install mechanism.

### 6.3 `git-root.ts` (new file in pull) — P2

Filesystem walk to detect git root (max depth 12). Co-cli currently uses `git`
subprocess for this. The pure filesystem walk is faster (no subprocess) and works
in restricted environments.

**What to borrow:**
Port the git root walk to Python: `find_git_root(start: Path, max_depth=12)`.
Use instead of `git rev-parse --show-toplevel` subprocess in `shell_backend.py`.

### 6.4 Subagent announce improvements — P3

`subagent-announce.ts` now has retry logic (3x, exponential backoff) and a
`suppressAnnounceReason` field for steer-restart scenarios. Relevant when co-cli
adds subagent support. File for future reference.

### 6.5 `devices-cli.ts` (new file in pull) — out of scope

Device pairing is a gateway/mobile feature with no co-cli equivalent. Skip.

---

## Priority Summary

| Priority | Item | Effort |
|----------|------|--------|
| P1 | SSRF protection in web_fetch (3.1) — verify/fix `_url_safety.py` | XS–S |
| P1 | Safe binary trust: argument validation on auto-approve (2.1) | S |
| P1 | Dangerous env var blocklist audit (2.2) | XS |
| P2 | SQLite FTS5 retrieval tier (1.1) | M |
| P2 | Exec approval persistence (2.3) | M |
| P2 | Session persistence (4.4) | M |
| P2 | Temporal decay scoring on search (1.2) | S |
| P2 | Session-to-memory hook on `/new` (1.5) | S |
| P2 | Model fallback (4.1) | S |
| P2 | Doctor enhancements (5.2) | S |
| P2 | Git root via filesystem walk (6.3) | XS |
| P3 | MMR re-ranking (1.3) | S |
| P3 | Embedding provider layer (1.4) | L |
| P3 | Chunking with overlap (1.6) | M |
| P3 | Process registry / backgrounding (2.4) | L |
| P3 | Security audit command (3.2) | M |
| P3 | Skills system (4.2) | L |
| P3 | Cron scheduling (4.3) — requires 4.4 | XL |
| P3 | Config includes (5.1) | S |

---

## Anti-Patterns to Avoid

Openclaw patterns that do NOT fit co-cli's architecture:

1. **Gateway / WebSocket server** — openclaw is a multi-channel bot server;
   co-cli is a local CLI. No gateway, no WebSocket, no device pairing.

2. **Plugin package system** — openclaw has npm-based plugin installs with
   postinstall isolation. Co-cli uses pydantic-ai tool registration directly.
   MCP servers already cover extensibility at the tool level.

3. **Multi-agent per-channel sessions** — co-cli is single-user, single-session.
   The per-agent session store, group session routing, and delivery targets are
   all channel-specific concepts with no equivalent.

4. **LanceDB** — not present in openclaw's memory module either; openclaw uses
   SQLite + sqlite-vec. No need to evaluate LanceDB separately.
