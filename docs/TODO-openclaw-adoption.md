# TODO: Openclaw Adoption — Implementation Tasks

Actionable implementation tasks for the P1/P2 items in `docs/TODO-gap-openclaw-analysis.md`.
Design rationale, problem statements, and design decisions live there. This doc is the task list.

P3 items (MMR, embeddings, process registry, skills, cron, config includes) are tracked in
`TODO-gap-openclaw-analysis.md` §8–§14. Not in this doc.

---

## TASK-1 — Shell Arg Validation (P1)

**What:** Add argument-level validation to `_is_safe_command()` in `co_cli/_approval.py`.
After a prefix match succeeds, extract the remainder of the command string, split on
whitespace, and reject any token that contains glob chars (`* ? [ ] { }`), path separators
(`/` or `\`), path starters (`./`, `~/`, `..`), or null bytes (`\x00`).
Single-letter flags (`-f`) and `--word-flags` with no path chars pass.

**Why:** `git diff --no-index /etc/passwd /dev/null` currently passes the safe-command check
— the prefix matches, no chaining operators are present, but the path argument escapes
unnoticed. See `TODO-gap-openclaw-analysis.md §1` for full analysis.

**Files:**
- `co_cli/_approval.py` — add `_validate_args(args_str: str) -> bool`, wire into `_is_safe_command()`
- `tests/test_commands.py` — add arg-validation test cases

**Test cases to add:**
- `git diff --no-index /etc/passwd /dev/null` → False (path arg)
- `ls /etc` → False (path arg under `ls` prefix)
- `grep foo /etc/hosts` → False (path arg)
- `grep -r foo` → True (flag + bare word, no path chars)
- `grep foo*` → False (glob char in arg)
- `git diff HEAD~1` → True (`~1` is a git rev, not a tilde-path; only `~/` prefix is rejected)
- `git status --short` → True (flag only, no path)
- `git diff` (no args) → True
- `wc -l` → True
- `cat ../secret` → False (`..` path starter in arg)

**Done when:** `uv run pytest tests/test_commands.py -v -k "safe_command or arg_valid"` passes
with all new cases included.

---

## TASK-2 — Exec Approvals Module (P1)

**What:** Create `co_cli/_exec_approvals.py` with pure data functions over a JSON file at
`.co-cli/exec-approvals.json`. No I/O coupling to deps — pure functions only, testable in isolation.

**Why:** Approval persistence requires a clean data layer. See `TODO-gap-openclaw-analysis.md §2`.

**Files:**
- `co_cli/_exec_approvals.py` — new file
- `tests/test_exec_approvals.py` — new file

**Functions to implement:**
```
load_approvals(path: Path) -> list[dict]
    Read JSON; return [] on missing/corrupt. Never raises.

save_approvals(path: Path, approvals: list[dict]) -> None
    Write JSON indented. Call os.chmod(path, 0o600) after every write_text() call
    (not just on creation — ensures permissions survive file recreation).

find_approved(cmd: str, approvals: list[dict]) -> dict | None
    Skip entries where pattern == "*" (log logger.warning).
    Return first entry where fnmatch.fnmatch(cmd, entry["pattern"]) is True; None if no match.

add_approval(approvals: list[dict], pattern: str, cmd: str) -> list[dict]
    If an entry with the same pattern already exists: call update_last_used() instead.
    Otherwise append {id: uuid4().hex, pattern, created_at: now_iso,
                     last_used_at: now_iso, last_used_command: cmd}.
    Returns updated list (does not mutate input).

update_last_used(approvals: list[dict], approval_id: str, cmd: str) -> list[dict]
    Find entry by id; update last_used_at and last_used_command. Return updated list.

prune_stale(approvals: list[dict], max_age_days: int = 90) -> list[dict]
    Remove entries where last_used_at is older than max_age_days.
    Log logger.warning() for each removed entry naming the pattern.
    Entries missing last_used_at are kept (conservative). Return pruned list.

derive_pattern(cmd: str) -> str
    Collect up to 3 non-flag tokens (tokens not starting with '-'), then append ' *'.
    Examples:
      'git status --short'  -> 'git status *'      (2 non-flag tokens)
      'uv run pytest'       -> 'uv run pytest *'   (3 non-flag tokens — safer than 2)
      'grep -r foo'         -> 'grep *'            (1 non-flag token before first flag)
      'ls'                  -> 'ls *'
```

**File schema** (`.co-cli/exec-approvals.json`):
```json
{
  "approvals": [
    {
      "id": "uuid4-hex-string",
      "pattern": "git status *",
      "created_at": "2026-02-28T12:00:00+00:00",
      "last_used_at": "2026-02-28T14:30:00+00:00",
      "last_used_command": "git status --short"
    }
  ]
}
```

**Test cases in `tests/test_exec_approvals.py`:**
- `add_approval()` then `find_approved()` with exact command → match
- `add_approval()` with pattern `"git status *"` → matches `"git status --short"` and `"git status"`
- `find_approved()` no matching pattern → None
- `find_approved()` with a `pattern == "*"` entry → None (wildcard skipped)
- `prune_stale()` removes entry with `last_used_at` 100 days ago; keeps entry 10 days ago
- `save_approvals()` + `load_approvals()` round-trip: data intact
- `save_approvals()` sets mode 0o600 — verify `path.stat().st_mode & 0o777 == 0o600`
- `save_approvals()` called twice: mode is still 0o600 on second write
- `update_last_used()` updates correct entry by id; other entries unchanged
- `add_approval()` called twice with same pattern → only one entry in result list (dedup)
- `derive_pattern("git status --short")` → `"git status *"`
- `derive_pattern("uv run pytest")` → `"uv run pytest *"`
- `derive_pattern("grep -r foo")` → `"grep *"`
- `derive_pattern("ls")` → `"ls *"`

**Done when:** `uv run pytest tests/test_exec_approvals.py -v` passes all cases.

---

## TASK-3 — Wire Exec Approvals into Orchestration (P1)

**What:** Integrate the exec approvals module into the shell tool approval flow. Add `exec_approvals_path`
to `CoDeps`. Check persisted approvals before prompting. Save new "always" approvals to disk.
Add `/approvals list` and `/approvals clear [id]` slash commands.

**Why:** The module from TASK-2 has no effect until wired into the approval gate.

**Files:**
- `co_cli/deps.py` — add `exec_approvals_path: Path`
- `co_cli/main.py` — pass `exec_approvals_path` in `create_deps()`; startup stale-prune
- `co_cli/_orchestrate.py` — modify `_handle_approvals()` to consult + update persisted approvals
- `co_cli/_commands.py` — add `_cmd_approvals()`, register as `"approvals"` in `COMMANDS`

**Key design decisions:**
- Persistence applies **only to `run_shell_command`** — other tools use the existing in-memory
  `auto_approved_tools` session set (no pattern matching needed for non-shell tools).
- Load approvals ONCE before the approval loop, not per call. Write back only if dirty.
- At `chat_loop()` startup, run `prune_stale()` and save if any entries were pruned.

**`co_cli/deps.py`** addition:
```python
exec_approvals_path: Path = field(
    default_factory=lambda: Path.cwd() / ".co-cli/exec-approvals.json"
)
```

**`_handle_approvals()` changes:** Before prompting for a `run_shell_command` call:
1. `find_approved(cmd, persisted_approvals)` — if found: auto-approve; call `update_last_used()`;
   set dirty flag. Skip prompt.
2. Existing `_is_safe_command` check — unchanged.
3. Fall through to user prompt.
4. On user choice "a": call `derive_pattern(cmd)`, `add_approval()`; set dirty flag;
   also add to `deps.auto_approved_tools` (session fast path).
5. After loop: if dirty, `save_approvals(deps.exec_approvals_path, persisted_approvals)`.

**Done when:**
- `uv run pytest tests/test_commands.py -v` passes (no regressions)
- Manual: approve a shell command with "a" → `.co-cli/exec-approvals.json` is written
- Manual: restart `co chat` → same command auto-approved without prompt

---

## TASK-4 — Temporal Decay Scoring on Recall (P2)

**What:** Add a post-retrieval decay multiplier to `recall_memory()` in `co_cli/tools/memory.py`
so that older memories rank lower than recent ones for the same query.
Formula: `final_score = bm25_score * exp(-ln(2) / half_life_days * age_days)`.
Memories with `decay_protected: true` bypass the multiplier.

**Why:** FTS5 BM25 is the only ranking signal; stale preferences rank equal to today's corrections.
See `TODO-gap-openclaw-analysis.md §3` for full analysis.

**Files:**
- `co_cli/tools/memory.py` — add `_decay_multiplier()`, apply in `recall_memory()` FTS path
- `co_cli/config.py` — add `memory_recall_half_life_days: int = Field(default=30, ge=1)`
  and env var `CO_MEMORY_RECALL_HALF_LIFE_DAYS`
- `co_cli/deps.py` — add `memory_recall_half_life_days: int = 30`
- `co_cli/main.py` — pass `memory_recall_half_life_days=settings.memory_recall_half_life_days`
  in `create_deps()`
- `tests/test_memory_decay.py` — add retrieval decay test cases

**`_decay_multiplier(ts_iso: str, half_life_days: int) -> float`:**
- Parse `ts_iso` via `_parse_created()` (already exists in `memory.py`)
- `age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400`
- `multiplier = math.exp(-math.log(2) / half_life_days * age_days)`
- Return `max(0.0, min(1.0, multiplier))` — clamp to [0.0, 1.0]

**In `recall_memory()` FTS path**, after building `matches` list:
- Pair each `MemoryEntry` with its `SearchResult.score` (confirmed at `knowledge_index.py:101`).
- For each pair: if `m.decay_protected` is False, multiply score by `_decay_multiplier()`.
- Re-sort by adjusted score descending; slice to `max_results`.

**Test cases to add to `tests/test_memory_decay.py`:**
- Two memories with identical content; one `updated` 60 days ago, one 1 day ago: after recall,
  the 1-day-old one ranks first (higher adjusted score).
- A memory with `decay_protected: true` and one without, same age: the protected one is not
  penalized (score unchanged by multiplier).
- `_decay_multiplier` with `age_days=0` returns 1.0.
- `_decay_multiplier` with `age_days=30` and `half_life_days=30` returns approximately 0.5 (±0.01).
- `_decay_multiplier` with a future-dated timestamp (negative age) returns 1.0 (clamped, not > 1.0).

**Done when:** `uv run pytest tests/test_memory_decay.py -v` passes all old and new cases.

---

## TASK-5 — Model Fallback (P2)

**What:** Add `fallback_models: list[str]` to Settings and CoDeps. In `chat_loop()`, on a
provider error, rebuild the agent with the next model in the fallback list and replay the turn.
Maximum one fallback attempt per turn. Context overflow errors are NOT retried.

**Why:** A single configured model means any provider error ends the session. See
`TODO-gap-openclaw-analysis.md §5`.

**Files:**
- `co_cli/config.py` — add `fallback_models` field + comma-split field_validator + env var
- `co_cli/deps.py` — add `fallback_models: list[str] = field(default_factory=list)`
- `co_cli/agent.py` — add `override_model: str | None = None` param to `get_agent()`
- `co_cli/main.py` — capture `pre_turn_history` before `run_turn()`; add fallback loop on error
- `tests/test_agent.py` — verify `fallback_models` field wiring + `override_model` param

**`co_cli/config.py`:**
```python
fallback_models: list[str] = Field(default_factory=list)
# doc comment: "Same-provider model names only. Example (Ollama): ['qwen3:7b', 'llama3.3:latest']"
```
Add `field_validator("fallback_models", mode="before")` using the same comma-split pattern as
`_parse_safe_commands`. Add to `env_map`: `"fallback_models": "CO_FALLBACK_MODELS"`.

**`co_cli/agent.py` — `get_agent()` change:**
Add `override_model: str | None = None`. When provided, substitute for `settings.ollama_model`
(Ollama provider) or `settings.gemini_model` (Gemini provider). Provider stays unchanged.

**`co_cli/main.py` — fallback loop:**
- Capture `pre_turn_history = message_history` BEFORE calling `run_turn()`.
- On `turn_result.outcome == "error"` and `deps.fallback_models` non-empty:
  - Pop next model: `fallback_model = deps.fallback_models.pop(0)`.
  - Rebuild agent via `get_agent(override_model=fallback_model)`.
  - Replay `run_turn()` with `pre_turn_history` (discard `turn_result.messages` — may be partial).
  - Maximum one fallback per turn.

**Test cases in `tests/test_agent.py`:**
- `CoDeps` has field `fallback_models` of type `list[str]` with default `[]`.
- `get_agent(override_model="test-model")` does not raise; returned agent model name equals `"test-model"`.

**Done when:** `uv run pytest tests/test_agent.py -v` passes. Code review confirms `pre_turn_history`
is captured before `run_turn()` and used in the fallback replay call.

---

## TASK-6 — Session Persistence Module (P2)

**What:** Create `co_cli/_session.py` with pure data functions over a JSON file at
`.co-cli/session.json` (mode 0o600). Persists session UUID and compaction count across
restarts within a configurable TTL.

**Why:** Session restarts always generate a new UUID, losing compaction metadata. See
`TODO-gap-openclaw-analysis.md §6`.

**Files:**
- `co_cli/_session.py` — new file
- `tests/test_session.py` — new file

**Functions to implement:**
```
load_session(path: Path) -> dict | None
    Parse JSON; return None on missing or corrupt file. Never raises.

save_session(path: Path, session: dict) -> None
    Write JSON indented. Call os.chmod(path, 0o600) after every write_text() call.

is_fresh(session: dict | None, ttl_minutes: int) -> bool
    Return False if session is None or "last_used_at" field is missing or unparseable.
    If last_used_at is in the future: return True (clock skew guard; log DEBUG warning).
    Return True if age < ttl_minutes; False otherwise.

new_session(model: str) -> dict
    Return {"session_id": uuid4().hex, "model": model,
            "last_used_at": now_iso(), "compaction_count": 0}

touch_session(session: dict) -> dict
    Return new dict with "last_used_at" updated to now. Does not mutate input.

increment_compaction(session: dict) -> dict
    Return new dict with "compaction_count" incremented by 1. Does not mutate input.
```

**Test cases in `tests/test_session.py`:**
- `new_session("gemini-2.0-flash")` returns dict with all 4 required keys; `session_id` is
  a non-empty hex string of length 32.
- `is_fresh(session, ttl_minutes=60)` with `last_used_at = now` → True.
- `is_fresh(session, ttl_minutes=60)` with `last_used_at = 90 min ago` → False.
- `is_fresh(None, 60)` → False.
- `is_fresh` with missing `last_used_at` field → False.
- `is_fresh` with `last_used_at = 10 min in the future` → True (clock skew guard).
- `save_session()` + `load_session()` round-trip: all fields intact.
- `save_session()` sets mode 0o600 — verify `path.stat().st_mode & 0o777 == 0o600`.
- `save_session()` called twice: mode is still 0o600 on second write.
- `touch_session()` returns a new dict with `last_used_at` updated; input is unchanged.
- `increment_compaction()` returns new dict with count +1; input is unchanged.

**Done when:** `uv run pytest tests/test_session.py -v` passes all cases.

---

## TASK-7 — Wire Session Persistence into Chat Loop (P2)

**What:** Integrate `_session.py` into `chat_loop()` in `co_cli/main.py`. Load session at
startup; restore or generate `session_id`; save after each turn; increment compaction count
when compaction fires. Add `session_ttl_minutes` to Settings.

**Why:** The module from TASK-6 has no effect until wired into the chat loop.

**Files:**
- `co_cli/config.py` — add `session_ttl_minutes: int = Field(default=60, ge=1)` +
  env var `CO_SESSION_TTL_MINUTES`
- `co_cli/main.py` — session load/save/touch/increment in `chat_loop()`

**`co_cli/main.py` additions:**
- Before `create_deps()` in `chat_loop()`:
  ```python
  _session_path = Path.cwd() / ".co-cli/session.json"
  _session_data = load_session(_session_path)
  if is_fresh(_session_data, settings.session_ttl_minutes):
      _session_id = _session_data["session_id"]
  else:
      _session_data = new_session(model_name)
      save_session(_session_path, _session_data)
      _session_id = _session_data["session_id"]
  ```
- After each successful turn: `_session_data = touch_session(_session_data)` + `save_session()`.
- When compaction fires: `_session_data = increment_compaction(_session_data)` + `save_session()`.
- Session save must be in the outer `while True` loop body unconditionally — not inside any
  `if mcp_servers:` branch (ensure MCP-fallback path also saves).

**Note:** `model_name` for `new_session()` is resolved from `settings.llm_provider` +
`settings.ollama_model` / `settings.gemini_model` — same logic already in `create_deps()`.

**Done when:** After `co chat` exits and restarts within 60 min, the session_id in logs is
identical. After 60+ min, a new session_id is generated.
`uv run pytest tests/test_agent.py tests/test_commands.py -v` still passes (no regressions).

---

## TASK-8 — Doctor Security Checks (P2)

**What:** Add `SecurityFinding` dataclass and `check_security()` function to `co_cli/status.py`.
Surface findings inline after `co status` and `/status` commands. Empty findings list → no output.

**Why:** No automated way to detect world-readable config files or wildcard exec approvals.
See `TODO-gap-openclaw-analysis.md §7`.

**Dependency:** TASK-2 must ship first (exec approvals module needed for check 3).

**Files:**
- `co_cli/status.py` — add `SecurityFinding`, `check_security()`
- `co_cli/_commands.py` — update `_cmd_status()` to display findings after status table
- `co_cli/main.py` — update `co status` CLI handler to display findings
- `tests/test_status.py` — new file (or add to existing)

**`SecurityFinding` dataclass:**
```python
@dataclass
class SecurityFinding:
    severity: str   # "warn" | "error"
    check_id: str   # short machine-readable identifier (e.g. "user-config-world-readable")
    detail: str     # human-readable description
    remediation: str
```

**`check_security()` — 3 checks, each individually try/except'd:**
1. `SETTINGS_FILE` (`~/.config/co-cli/settings.json`): if exists and `stat().st_mode & 0o004`,
   append finding `check_id="user-config-world-readable"`,
   `remediation="chmod 600 ~/.config/co-cli/settings.json"`.
2. `Path.cwd() / ".co-cli/settings.json"`: same permission check,
   `check_id="project-config-world-readable"`.
3. `Path.cwd() / ".co-cli/exec-approvals.json"`: if exists, load with `_exec_approvals.load_approvals()`
   (lazy import); if any entry has `pattern == "*"`, append finding `check_id="exec-approval-wildcard"`,
   `remediation="/approvals clear <id>"`.

Add optional `_user_config_path` and `_project_config_path` parameters to `check_security()` for
testability (defaults to real paths when None).

**Test cases in `tests/test_status.py`:**
- Temp settings file with mode 0o644 → `check_security(_user_config_path=tmp_path)` returns
  finding with `check_id="user-config-world-readable"`.
- Same file with mode 0o600 → no finding.
- Temp exec-approvals file with `pattern == "*"` entry → finding `check_id="exec-approval-wildcard"`.
- All clean → `check_security()` returns `[]`.

**Done when:** `uv run pytest tests/test_status.py -v` passes all cases. `uv run co status`
shows a WARN line for a world-readable config file and shows nothing when config is 0o600.

---

## TASK-9 — `/new` Slash Command (P2, deferred)

**What:** Add `/new` slash command that checkpoints the current session to a knowledge file
and clears history.

**Why:** No explicit session-close hook. Good exchanges are lost when sessions end.

**Dependency:** `_index_session_summary()` in `co_cli/_history.py` must ship first (currently
deferred — no timeline). Do not start this task until that function exists.

**Files:** `co_cli/_commands.py`

**When unblocked:**
- Add `_cmd_new()`: take last min(15, len(history)) messages; call
  `_run_summarization_with_policy()` + `_index_session_summary()`; if summary succeeds, clear
  history; print confirmation.
- Register as `SlashCommand("new", "Checkpoint session to memory and start fresh", _cmd_new)`.
- Tests: session file written and history cleared on `/new`; no-op on empty history.

**Done when:** `/new` writes a `session-{timestamp}.md` file in `.co-cli/knowledge/` with
`provenance: session` frontmatter and returns `[]` (clears history). `/new` on empty history
prints a no-op message and returns `None`.

---

## End-to-End Verification

After TASK-1 through TASK-8:
```bash
uv run pytest tests/test_commands.py tests/test_exec_approvals.py \
    tests/test_memory_decay.py tests/test_session.py \
    tests/test_status.py tests/test_agent.py -v

# Approval persistence:
uv run co chat
# > run shell command → approve with "a" → exit
# > uv run co chat → same command → no prompt appears

# Session continuity:
uv run co chat  # note session_id in logs
# > exit within 60 min → restart → same session_id

# Security checks:
chmod 644 ~/.config/co-cli/settings.json && uv run co status  # shows WARN
chmod 600 ~/.config/co-cli/settings.json && uv run co status  # clean
```
