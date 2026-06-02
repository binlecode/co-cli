# google-auth-least-privilege

> Make co's Google credential path best-practice: least-privilege scopes, a terminal
> actionable error on scope/auth failure (no more silent retries), and a first-class
> `co google auth` setup command so users stop hand-running gcloud and editing settings.json.

## Context

A live session traced the Google tool surface end to end (config → registration → visibility →
call return) and exercised the real credential path. The **tool surface** (deferred, config-gated,
monomorphic per-operation tools) is sound and ahead of peers — out of scope here. The **credential
acquisition / failure path** is the weak link, and three issues were observed against the running
system, not imagined:

### Observed failure modes (live, this session — source-confirmed)

1. **gcloud-ADC reliance is a category error for Workspace data.** `ensure_google_credentials`
   (`co_cli/tools/google/_auth.py:20-73`) resolves via ADC paths and `gcloud auth
   application-default login` first. A direct scope probe against the resolved token showed
   `gmail.modify`, `drive.readonly`, AND `calendar.readonly` **all rejected with `invalid_scope`**,
   while `cloud-platform` / `userinfo.email` were granted — i.e. gcloud's built-in OAuth client is
   categorically barred from Workspace user scopes. ADC is built for cloud/service access, not
   end-user Gmail/Drive/Calendar. *(TASK-3 removes the gcloud-acquisition legs entirely and makes
   `co google auth` the sole acquisition path — see Scope and Resolved Decision 3.)*
2. **Over-broad scope (least-privilege violation).** `ALL_GOOGLE_SCOPES` (`_auth.py:13-17`) requests
   `gmail.modify` — a *restricted* scope permitting modify/delete of mail — for tools that only
   list, search, and draft. API usage confirms the minimum: `messages.list` + `messages.get`
   (`gmail.py:79,124,31`) need only `gmail.readonly`; `drafts.create` (`gmail.py:181`) needs only
   `gmail.compose`. `drive.readonly` and `calendar.readonly` are already minimal.
3. **Scope/auth failure is silently retried, never surfaced.** When the credential lacks a scope,
   the API auto-refresh raises a `google.auth` `RefreshError` (no HTTP status). It reaches
   `handle_google_api_error` (`co_cli/tools/tool_io.py:309-331`), whose `http_status_code(e)` returns
   `None`, so it falls through to `raise ModelRetry(...)` — classifying a **permanent config error as
   retryable**. The model burns its retry budget (3 on reads) on a failure no retry can fix, and the
   user never gets the actionable "re-auth with these scopes" message. The resolver also
   short-circuits on any existing token file (`_auth.py:39-41`) without validating scopes, so a
   stale/wrong-scope token blocks the correct flow indefinitely.

### Current-state facts (source-confirmed)

- Credential resolution order and scopes: `_auth.py:13-73`. Per-turn visibility gate `_google_available`:
  `_auth.py:76-90`. Service build + not-configured return: `_auth.py:111-133`.
- Error routing: `handle_google_api_error` `tool_io.py:309-331`; status extraction `tool_io.py:289-306`;
  `tool_error`/`tool_output` `tool_io.py:239-286`.
- Gmail scope needs: read = `messages.list/get` (`gmail.py:79,124,31`); draft = `drafts.create`
  (`gmail.py:181`). Drive/Calendar are read-only.
- CLI: Typer `app` in `co_cli/main.py`; subcommand-group pattern is `dream_app = typer.Typer()` +
  `app.add_typer(dream_app, name="dream")` (`co_cli/commands/dream.py:16`, `main.py:102,109`).
- `google-auth-oauthlib` is **already a dependency** (`pyproject.toml:14`) — `InstalledAppFlow` is
  available; no new dependency and no gcloud requirement for the setup command.
- Token cache is session-scoped on `deps.session.google` (`deps.py:127-132`); `co google auth` runs
  out-of-process, so a fresh `co chat` resolves the new token — no in-session invalidation needed.

## Problem & Outcome

**Problem:** co requests an over-broad restricted Gmail scope; a scope/auth misconfiguration is
mis-handled as a retryable error and never surfaced actionably; and obtaining a working Workspace
credential requires the user to hand-run gcloud with a custom client and hand-edit `settings.json`.

**Outcome:** co requests only least-privilege scopes (`gmail.readonly` + `gmail.compose` +
`drive.readonly` + `calendar.readonly`); a scope/auth failure returns a terminal, actionable
re-auth message instead of silent `ModelRetry` churn; and `co google auth` runs the browser OAuth
flow with a user-supplied client, writes the token, and is the documented one-command setup.

**Failure cost:** without (1) co holds far more Gmail authority than it uses (`gmail.modify` permits
delete/trash/label rewrites of any mail) — a larger blast radius. (Note: this is a blast-radius win,
not a verification win — `gmail.readonly`, `gmail.compose`, and `gmail.modify` are all *restricted*
scopes requiring the same Google CASA assessment, so the verification burden is unchanged.) Without
(2) a misconfigured credential
manifests as the agent silently retrying and giving up, with no signal to the user about what to
fix; without (3) every user must rediscover the gcloud-custom-client + settings.json dance this
session just went through.

## Scope

**In:** least-privilege scope set in `_auth.py`; terminal+actionable classification of
`RefreshError`/`invalid_scope` in the Google error path; making `co google auth` (an
`InstalledAppFlow` Typer subcommand) the **sole credential-acquisition path** by removing the
gcloud-ADC-login and ADC-auto-copy legs from `ensure_google_credentials`; redirecting the
not-configured messages to `co google auth`; a `co google check` credential-verify command;
behavioral tests for each.

**Out:** the credential-*reading* steps (explicit `google_credentials_path` + default
`GOOGLE_TOKEN_PATH`) — kept, they work for any correctly-scoped token; per-service *incremental*
consent (the single combined-scope token model stays — only the scope *set* shrinks); shipping a
co-verified OAuth client (requires Google's restricted-scope security assessment — infeasible for a
local-first OSS tool; called out as inherent friction); the tool surface itself
(deferred/monomorphic — already sound); Google API enablement / consent-screen setup (user's
Cloud-Console responsibility).

**Shippable increments:** TASK-1 + TASK-2 (least-privilege scopes + honest terminal failure) stand
alone with no prerequisite and can ship even if TASK-3's interactive browser leg needs manual
verification time. TASK-3 (sole acquisition path + setup command) and TASK-4 (verify) build on them.

## Behavioral Constraints

- **Least privilege:** the scope set is the minimal floor for what the tools call (read + draft). No
  `gmail.modify` (drops delete/trash/label-rewrite authority), no write Drive/Calendar scopes.
  `gmail.compose` is the narrowest scope that permits `drafts.create` — it inherently also grants
  send capability (no draft-only, no-send scope exists), which co never invokes. We do not request
  the standalone `gmail.send` scope.
- **Terminal means terminal:** a scope/auth config failure returns a `tool_error` (terminal) — never
  `ModelRetry`. Transient failures (403/404/429/5xx) keep retrying as today.
- **Actionable error:** the failure message names the missing-capability cause and points to
  `co google auth` (and the required scopes), so the user can self-serve.
- **Single acquisition path:** `co google auth` is the only way co *acquires* a credential.
  `ensure_google_credentials` no longer runs `gcloud` or auto-copies ADC (both bring in
  gcloud-scoped tokens that cannot grant Workspace scopes); it only *reads* an explicit path or the
  default token file, else returns None → not-configured message points at `co google auth`.
- **No secrets in logs/output:** the setup, verify, and error messages never print
  `client_secret`/`refresh_token` (asserted at the command boundary).
- **Out-of-process setup:** `co google auth` / `co google check` write/read the token file and exit;
  they do not require or mutate a running chat session.

## High-Level Design

### TASK-1 — least-privilege scope set
Replace `gmail.modify` in `ALL_GOOGLE_SCOPES` (`_auth.py:13-17`) with `gmail.readonly` +
`gmail.compose`. Final set: `gmail.readonly`, `gmail.compose`, `drive.readonly`, `calendar.readonly`.
No call-site changes — `drafts.create` works under `gmail.compose`, `messages.list/get` under
`gmail.readonly`. This is the single source of truth the setup command (TASK-3) and credential load
both read.

### TASK-2 — terminal, actionable error on scope/auth failure
In `handle_google_api_error`, classify a `google.auth.exceptions.RefreshError` by **type first**
(`isinstance` — authoritative; `RefreshError` carries no HTTP status and renders as a stringified
tuple, so the `invalid_scope` substring is only a secondary signal for non-RefreshError shapes) and
return a **terminal** `tool_error(..., ctx=ctx)` whose message states the credential is missing
required scopes and instructs the user to run `co google auth` to re-authorize with `gmail.readonly`,
`gmail.compose`, `drive.readonly`, `calendar.readonly`. The new branch is inserted **right before**
the catch-all `raise ModelRetry(...)` at `tool_io.py:331`; the 401/403/404/429/5xx branches
(`:321-330`) are unchanged so transient failures keep retrying. `handle_google_api_error` is already
Google-specific, so importing `google.auth.exceptions` there is fine.

### TASK-3 — `co google auth` as the sole acquisition path
Two coupled changes:

**(a) Remove the gcloud-acquisition legs** from `ensure_google_credentials` (`_auth.py:43-73`): delete
the ADC auto-copy (step 3) and the `gcloud auth application-default login` leg (step 4). The function
becomes: explicit `credentials_path` if it exists → default `GOOGLE_TOKEN_PATH` if it exists → else
`None`. This removes the category error at the root (gcloud's client cannot grant Workspace scopes).
Then clean up what the removal orphans (CD-m-8): drop the now-unused `shutil`/`subprocess` imports and
the `ADC_PATH` import (`_auth.py:3-10`), and fix the now-stale `ensure_google_credentials` docstring
(`:24-31`) and the `_google_available` "interactive gcloud login" comment (`:80`). Redirect the
not-configured strings (`gmail.py:14-18`, `drive.py:13-16`, `calendar.py:13-16`) to say *"run
`co google auth`"* instead of the gcloud command. Finally, remove the now-misleading ADC branch from
`bootstrap/check.py:_check_google` (`:263-265`) — and the `adc_path` arg + its pass-site (`:322`),
dropping `ADC_PATH` from the `check.py:315` import — so `co doctor` reports not-configured for an
ADC-only state instead of green-lighting it (CD-m-9). With those three call sites gone, `ADC_PATH`
has **zero** remaining consumers, so also delete its definition at `config/core.py:35` — a stale
module constant that ruff will *not* flag (unused constants aren't caught), so it must be removed by
hand or it survives as silent dead code (CD-m-10).

**(b) Add the `co google auth` command.** A `google` Typer group (`app.add_typer(google_app,
name="google")`; single-command group, **no** no-arg callback — Typer shows help by default):
- Options: `--client-secret` (path to the user's OAuth *Desktop-app* client JSON; default
  `config.google_client_secret_path`), `--credentials-path` (token write target; default
  `GOOGLE_TOKEN_PATH`).
- Runs `InstalledAppFlow.from_client_secrets_file(client_secret, ALL_GOOGLE_SCOPES).run_local_server(port=0)`
  (browser account-pick + consent), then writes `creds.to_json()` (authorized_user format
  `from_authorized_user_file` reads) to the target.
- Prints a success line naming the path + `creds.scopes` — **never** the json blob, `creds.token`,
  `refresh_token`, or `client_secret`.
- On a missing/invalid client-secret file, prints an actionable error **enumerating** the
  Cloud-Console prerequisites (create project → enable Gmail/Drive/Calendar APIs → OAuth consent
  screen + add self as test user → create Desktop OAuth client → download json to
  `google_client_secret_path`).

Shares `ALL_GOOGLE_SCOPES` with TASK-1 so requested and required scopes can never drift. Writing to
`GOOGLE_TOKEN_PATH` by default means resolution step 2 picks it up with no settings.json edit.

### TASK-4 — `co google check` credential verify
A second command in the `google` group: loads the configured credential
(`get_cached_google_creds`-style read of the resolved path), attempts a scope-validating refresh
(`creds.refresh(Request())`), and prints **granted-vs-required** scopes plus next-step guidance —
reusing TASK-2's `RefreshError`/`invalid_scope` classification so a scope shortfall yields the same
actionable "run `co google auth`" message. This closes the post-setup verification loop (the
dominant pain this session) without starting a chat. Never prints secrets.

## Tasks

### ✓ DONE TASK-1 — least-privilege scope set
- **files:** `co_cli/tools/google/_auth.py`, `tests/test_flow_google_auth.py`
- **prerequisites:** none
- **done_when:** a test asserts `ALL_GOOGLE_SCOPES` equals exactly
  `{gmail.readonly, gmail.compose, drive.readonly, calendar.readonly}` and contains no `gmail.modify`
  / `gmail.send` / any write scope; existing `tests/test_flow_google_auth.py` (config→return path)
  still passes green.
- **success_signal:** the Google consent screen requests read + draft only — no mail modify/delete.

### ✓ DONE TASK-2 — terminal, actionable error on scope/auth failure
- **files:** `co_cli/tools/tool_io.py`, `tests/test_flow_google_auth.py`
- **prerequisites:** none
- **done_when:** a test constructs a real `google.auth.exceptions.RefreshError` (the `isinstance`
  type path, payload-dict-independent), passes it to `handle_google_api_error(..., ctx=ctx)`, and
  asserts the result is a terminal `ToolReturn` with `metadata == {"error": True}` whose
  `return_value` names `co google auth` and the required scopes — and that the call does **not** raise
  `ModelRetry`. A second assertion confirms a transient case (429-bearing exception) still raises
  `ModelRetry` (retry path preserved).
- **success_signal:** a wrong-scope credential yields one clear "re-authorize with co google auth"
  message instead of silent retries.

### ✓ DONE TASK-3 — `co google auth` as sole acquisition path
- **files:** `co_cli/commands/google.py`, `co_cli/main.py`, `co_cli/config/core.py`,
  `co_cli/tools/google/_auth.py`, `co_cli/tools/google/gmail.py`, `co_cli/tools/google/drive.py`,
  `co_cli/tools/google/calendar.py`, `co_cli/bootstrap/check.py`,
  `tests/test_flow_google_auth_command.py`
- **prerequisites:** TASK-1
- **done_when:** (a) `uv run co google auth --help` exits 0 and lists `--client-secret` /
  `--credentials-path`; (b) a token round-trip test — given a real `Credentials` object **with
  `client_id`/`client_secret` populated**, the write helper produces a file that
  `Credentials.from_authorized_user_file(path, scopes=ALL_GOOGLE_SCOPES)` loads without error;
  (c) the success-print path asserts stdout contains the path + scopes but **no** `refresh_token` /
  `client_secret` substring; (d) a test asserts `ensure_google_credentials` with no readable token
  returns `None` **without** invoking `gcloud`/ADC-copy (the acquisition legs are gone), and the
  not-configured strings name `co google auth`; (e) the missing-client-secret error lists the
  Cloud-Console prerequisite steps; (f) lint is clean after the orphaned-import removal, the
  `ADC_PATH` constant is deleted from `config/core.py` (zero remaining consumers; not ruff-caught), and
  `_check_google` returns not-configured (not "configured (ADC)") for an ADC-only state. The
  interactive `run_local_server` leg is verified manually (Testing).
- **success_signal:** a user runs `co google auth`, logs into the chosen account, and the Google
  tools work on the next `co chat` — no gcloud, no settings.json editing.

### ✓ DONE TASK-4 — `co google check` credential verify
- **files:** `co_cli/commands/google.py`, `tests/test_flow_google_auth_command.py`
- **prerequisites:** TASK-3
- **done_when:** `uv run co google check --help` exits 0; AND a test of the report helper — given a
  granted-scope set and the required set, it prints the granted-vs-required diff and, on a shortfall,
  the actionable "run `co google auth`" guidance (reusing TASK-2's classification) with no secrets in
  output. The live refresh against a real token is verified manually (Testing).
- **success_signal:** a user runs `co google check` and sees, before ever opening chat, whether the
  credential satisfies co's required scopes and exactly what to do if not.

## Testing

- TASK-1/2: extend `tests/test_flow_google_auth.py` (real `Settings`/`CoDeps`/`RunContext`, no mocks).
  TASK-2 builds a real `RefreshError` as input data (an input literal, not a behavior-replacing fake)
  and asserts classification; transient-path assertion guards no regression.
- TASK-3: new `tests/test_flow_google_auth_command.py` — CLI `--help` registration smoke via the
  Typer runner; a token round-trip test (`Credentials` with `client_id`/`client_secret` →
  `to_json()` → `from_authorized_user_file`); a no-secrets stdout assertion; and a test that
  `ensure_google_credentials` returns `None` (no gcloud/ADC-copy) when no token is readable. The
  interactive browser flow (`run_local_server`) is verified manually once: run `co google auth` with
  a real Desktop client, confirm a token is written and `google_gmail_search(in:sent)` then works.
- TASK-4: report-helper unit test (granted-vs-required diff + actionable shortfall message,
  no secrets); the live `creds.refresh()` verify is the same manual run as TASK-3.
- No `docs/specs/` in any task `files:` — `sync-doc` updates the tools/auth spec prose post-delivery.
- Per project policy: pytest runs use `-x` and tee to a timestamped `.pytest-logs/` file.

## Resolved Decisions (Gate-1 C1)

1. **`--client-secret` default → `Settings.google_client_secret_path` config field** (new, in
   `co_cli/config/core.py`, mirroring `google_credentials_path`), defaulting to the
   `~/env-secrets/google_client_secret.json` convention. Discoverable + overridable; keeps the secret
   out of the repo. (PO-m-1 / CD-m-7)
2. **`co google auth` writes to `GOOGLE_TOKEN_PATH` by default and never auto-edits settings.json.**
   Resolution step 2 picks that path up with zero config change; for a custom `--credentials-path`
   the command only *prints* the `google_credentials_path` line to add. (PO-m-1)
3. **Acquisition legs removed, reading legs kept** (PO-M-1): `ensure_google_credentials` keeps the
   explicit-path and default-token-file *reads*; the ADC auto-copy and gcloud-login *acquisition*
   legs are deleted. `co google auth` is the sole acquisition path.

---

## Final — Team Lead

Plan approved. Converged at Cycle C2 — both Core Dev and PO returned `Blocking: none`. C1 adopted PO-M-1 (remove the gcloud/ADC acquisition legs — `co google auth` becomes the sole acquisition path), PO-M-2 (new TASK-4 `co google check` verify), and all seven CD minors + three PO minors. C2 adopted CD-m-8 (remove orphaned `shutil`/`subprocess`/`ADC_PATH` imports + stale docstrings) and CD-m-9 (remove the misleading ADC branch from `co doctor`). Open Questions resolved (client-secret config field; write to `GOOGLE_TOKEN_PATH`, no settings auto-edit).

**Gate-1 review addendum (post-approval):** adopted three corrections — CD-m-10 (delete the now-orphaned `ADC_PATH` constant at `config/core.py:35`; ruff does not flag unused module constants, so it would survive as silent dead code), and two rationale-accuracy fixes: `gmail.compose` is the least-privilege floor for `drafts.create` but inherently grants send capability (no draft-only scope exists), and the `modify → readonly+compose` switch is a blast-radius win, not a verification win (all three are restricted scopes under the same CASA assessment).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev google-auth-least-privilege`

---

## Delivery Summary — 2026-06-02

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `ALL_GOOGLE_SCOPES` == {gmail.readonly, gmail.compose, drive.readonly, calendar.readonly}, no modify/send/write; existing tests green | ✓ pass |
| TASK-2 | real `RefreshError` → terminal `ToolReturn` (`metadata={"error": True}`) naming `co google auth` + scopes, no `ModelRetry`; 429 still `ModelRetry` | ✓ pass |
| TASK-3 | (a) `auth --help` exits 0 w/ both opts; (b) token round-trip; (c) no-secrets success; (d) `ensure_google_credentials` → None w/o gcloud/ADC + not-configured names `co google auth`; (e) prereq steps listed; (f) lint clean, `ADC_PATH` const deleted, `_check_google` no ADC branch | ✓ pass |
| TASK-4 | `check --help` exits 0; report helper granted-vs-required diff + shortfall guidance, no secrets | ✓ pass |

**Tests:** scoped — 36 passed, 0 failed (`test_flow_google_auth.py`, `test_flow_google_auth_command.py`, `test_agent_build_task_agent.py`, `test_flow_bootstrap_config_loading.py`, `test_tool_io.py`). 18 Google-auth-specific tests added.
**Doc Sync:** fixed — `config.md` (removed `ADC_PATH` ×2, clarified `google_credentials_path`, added `google_client_secret_path` row); `tools.md` (Google gate is per-turn visibility, not registration); `agents.md` (drop-out via `_google_available`, test pointer → `test_flow_google_auth.py`).

**Scope addition (TL decision, mid-delivery):** Integration surfaced a design gap the plan missed — the Google tools' `requires_config="google_credentials_path"` registration gate is never satisfied by `co google auth` (which writes `GOOGLE_TOKEN_PATH` without setting that field), so the freshly-authorized token would never reach a registered tool. Escalated; TL chose **token-existence in `check_fn`**: dropped `requires_config` from all 7 Google tools and extended `_google_available` to surface them pre-resolution only when a credential source exists on disk (explicit `google_credentials_path` file OR default `GOOGLE_TOKEN_PATH`). This delivers TASK-3's success_signal (tools work next `co chat`, no settings.json edit) while keeping them hidden for users with no Google setup. Files added to TASK-3 scope: `co_cli/tools/google/{drive,gmail,calendar}.py` (gate removal), `_auth.py` (`_google_available`), `docs/specs/agents.md` + `tools.md` (mechanism).

**Manual-verify (per plan — interactive/live, not automatable):**
- TASK-3: run `co google auth` with a real Desktop client → browser consent → confirm token written to `GOOGLE_TOKEN_PATH` and `google_gmail_search(in:sent)` works on next `co chat`.
- TASK-4: run `co google check` against the real token → confirm granted-vs-required scope diff and live `creds.refresh()`.

**Overall: DELIVERED**
All four tasks pass `done_when`; lint clean; scoped tests green; doc sync done. The mid-delivery registration-gate gap was escalated and resolved per TL decision. Two interactive legs remain manual-verify per the plan's Testing section.

**Next step:** `/review-impl google-auth-least-privilege` — full suite + evidence scan + behavioral verification → verdict appended to plan.

---

## Implementation Review — 2026-06-02

Scope: TASK-1, TASK-2, TASK-3, TASK-4 (all `✓ DONE`). Stance: issues exist — PASS is earned. Evidence collected by four parallel per-task subagents, reconciled by an adversarial cold-read pass.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `ALL_GOOGLE_SCOPES` == {gmail.readonly, gmail.compose, drive.readonly, calendar.readonly}; no modify/send/write | ✓ pass | `_auth.py:11-16` exactly four scopes, no modify/send/write. Test `test_flow_google_auth.py:38-55` asserts set equality + absence of modify/send/full-read scopes |
| TASK-2 | real `RefreshError` → terminal `ToolReturn` (`metadata={"error": True}`) naming `co google auth` + scopes, no `ModelRetry`; 429 still `ModelRetry` | ✓ pass | `tool_io.py:328-334` — `isinstance(RefreshError)` is the FIRST branch, returns terminal `tool_error` (→`metadata={"error":True}` via `tool_output` `:259`), names `co google auth` + 4 scopes, sits before catch-all `raise ModelRetry` `:346`. 403/404/429/5xx unchanged `:338-345`. Tests `test_refresh_error_is_terminal_and_actionable`, `test_transient_429_still_retries` green. `git diff HEAD` = +15/-0: 401 was *already* terminal pre-change, not new scope |
| TASK-3 | (a) `auth --help` exits 0 w/ both opts; (b) token round-trip; (c) no-secrets success; (d) `ensure_google_credentials`→None w/o gcloud/ADC + not-configured names `co google auth`; (e) prereq steps; (f) lint clean, `ADC_PATH` deleted, `_check_google` no ADC branch | ✓ pass | (a) verified live — both opts listed. (b) `_write_token` `google.py:54-61` (atomic + chmod 0600), round-trip test green. (c) `_auth_success_message` `:45-51` path+scopes only. (d) `ensure_google_credentials` `_auth.py:19-46` = explicit→default→None; `grep subprocess\|shutil\|gcloud\|ADC` only docstring negations; not-configured strings `gmail.py:14`,`drive.py:13`,`calendar.py:13` name `co google auth`. (e) `_client_secret_prerequisites` `:28-42` 5 Cloud-Console steps. (f) `grep -rn ADC_PATH co_cli/` = 0; lint clean; `_check_google` `check.py:254-263` no ADC branch/arg |
| TASK-3 (mid-delivery gate) | `requires_config` dropped from 7 Google tools; `_google_available` surfaces them on on-disk credential source | ✓ pass | `grep requires_config co_cli/tools/google/` = only the `_auth.py:52` docstring noting its absence; `_google_available` `_auth.py:64-72` checks `google_credentials_path` file OR `GOOGLE_TOKEN_PATH.exists()` pre-resolution, hides on absent/expired post-resolution |
| TASK-4 | `check --help` exits 0; report helper granted-vs-required diff + shortfall guidance, no secrets, reusing TASK-2 classification | ✓ pass | `google_check` `google.py:132-176`, `_check_report` `:105-129` (✓/✗ per required scope, shortfall → `co google auth`). Verified live: shortfall message to stderr, exit 1. Tests `test_check_report_*` green |

### Issues Found & Fixed
No blocking issues found. No fix edits were required.

One finding raised and reconciled to **false-positive**:
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| TASK-4 `google_check` does not call the shared `handle_google_api_error` classifier (done_when says "reusing TASK-2's classification") | `google.py:167-173` vs `tool_io.py:309-346` | false-positive | The tool-layer helper requires `RunContext[CoDeps]` and returns `ToolReturn`/raises `ModelRetry` — agent-loop types a Typer CLI command cannot produce. Reuse is satisfied at the message/UX level (both emit the same "Re-authorize by running `co google auth`" guidance; `_check_report` docstring states the parity). Extracting a shared pure classifier would couple two divergent surfaces (model-facing tuple vs human-facing report) for one sentence — over-engineering, out of scope. done_when met. |

Scope note (non-blocking): the working tree carries unrelated in-flight changes from other plans (prefill-trim, shell-cwd-anchor, deferred-tool-stub-grouping; `deferred_prompt.py`, several test files, `prompt-assembly.md`). These are not part of this delivery and were not modified by it — flagged for staged-file hygiene at ship time.

### Tests
- Command: `uv run pytest -x -q` (full suite, user-requested)
- Result: 554 passed, 1 failed — the single failure was `test_flow_tool_call_functional.py::test_tool_selection_shell_git_status`, an **LLM-driven tool-selection test unrelated to this change**.
- RCA: span trace showed the model correctly routed to `shell_exec` on turn 1; the Ollama backend (`qwen3.6:35b-a3b-agentic`) then progressively stalled (chat calls 3.98s → 22.25s → 73.8s+ERROR) under sustained serial load, pushing `co.turn` to its 100s ceiling so `asyncio.timeout` truncated the turn and `turn.messages` carried no completed `ToolCallPart`. **External dependency (Ollama latency), not a code defect.** Confirmed by isolated warm re-run: passes in 63s, correctly routes to `shell_exec`. Not bumping the timeout per project policy (don't paper over LLM latency).
- All 10 google-auth tests (`test_flow_google_auth.py`, `test_flow_google_auth_command.py`) passed within the full run.
- Logs: `.pytest-logs/20260602-161405-review-impl-google-auth.log`, `.pytest-logs/*-rerun-git-status-1.log`
- Lint: `scripts/quality-gate.sh lint` → PASS (327 files formatted, all checks pass).

### Behavioral Verification
- `co --help`: ✓ `google` group registered (alongside chat/tail/trace/dream). No `co status`/`co doctor` command exists in this CLI — the skill's generic template assumption; `_check_google`'s change verified at source instead.
- `co google --help`: ✓ group help lists `auth` + `check`.
- `co google auth --help`: ✓ exits 0, lists `--client-secret` + `--credentials-path` (TASK-3 done_when (a)).
- `co google check --help`: ✓ exits 0, lists `--credentials-path`.
- `co google check` (live, real env): ✓ existing `~/.co-cli/google_token.json` failed the scope-validating refresh → actionable message *"…invalid or missing required scopes. Re-authorize by running `co google auth`."* to stderr, **exit 1** (scriptable). No secrets in output. **TASK-4 `success_signal` verified live.**
- `success_signal` status: TASK-2 verified (the live `check` reuses the same re-auth guidance string); TASK-4 verified live. TASK-1 (consent screen read+draft only) is by-construction from the scope set; TASK-3 (browser OAuth → tools work next `co chat`) is the interactive `run_local_server` leg — manual-verify per the plan's Testing section, not automatable here.

### Overall: PASS
All four tasks meet `done_when` with file:line evidence; lint clean; the sole suite failure is a confirmed Ollama-latency flake in an unrelated tool-selection test that passes on warm re-run; behavioral verification confirms the new `co google auth`/`co google check` surface and TASK-4's success_signal. The one finding (TASK-4 classifier non-reuse) is a false-positive — incompatible tool-vs-CLI return contracts, message-level parity satisfies the spec. Two interactive legs (TASK-1 consent, TASK-3 browser flow) remain manual-verify per plan. Ready to ship.
