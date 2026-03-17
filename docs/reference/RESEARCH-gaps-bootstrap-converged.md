# Bootstrap Gap Analysis — Converged Peer Systems vs `co`

## Scope

This report extends `RESEARCH-gaps-openclaw-bootstrap-vs-co.md` by incorporating evidence from
three additional peer CLI agent systems: gemini-cli, opencode, and aider. The goal is to
identify bootstrap patterns that **two or more** systems converge on — those are the ones
worth adopting, not single-system idiosyncrasies.

Out-of-scope: workspace seed files, repair-oriented doctor, service management, config
metadata, bootstrap budget analysis. These carry skip verdicts from the OpenClaw report and
the new systems do not change that verdict.

## Source Basis

### `co` (as-built, current)

- `co_cli/main.py` — `chat_loop()` bootstrap sequence (lines 80–163 entry point; REPL loop continues)
- `co_cli/_startup_check.py` — `check_startup()` pre-agent gate (41 lines)
- `co_cli/_wiring.py` — `create_deps()` dependency graph construction (115 lines)
- `co_cli/_wakeup.py` — post-agent wakeup tasks: `sync_knowledge()`, `restore_session()`, `run_integration_health()` (94 lines)
- `co_cli/_runtime_check.py` — `check_runtime()` post-agent integration sweep (159 lines)
- `co_cli/_probes.py` — factual probe layer (230 lines)
- `co_cli/_doctor.py` — backward-compat shim; maps `ProbeResult` → `DoctorResult` / `CheckItem`
- `co_cli/config.py` — `load_config()` settings load with layered merge (559 lines)

### Peer Systems

- **gemini-cli** — `packages/cli/src/core/initializer.ts`, `packages/cli/src/core/auth.ts`, `packages/cli/src/gemini.tsx`
- **opencode** — `packages/opencode/src/project/bootstrap.ts`, `packages/opencode/src/project/instance.ts`, `packages/opencode/src/cli/bootstrap.ts`
- **aider** — `aider/main.py` (lines 43–750)
- **openclaw** — previously analysed; findings carry forward from `RESEARCH-gaps-openclaw-bootstrap-vs-co.md`

## Executive Summary

`co` has been significantly refactored since the OpenClaw report. The monolithic bootstrap has
been split into focused modules: `_wiring.py` (dependency graph), `_startup_check.py` (pre-agent
gate), and `_wakeup.py` (post-agent tasks). The three-phase structure is now more explicit in code.

**What changed since the OpenClaw report:**
- `_wakeup.py` and `_wiring.py` are new modules — bootstrapping is no longer inline in `main.py`
- `config.py` now catches JSON parse errors with `try/except` (gap 4.4 partially closed)
- `run_integration_health()` wraps `check_runtime()` and passes `RuntimeCheck` to the banner
- Line numbers in `main.py` have shifted substantially (bootstrap is now lines 80–163, not 249–396)

**What is still open:**

1. `check_startup()` raises `RuntimeError` — user sees a Python traceback on bad API key
2. No readiness verdict model — banner shown unconditionally regardless of `runtime_check.findings`
3. No filesystem preflight — `.co-cli/tasks/`, `.co-cli/memory/`, `session.json` parent assumed writable
4. Best-effort config: JSON parse errors now caught (progress), but Pydantic validation failure still raises raw `ValueError`
5. MCP failure still calls `SystemExit(1)` directly from bootstrap — bypasses any future clean-exit path

All four original gaps remain open (gap 4.4 is partially addressed). The peer-survey evidence
is unchanged.

## 1. Current Bootstrap Sequence (as-built)

The bootstrap in `main.py:chat_loop()` runs in this order:

```
1.  TerminalFrontend()
2.  PromptSession()
3.  TaskStorage(tasks_dir)           ← main.py; .co-cli/tasks/ path computed here, no writability check
4.  TaskRunner(storage=task_storage)
5.  create_deps(task_runner)         ← _wiring.py; knowledge fallback hybrid→fts5→grep
6.  check_startup(deps, frontend)    ← _startup_check.py; HARD GATE — raises RuntimeError
7.  get_agent(...)                   ← agent created; tool surface bound to deps.session
8.  agent context entry (await)      ← MCP servers connect; SystemExit(1) on failure
9.  discover_mcp_tools()
10. _load_skills()
11. sync_knowledge(deps, frontend)   ← _wakeup.py; degrades gracefully on error
12. restore_session(...)             ← _wakeup.py; creates new session on load failure
    frontend.on_status(skill count)  ← status line emitted here; not a wakeup task
13. run_integration_health(deps, frontend) → RuntimeCheck   ← _wakeup.py wraps check_runtime()
14. display_welcome_banner(runtime_check)  ← unconditional; findings not consulted
```

Steps 1–6 are the **pre-agent gate** (blocking). Steps 7–10 are **agent wiring**. Steps 11–14
are **post-agent wakeup** (soft — exceptions caught and degraded). `_wakeup.py` owns steps 11–13.

## 2. DESIGN-probes.md Accuracy vs As-Built Code

The prior DESIGN-doctor.md statement — "`display_welcome_banner` calls `get_status()` independently
via `_doctor.run_doctor()`" — was already corrected when the doc was renamed to DESIGN-probes.md.

The current as-built flow is:

```
run_integration_health(deps, frontend)   # main.py step 13 via _wakeup.py
  └── check_runtime(deps)                # returns RuntimeCheck
       └── probes all integrations
display_welcome_banner(runtime_check)    # main.py step 14
```

`run_integration_health()` is a thin wrapper in `_wakeup.py` (lines 78–94): it calls
`check_runtime()`, catches any exception, returns an empty `RuntimeCheck` on failure, and
returns the full result on success. The banner receives `RuntimeCheck` directly.

The old doc cited `main.py:396` and `main.py:384`. Those lines are now approximately 163 and
160 respectively (bootstrap compressed from lines 249–396 to 80–163 after the refactor).

Everything else in DESIGN-probes.md accurately reflects the as-built code:
- Probe architecture diagram correct
- Data models (`CheckItem`, `DoctorResult`, `RuntimeCheck`) correct
- Probe semantics per integration correct
- Three callsites (bootstrap, capabilities, `co status`) correctly described

## 3. Converged Patterns Across Peer Systems

The table below maps each pattern against the systems that have it. Patterns with 2+ system
coverage are candidates for adoption.

| Pattern | gemini-cli | opencode | aider | openclaw | `co` |
|---------|-----------|---------|-------|---------|------|
| Structured startup result object | ✓ `InitializationResult` | ✓ Promise resolve/reject | ✓ exit code + bool returns | ✓ `WakeResult` | ✗ `None` + raise |
| Clean exit on fatal error (no traceback) | ✓ UI handles `authError` | ✓ Promise rejection | ✓ `return 1` before agent | ✓ `blocked` status, no raise | ✗ `RuntimeError` bubbles |
| Degraded mode taxonomy | ✓ `ValidationRequiredError` non-fatal | ✗ all-or-nothing | ✓ `--no-git` escape hatch | ✓ `degraded` state | ✓ silent degrade, no taxonomy |
| Single readiness verdict before REPL | ✓ `authError === null` | ✓ resolved = ready | ✓ exit 0 = ready | ✓ explicit verdict | ✗ banner unconditional |
| Filesystem / workspace preflight | ✓ `loadTrustedFolders()` | ✓ VCS root detection | ✓ `.git` dir search | ✓ dirs writable check | ✗ no preflight |
| Best-effort config read on validation failure | ✗ strict parse | ✗ strict parse | ✓ deprecated-key check | ✓ `readBestEffortConfig()` | ✓ JSON errors caught; ✗ Pydantic errors still raw |
| Multi-pass config / arg parsing | ✗ single pass | ✗ single pass | ✓ double-parse for config path | ✗ single pass | ✗ single pass |
| Repair hints / doc links on error | ✗ | ✗ | ✓ docs URLs per error type | ✓ explicit repair hints | ✗ raw exception text |

### Pattern details

#### Structured startup result object (4/4 systems)

**gemini-cli** returns `InitializationResult`:
```typescript
interface InitializationResult {
  authError: string | null;
  accountSuspensionInfo: AccountSuspensionInfo | null;
  themeError: string | null;
  shouldOpenAuthDialog: boolean;
}
```
Caller decides: open dialog for OAuth, show error, or proceed normally. Exceptions are caught
inside the initializer and converted to structured fields before the caller sees them.

**opencode** uses Promise resolution as an implicit result: resolved = ready, rejected = failed.
Instance cache is cleared on rejection so retries start fresh.

**aider** returns boolean from each check plus integer exit codes from `main()`. Caller (shell)
gets a machine-readable verdict even if there is no structured object.

**openclaw** `WakeResult` with `status: "ready" | "degraded" | "blocked"`.

`co`'s `check_startup()` returns `None` and raises `RuntimeError` on failure. The caller
(`chat()`) only catches `KeyboardInterrupt`, so any `RuntimeError` surfaces as a Python
traceback to the user. The `_wakeup.py` module exists but does not contain a `WakeResult` —
it owns the post-agent tasks (sync_knowledge, restore_session, run_integration_health) which
do catch and degrade gracefully, but the pre-agent gate is still raise-on-error.

#### Clean exit on fatal error (4/4 systems)

Every peer handles fatal startup errors without producing a raw traceback:
- gemini-cli: auth errors → `authError` field → UI dialog, not crash
- opencode: rejected Promise → error page, not crash
- aider: explicit `return 1` before agent creation; user sees a message, not a stack trace
- openclaw: `blocked` status → friendly message, clean exit

`co` currently: `check_startup()` raises `RuntimeError`; `chat()` wraps only
`asyncio.run(chat_loop())` in a `try/except KeyboardInterrupt`. A bad API key produces:
```
RuntimeError: GEMINI_API_KEY not set — required for Gemini provider
```
as a raw Python traceback printed by Typer.

#### Degraded mode taxonomy (3/4 systems)

gemini-cli distinguishes `ValidationRequiredError` (OAuth in progress) as explicitly non-fatal —
the UI loads and shows a dialog. Theme errors are also non-blocking.

aider's `--no-git` flag skips the git root check entirely; tool continues without VCS awareness.

openclaw has an explicit `degraded` state separate from `blocked` — the agent starts but with
capability warnings surfaced before the first prompt.

`co` already degrades silently: knowledge falls back `hybrid → fts5 → grep` in `create_deps()`
(`_wiring.py`), MCP fails hard with `SystemExit(1)` on agent context entry, and `check_startup()`
only hard-fails on provider and role-model errors. The degradation behavior is correct but there
is no named taxonomy for it. A user cannot tell whether `co` is fully operational,
degraded-but-usable, or blocked without reading every status line emitted during bootstrap.

#### Single readiness verdict (3/4 systems)

gemini-cli: `authError === null && !shouldOpenAuthDialog` → ready.
aider: exit code 0 → ready; `chat()` is reachable.
openclaw: `WakeResult.status === "ready"` → banner shown; `blocked` → exit.

`co`: the banner is shown unconditionally after `display_welcome_banner(runtime_check)`.
Whether `runtime_check.findings` has errors, whether knowledge fell back to grep, whether MCP
failed — none of that currently changes the banner verdict. The user sees the same banner
regardless of degraded state. `RuntimeCheck` has both `findings` (non-ok probes) and
`fallbacks` (active degraded modes) populated by `check_runtime()`, but the banner ignores them.

#### Filesystem / workspace preflight (3/4 systems)

gemini-cli calls `loadTrustedFolders()` and validates workspace paths before agent creation.
opencode detects VCS root and resolves to real path — startup fails cleanly if the working
directory is invalid.
aider searches parent directories for `.git` and builds its config search path from the result.

`co` constructs these paths in two different places, neither of which preflights writability:
- `tasks_dir = Path.cwd() / ".co-cli" / "tasks"` — computed in `main.py` at line 96;
  `TaskStorage(tasks_dir)` is constructed immediately at line 97, before `create_deps()` is called.
- `session_path = Path.cwd() / ".co-cli" / "session.json"` — local variable in `main.py` at line 155.
- `.co-cli/memory/` and library dirs — resolved inside `_wiring.py` (`create_deps()`), lines 91–92.

None of the three paths are checked for writability before use. The first write attempt fails
with a raw `OSError`.

#### Best-effort config on validation failure (2/4 systems) — partially addressed

aider explicitly checks for deprecated `yes:` key in config files before Pydantic/argparse
validation, returns exit code 1 with a clear message, and includes documentation URLs.

openclaw has `readBestEffortConfig()` that captures parse and validation errors as structured
warnings before re-raising, enabling a doctor-mode diagnostic.

**Current `co` state (as-built):** `config.py:load_config()` now wraps the JSON read for both
user config (lines 510–517) and project config (lines 520–526) in `try/except` blocks that
catch JSON errors, print a warning, and continue with defaults. This closes the JSON parse
failure case. However, Pydantic's `Settings.model_validate(data)` (line 529) still raises a
raw `ValidationError` if the merged data fails field validation — the user sees a Pydantic
traceback with no indication of which settings file caused the problem.

The remaining scope for gap 4.4 is: catch `ValidationError` from `model_validate()`, extract
the field path and value from the error, and re-raise with a message that names the offending
settings file and field.

## 4. Gap Matrix

| Gap | Evidence | Severity | Status | Remaining effort |
|-----|---------|---------|--------|-----------------|
| `RuntimeError` bubbles as traceback on bad API key | 4/4 systems catch and convert | High — visible UX regression | Open | Wrap `check_startup`, return `WakeResult` |
| No readiness verdict — banner unconditional | 3/4 systems have explicit verdict | Medium — user cannot assess state | Open | Feed `runtime_check.findings` + `fallbacks` into banner output |
| No filesystem preflight for `.co-cli/` dirs | 3/4 systems preflight paths | Medium — silent OSError on first write | Open | tasks_dir check in `main.py` before `TaskStorage`; memory check in `_wiring.py`; session_path check in `main.py` before `restore_session` |
| No best-effort config snapshot on Pydantic failure | 2/4 systems capture parse errors | Medium — JSON errors now caught; Pydantic trace still raw | Partial | Catch `ValidationError`, annotate with file + field path |
| MCP failure exits with `SystemExit(1)`, no graceful degraded | aider and openclaw both degrade | Low — MCP is optional by design | Open | Requires degrade-and-continue for agent context entry |

## 5. Recommended Adoption

These are the same four adoptions recommended by the OpenClaw report, now confirmed by the
broader survey. The implementation approach is updated to reflect the refactored module layout.

### 5.1 Structured startup result + clean exit

**Verdict: adopt.** 4/4 evidence.

Replace `check_startup() -> None` (raises on error) with a function returning a `WakeResult`
where `WakeResult.status` is `"ready" | "degraded" | "blocked"`. The natural home is
`_startup_check.py` since that is already the pre-agent gate.

```
WakeResult:
  status:   "ready" | "degraded" | "blocked"
  blockers: list[str]   # shown + exit cleanly
  degraded: list[str]   # shown + continue

Policy:
  blocked  → emit blockers, return cleanly — no raise, no traceback
  degraded → emit degraded list, continue
  ready    → continue (no output unless --verbose)
```

`chat()` wraps `asyncio.run(chat_loop())` and currently catches only `KeyboardInterrupt`. After
this change it also handles `WakeResult.status == "blocked"` with a clean `SystemExit(0)` and
a user-friendly message.

Note: `_wakeup.py` already exists but it owns post-agent tasks. `WakeResult` belongs in
`_startup_check.py` as the pre-agent gate contract, not in `_wakeup.py`.

### 5.2 Single readiness verdict in banner

**Verdict: adopt.** 3/4 evidence.

`display_welcome_banner(runtime_check)` already receives `RuntimeCheck` from step 12
(`run_integration_health()`). The missing piece: the banner does not change its output based
on `runtime_check.findings` or `runtime_check.fallbacks`.

After this change: if `findings` is non-empty → banner says "ready (degraded)"; if
`fallbacks` is non-empty → banner lists active fallbacks; if both empty → banner says "ready".
When gap 5.1 is also closed, the combined `WakeResult` and `RuntimeCheck` feed a single verdict line.

### 5.3 Filesystem preflight in startup check

**Verdict: adopt.** 3/4 evidence.

Before use, verify that the paths `co` is about to write are writable. Because `tasks_dir`
and `session_path` are local variables in `main.py` (not accessible via `deps.config`), the
checks must happen where those variables are constructed — not inside `check_startup()` or
`_wiring.py`:

```
In main.py, before TaskStorage(tasks_dir):
  - .co-cli/tasks/ parent dir writable   → blocked if fails (tasks are non-optional)
    (currently line 97; preflight goes at line 96 after tasks_dir is computed)

In _wiring.py / create_deps():
  - .co-cli/memory/ writable or creatable → degraded if missing (memory is optional)

In main.py, before restore_session():
  - session.json parent writable          → degraded if missing (session is optional)
    (currently line 159; preflight goes at line 155 after session_path is computed)
```

Skills dir and library dir produce `degraded` only — they are optional. These three checks
catch the `OSError` that currently surfaces as a raw exception during the first write.

### 5.4 Best-effort config snapshot on Pydantic failure

**Verdict: adopt, remaining scope.** 2/4 evidence, but high-severity UX failure.

The JSON parse error case is already handled in `config.py` (lines 510–526). The remaining
scope: wrap `Settings.model_validate(data)` (line 529) in a `try/except ValidationError` block.
Extract the failing field path and value from the `ValidationError`. Re-raise a `ValueError`
with a message of the form:

```
settings.json validation failed: field 'role_models.reasoning[0].model' has invalid value 'bad-value'
  Check: ~/.config/co-cli/settings.json and .co-cli/settings.json
```

No snapshot cache, no projection layer. Just a catch + annotated re-raise. OpenClaw's full
`readBestEffortConfig()` is out of scope.

## 6. What Does Not Change

- `_probes.py` is the correct probe layer; none of the peers improve on the separation of
  pure probe functions from policy callers.
- `check_runtime()` stays as the post-agent integration sweep wrapped by `run_integration_health()`
  in `_wakeup.py`; it is not merged into the pre-agent startup check.
- `_wakeup.py` owns post-agent tasks (`sync_knowledge`, `restore_session`, `run_integration_health`);
  `WakeResult` and filesystem preflight belong in `_startup_check.py`, not here.
- The "degrade and continue" strategy for optional integrations is correct and confirmed by
  gemini-cli and aider.
- Agent instantiation order does not change.
- The three-phase bootstrap structure (pre-agent gate → agent wiring → post-agent wakeup)
  is sound and confirmed by peer system patterns.

## 7. What to Skip

The broader peer survey introduces two new patterns that are out of scope for `co`:

| Pattern | System | Reason to skip |
|---------|--------|----------------|
| Multi-pass arg/config parsing | aider | aider has 15+ config file locations; co has two (project + user). Single-pass Pydantic is correct for this scope. |
| Instance caching / restart-on-failure | opencode | opencode is embedded in an IDE and reconnects. `co` is a CLI that exits on failure. No persistent state to recover. |
| Auth dialog (non-blocking auth error) | gemini-cli | gemini-cli has a TUI with modal dialogs. `co`'s REPL has no dialog layer; blocking on auth error and showing a clear message is correct. |
| All-or-nothing sequential init | opencode | opencode has no optional components. `co`'s "degrade and continue" strategy is intentionally soft. Adopting all-or-nothing would regress MCP and knowledge degradation. |

## 8. Relationship to Prior Research

The OpenClaw report (`RESEARCH-gaps-openclaw-bootstrap-vs-co.md`) identified the same four
gaps (sections 3.1, 4.1, 5.5, 7.1–7.4). This report confirms all four with independent
evidence and adds two implementation details:

1. The `display_welcome_banner(runtime_check)` wire-up described as "missing" in the OpenClaw
   report is now in place via `run_integration_health()` in `_wakeup.py`. The banner verdict
   integration (gap 5.2 here) is the remaining piece — not the wire-up.

2. MCP failure is a fifth gap not covered by the OpenClaw report: `SystemExit(1)` on MCP
   connection failure (agent context entry, `main.py` step 7) bypasses any future clean-exit
   path. After `WakeResult` is introduced, MCP failure should flow through the same
   `blocked`/`degraded` taxonomy rather than calling `SystemExit` directly from bootstrap.

**Since the original report, gap 4.4 (best-effort config) has been partially addressed**: JSON
parse errors in user and project `settings.json` are now caught and warned before falling back
to defaults. The Pydantic `ValidationError` case (malformed field values that parse as valid
JSON) remains unhandled.
