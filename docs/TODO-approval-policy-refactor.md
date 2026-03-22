# TODO: Approval Policy Refactor

This delivery refactors co's approval system from a mixed model:

- shell approvals remembered by derived command pattern
- non-shell approvals remembered by bare tool name for the rest of the session

to a converged policy model with explicit approval subjects, scoped remembered
rules, and a central approval broker that stays separate from tool execution.

The target design is intentionally conservative:
- keep the existing approval interception loop in `run_turn()`
- keep shell's inline `DENY / ALLOW / REQUIRE_APPROVAL` classifier
- keep `ApprovalRequired` as the SDK handoff point
- remove broad session-wide trust by tool name for mutating non-shell tools

The refactor is implementation-driven, not speculative. It should improve:
- security posture
- approval legibility
- prompt-fatigue reduction
- consistency between native tools, shell, web, and MCP-backed tools

Ordered high -> low impact.

---

## Current Gaps

The current codebase has the right high-level shape but an inconsistent approval
model:

- `run_shell_command` uses a stronger pattern:
  - classify command inline
  - only defer `REQUIRE_APPROVAL`
  - remember `"a"` as a derived command pattern on disk
- non-shell deferred tools use a weaker pattern:
  - `"a"` stores only `tool_name` in `deps.session.session_tool_approvals`
  - later calls to the same tool skip prompting regardless of target or effect
- `/approvals` only manages persistent shell approvals, so the operator cannot
  inspect or reason about non-shell remembered approvals at all
- the session approval state does not encode effect class or target scope
- the current design makes it too easy for `"a"` on one call to become a broad
  trust grant for the entire tool for the rest of the session

The main design goal is not "more approvals." The goal is narrower remembered
approvals with clearer policy boundaries.

---

## Target Contract

After this refactor, approval decisions should be resolved in this order:

1. Execution boundary / sandbox decides what is technically possible.
2. Tool-local policy may immediately `DENY` or `ALLOW` special cases.
3. Deferred approval resolves against a normalized approval subject.
4. If a remembered rule matches that subject, auto-approve.
5. Otherwise prompt the user.
6. `"a"` stores a narrow rule based on subject kind, not a blanket tool grant.

Target subject examples:

- shell command:
  - `kind="shell_command_pattern"`
  - subject key derived from `git commit *`
- file write:
  - `kind="path_pattern"`
  - target derived from `docs/**` or the concrete file path
- web fetch:
  - `kind="domain"`
  - target derived from `docs.python.org`
- MCP tool:
  - `kind="mcp_tool"`
  - subject includes server name + tool name

Non-goals for this delivery:
- no broad redesign of pydantic-ai approval mechanics
- no cross-session persistence for every tool class
- no policy DSL exposed to the model
- no DESIGN doc edits as TODO inputs; doc sync happens after delivery

---

## T1 - Replace Bare Session Tool Approvals With Scoped Session Rules

**Files:** `co_cli/deps.py`, `co_cli/tools/_tool_approvals.py`

**Problem:** `CoSessionState.session_tool_approvals: set[str]` is too coarse.
For non-shell tools, `"a"` currently means "approve this whole tool for the rest
of the session," which is broader than the approval subject that the user
actually saw.

**Fix:** Replace `session_tool_approvals` with a typed session approval rule
store.

Recommended shape:

```python
@dataclass(frozen=True)
class SessionApprovalRule:
    kind: Literal[
        "tool",
        "path_pattern",
        "domain",
        "mcp_tool",
        "shell_command_pattern",
    ]
    tool_name: str
    value: str
```

Session state becomes:

```python
session_approval_rules: list[SessionApprovalRule] = field(default_factory=list)
```

Implementation rules:
- do not preserve `set[str]` as the primary runtime path
- matching logic must live in approval helpers, not in `run_turn()`
- keep shell persistent approvals on disk unchanged for this task
- session rule matching must be exact or pattern-based depending on rule kind

Migration approach:
- update all callsites from `session_tool_approvals` to the new helper API
- update tests that currently assume raw set membership
- no backward-compat deserialization is required because this is session memory
  only, not persisted user data

**done_when:**
- `CoSessionState` no longer stores bare non-shell approvals as `set[str]`
- approval matching goes through rule helpers
- no code path relies on `tool_name in deps.session.session_tool_approvals`

---

## T2 - Normalize Deferred Calls Into Explicit Approval Subjects

**Files:** `co_cli/tools/_tool_approvals.py`

**Problem:** The file already distinguishes shell command approvals from direct
tool approvals, but all non-shell tools still collapse into `ToolApprovalSubject(tool_name)`.
That loses the actual security-relevant target.

**Fix:** Expand approval-subject resolution so mutating tools resolve to narrow,
meaningful subjects.

Recommended subject model:

```python
@dataclass(frozen=True)
class ApprovalSubject:
    kind: str
    tool_name: str
    value: str
    display_value: str
```

Subject resolution rules:
- `run_shell_command(cmd=...)`
  - `kind="shell_command_pattern"`
  - `value=derive_pattern(cmd)`
- `write_file(path=...)` / `edit_file(path=...)`
  - `kind="path_pattern"`
  - normalize path relative to workspace root
  - initial implementation may use exact path
- `web_fetch(url=...)` / `web_search(query=...)`
  - `web_fetch` uses `kind="domain"` from parsed URL host
  - `web_search` should not support remembered `"a"` in this delivery unless a
    narrow subject can be justified cleanly
- MCP tools
  - resolve `kind="mcp_tool"`
  - subject key must include server prefix and concrete tool name
- all other tools
  - default to `kind="tool"` only if they are low-risk enough to justify that
    scope

Important rule:
- do not silently fall back to broad `kind="tool"` for obviously mutating tools
  just because subject extraction is inconvenient

**done_when:**
- approval-subject resolution is target-aware for shell, file-write, web-fetch,
  and MCP tool calls
- non-shell mutating tools no longer default to bare tool-name subjects unless
  explicitly intended
- user-facing approval descriptions show the resolved subject clearly

---

## T3 - Separate Subject Matching From Subject Persistence

**Files:** `co_cli/tools/_tool_approvals.py`, `co_cli/tools/_exec_approvals.py`

**Problem:** The current helpers mix three concerns:
- resolve what is being approved
- decide whether it is already approved
- persist approval decisions

This is manageable today but will become brittle once multiple subject kinds
exist.

**Fix:** Split approval handling into three explicit helper layers:

1. `resolve_approval_subject(tool_name, args, deps) -> ApprovalSubject`
2. `is_subject_auto_approved(subject, deps) -> bool`
3. `remember_subject_approval(subject, deps) -> None`

Persistence rules:
- `shell_command_pattern`
  - persist cross-session using `.co-cli/exec-approvals.json`
- other narrow non-shell subjects
  - persist for session only in `deps.session.session_approval_rules`
- broad `kind="tool"` subjects
  - allowed only for explicitly low-risk tools

Matching rules:
- shell remains fnmatch-based via the existing persistent store
- path rules may start as exact path matches in this delivery
- domain rules match exact host initially
- MCP rules match exact server/tool pair

Do not introduce a second JSON persistence store in this task unless the code
becomes materially cleaner by doing so. Session-only rules are sufficient for
the first pass.

**done_when:**
- helper responsibilities are clearly split
- shell persistence remains isolated to `_exec_approvals.py`
- non-shell remembered approvals are stored as typed session rules

---

## T4 - Gate Which Tools May Offer `"a"`

**Files:** `co_cli/tools/_tool_approvals.py`, `co_cli/context/_orchestrate.py`

**Problem:** The current prompt semantics imply that every deferred tool may
offer `y / n / a`, but not every tool should support remembered approval.
Converged peer systems generally disable or narrow "always allow" for risky
classes instead of exposing it uniformly.

**Fix:** Add explicit capability checks for whether a subject supports `"a"`.

Recommended helper:

```python
def approval_can_be_remembered(subject: ApprovalSubject) -> bool:
    ...
```

Initial policy:
- allow `"a"` for:
  - shell command patterns
  - exact file-write/edit paths
  - exact MCP server/tool pairs only if the tool is read-only or medium-risk
  - exact web-fetch domains if fetch approval is configured and the host is
    parsed successfully
- disallow `"a"` for:
  - tools with broad external side effects
  - subjects that failed normalization
  - any approval falling back to broad `kind="tool"` for mutating tools

Prompt behavior:
- if `"a"` is not allowed for the subject, the UI text should not imply it will
  be remembered
- if the frontend cannot vary available choices cleanly, keep the prompt input
  format but treat `"a"` as `"y"` for non-rememberable subjects and surface that
  in the description text

**done_when:**
- remembered approval is subject-gated, not universal
- the prompt description accurately states when `"a"` will be remembered
- no risky fallback path silently stores broad remembered approvals

---

## T5 - Make The Approval Collector Subject-Driven, Not Tool-Driven

**Files:** `co_cli/context/_orchestrate.py`, `co_cli/tools/_tool_approvals.py`

**Problem:** `_collect_deferred_tool_approvals()` is architecturally correct,
but it still thinks in terms of tool names more than approval subjects.

**Fix:** Refactor `_collect_deferred_tool_approvals()` so it operates only on
resolved approval subjects.

Required flow per deferred call:

1. Decode raw args.
2. Resolve approval subject.
3. Check whether the subject is already auto-approved.
4. Build a prompt description from the subject.
5. Prompt if needed.
6. Record approval or denial.
7. Remember the subject only if `"a"` is both chosen and allowed.

This task should not change the external orchestration contract:
- still return `DeferredToolResults`
- still not resume the stream directly
- still not create a new user/model turn

**done_when:**
- `_collect_deferred_tool_approvals()` no longer contains tool-specific
  branching beyond subject resolution
- approval handling is described by subject semantics, not bare tool names
- existing shell approval behavior remains intact

---

## T6 - Add Trust-Aware Restrictions For Untrusted Workspaces And Servers

**Files:** `co_cli/deps.py`, `co_cli/agent.py`, `co_cli/tools/_tool_approvals.py`, `co_cli/commands/_commands.py`

**Problem:** Remembered approvals are currently independent of workspace trust,
and the code does not cleanly distinguish:
- trusted local project
- untrusted project config surface
- trusted MCP server

Peer systems converge on separate trust gates above tool approval.

**Fix:** Add the minimal state needed to prevent broad remembered approvals in
untrusted contexts.

Initial scope:
- add session-level booleans or lightweight sets for:
  - `workspace_trusted`
  - `trusted_mcp_servers`
- when workspace is not trusted:
  - disable remembered approval for project-mutating tools
  - require prompts even if a session rule would otherwise match
- when MCP server is not trusted:
  - do not remember `"a"` for that server's tools

This task is about policy enforcement hooks, not a full trust UX.
If there is no trust UI yet, default to the current effective trust posture and
leave the state injectable for future wiring.

**done_when:**
- approval helpers can refuse remembered approval based on trust state
- untrusted contexts do not gain broad remembered approval power
- trust state is represented explicitly enough for future `/permissions` work

---

## T7 - Extend `/approvals` Into A General Approval Inspection Surface

**Files:** `co_cli/commands/_commands.py`, `co_cli/tools/_tool_approvals.py`

**Problem:** `/approvals` currently exposes only persistent shell patterns,
which hides most approval state from the operator.

**Fix:** Extend `/approvals` so it can inspect both:
- persistent shell approvals
- current-session approval rules

Minimum command surface:
- `/approvals list`
  - show persistent shell approvals
  - show current session approval rules separately
- `/approvals clear`
  - keep current behavior for persistent shell approvals
- optional stretch:
  - `/approvals clear session`
  - `/approvals clear session <index-or-key>`

Output requirements:
- clearly label scope: `persistent` vs `session`
- clearly label subject kind: `shell pattern`, `path`, `domain`, `mcp tool`
- do not dump raw internal dataclass reprs

**done_when:**
- operators can inspect all remembered approval state that materially affects the
  current session
- `/approvals list` is no longer shell-only
- output is concise and human-readable

---

## T8 - Tighten Tool Registration And Approval Metadata Boundaries

**Files:** `co_cli/agent.py`

**Problem:** Tool registration currently records only a boolean
`requires_approval`, which is useful but too weak for the refactored policy
model.

**Fix:** Add a small metadata layer for approval classification without changing
the pydantic-ai registration API.

Recommended approach:
- keep `agent.tool(..., requires_approval=...)` exactly as-is
- separately track metadata for co-owned policy decisions, for example:

```python
tool_approval_metadata = {
    "write_file": {"effect": "workspace_write", "remember_scope": "path"},
    "edit_file": {"effect": "workspace_write", "remember_scope": "path"},
    "run_shell_command": {"effect": "exec", "remember_scope": "shell_pattern"},
    "web_fetch": {"effect": "network_read", "remember_scope": "domain"},
}
```

This metadata should be consumed by approval-subject resolution and gating
helpers, not by the model prompt.

Do not over-engineer a full policy DSL in this task. The goal is a single
authoritative source for tool approval semantics.

**done_when:**
- co-owned tools have centralized approval metadata
- approval-subject logic does not hardcode the same policy repeatedly in
  multiple modules
- existing tool registration behavior remains compatible with current agent use

---

## T9 - Replace Policy-Violating Tests And Add Functional Regression Coverage

**Files:** `tests/test_orchestrate.py`, `tests/test_tool_approvals.py`, `tests/test_commands.py`, `tests/test_shell.py`, `tests/test_agent.py`, `tests/test_delegate_coder.py`

**Problem:** The current tests cover parts of the approval behavior, but they
still encode the old contract in a few places:
- non-shell `"a"` writes raw tool names into session state
- helper tests focus on the old subject simplification
- some tests are closer to narrow helper tests than high-value behavioral checks

**Fix:** Replace those expectations with end-to-end approval behavior checks.

Required regression cases:
- shell `"a"` persists a derived command pattern and later matching commands run
  without a deferred prompt
- `write_file(path="docs/a.md")` with `"a"` remembers only the normalized path
  subject, not all future writes
- `edit_file(path="docs/a.md")` does not auto-approve `edit_file(path="secrets.txt")`
- a subject that is not rememberable treats `"a"` as non-persistent approval
- untrusted workspace state disables remembered approval reuse
- `/approvals list` shows both persistent and session rules
- sub-agent deps still receive a fresh session approval state

Testing policy:
- prefer functional orchestration tests over narrow helper-only tests
- keep any helper tests only when they guard subtle normalization logic
- remove tests that assert on old raw-set implementation details

**done_when:**
- regression coverage reflects the new subject-scoped approval contract
- stale assertions against `session_tool_approvals` are removed
- approval behavior is verified through real orchestration paths where practical

---

## T10 - Regression Run And Acceptance Gate

**Files:** `tests/test_orchestrate.py`, `tests/test_commands.py`, `tests/test_shell.py`, `tests/test_agent.py`, `tests/test_tool_calling_functional.py`

**Problem:** This refactor changes a central safety path. It should not ship on
partial confidence.

**Fix:** Run focused approval regressions first, then the broader tool-calling
gate.

Required commands:

```bash
mkdir -p .pytest-logs
uv run pytest tests/test_orchestrate.py 2>&1 | tee .pytest-logs/YYYYMMDD-HHMMSS-approval-orchestrate.log
uv run pytest tests/test_commands.py 2>&1 | tee .pytest-logs/YYYYMMDD-HHMMSS-approval-commands.log
uv run pytest tests/test_shell.py 2>&1 | tee .pytest-logs/YYYYMMDD-HHMMSS-approval-shell.log
uv run pytest tests/test_agent.py 2>&1 | tee .pytest-logs/YYYYMMDD-HHMMSS-approval-agent.log
uv run pytest tests/test_tool_calling_functional.py 2>&1 | tee .pytest-logs/YYYYMMDD-HHMMSS-tool-calling.log
```

If the focused runs pass, run the broader suite only if changes touched shared
tool wiring beyond the approval layer.

**done_when:**
- all required focused approval tests pass
- the tool-calling functional gate passes
- no test expectations rely on the removed broad tool-name session approval

---

## Delivery Notes

Implementation order should be:

1. T1-T3 data model and helper split
2. T4-T5 collector and prompt behavior
3. T8 metadata cleanup
4. T6 trust hooks
5. T7 command surface
6. T9-T10 regression and cleanup

After code delivery, sync the relevant DESIGN docs as an output of the change,
not as a planned implementation task.
