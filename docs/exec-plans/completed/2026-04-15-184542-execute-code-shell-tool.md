# Plan: execute_code Shell Tool

Task type: code-feature

---

## Context

`analyze_code` is a delegation tool in `co_cli/tools/agents.py` — it builds a pydantic-ai sub-agent with file tools (glob, read_file, grep), runs it via `_delegate_agent`, and returns a summarized result. This is heavy machinery for what is essentially "run some code and see the output." The main agent has direct access to the same file tools and can investigate code in its own context; the isolated sub-agent context adds overhead without a compensating benefit.

Peers (`hermes-agent`, `opencode`) treat code execution as a shell operation: the main agent writes code with file tools, then runs it with a shell-style command (`python main.py`, `node index.js`, `uv run pytest`). No sub-agent is spawned. The interpreter and the file to run are the agent's decision — not a sub-agent's.

`run_shell_command` already covers this use case but is a general-purpose shell tool (git, builds, system queries). `execute_code` gives the agent a named, intention-revealing entry point specifically for running code, with approval always required — stricter than `run_shell_command`'s tiered model where safe prefixes bypass approval.

**Workflow artifact hygiene:** No stale TODO artifacts. No existing `execute-code-shell-tool` plan.

**Regression surface check:** Removing `analyze_code` also removes `_coder_instructions` (dead after removal) and `max_requests_coder` in `SubagentSettings` (no longer consumed). The three remaining delegation tools (`research_web`, `analyze_knowledge`, `reason_about`) and `_delegate_agent` are unaffected.

---

## Problem & Outcome

**Problem:** `analyze_code` spawns a full sub-agent to do what the main agent can do directly: write code with file tools, then run it via shell. The delegation pattern adds latency, depth-guard complexity, and a separate request budget for a task that does not require an isolated context window.

**Failure cost:** The main agent cannot run code without spawning a sub-agent, burning depth budget, and requiring a model to be configured. A user asking "run this script" pays sub-agent overhead when a direct shell call would suffice.

**Outcome:** `execute_code(cmd)` is a thin shell tool — the agent constructs `python main.py` or `node index.js`, the user approves, the shell runs it, stdout+stderr return. No sub-agent, no depth guard, no request budget. Identical to how `run_shell_command` works but scoped to interpreter commands with always-required approval.

---

## Scope

**In scope:**
- Create `co_cli/tools/execute_code.py` with `execute_code(ctx, cmd, timeout)` function
- Register `execute_code` as DEFERRED, no `approval=True` flag (inline guard pattern, mirrors `run_shell_command`), not concurrent-safe
- Remove `analyze_code` function and `_coder_instructions` from `agents.py`
- Remove `max_requests_coder` from `SubagentSettings` and its env-var mapping
- Update all integration points: `_native_toolset.py`, `tool_display.py`, `_deferred_tool_prompt.py`, `_commands.py`
- Update tests

**Out of scope:**
- Modifying `run_shell_command`, `_shell_policy`, or `ShellBackend`
- Changing `research_web`, `analyze_knowledge`, `reason_about` or `_delegate_agent`
- Sandboxing, process isolation, or output truncation beyond what `ShellBackend` already provides
- Adding a new config field for execute_code timeout — use existing `shell.max_timeout` cap

---

## Behavioral Constraints

- DENY patterns from `_shell_policy.evaluate_shell_command` apply — fork bombs, curl-pipe-to-shell, absolute-path destruction, heredoc injection are blocked
- No ALLOW tier: unlike `run_shell_command`, `execute_code` always requires approval even for safe-prefix commands — code execution always has side effects
- Registration: no `approval=True` flag — inline `ApprovalRequired` guard pattern (mirrors `run_shell_command`). DENY check runs first (before approval prompt); all non-DENY commands require approval via inline guard.
- `execute_code` is NOT in `_DELEGATION_TOOLS` in `_commands.py` (it is a shell tool, not a sub-agent spawner)
- `execute_code` is DEFERRED (discovered via search_tools), not ALWAYS visible — keeps primary tool surface clean; always-require-approval is justified because code execution spawns processes with filesystem side effects, comparable to `write_file`
- `is_concurrent_safe=False` — running code has filesystem side effects
- Default timeout: 60s (shorter than run_shell_command's 120s; code runs are bounded)
- Category in deferred prompt: "code execution" with rep `execute_code`; registration goes in `# Execution` block in `_native_toolset.py`, not `# Delegation tools`
- `_commands.py` `_DELEGATION_TOOLS` loses `analyze_code`, gains nothing (execute_code is not a delegation tool)

---

## High-Level Design

### `execute_code` function

```python
async def execute_code(ctx: RunContext[CoDeps], cmd: str, timeout: int = 60) -> ToolReturn:
    """Run a code interpreter command and return combined stdout + stderr.

    Use to run a code file or one-liner via an interpreter. The agent
    constructs the command; the user approves before execution.

    Examples: "python main.py", "node index.js", "uv run pytest tests/",
              "npx ts-node app.ts", "ruby script.rb"

    Do not use for git, builds, or system queries — use run_shell_command instead.

    Args:
        cmd: Interpreter command to run (e.g. "python main.py").
        timeout: Max seconds (default 60). Capped by shell_max_timeout.
    """
    policy = evaluate_shell_command(cmd, ctx.deps.config.shell.safe_commands)
    if policy.decision == ShellDecisionEnum.DENY:
        return tool_error(policy.reason, ctx=ctx)
    # Always require approval — code execution always has side effects.
    if not ctx.tool_call_approved:
        raise ApprovalRequired(metadata={"cmd": cmd})
    effective = min(timeout, ctx.deps.config.shell.max_timeout)
    try:
        output = await ctx.deps.shell.run_command(cmd, timeout=effective)
        return tool_output(output, ctx=ctx)
    except RuntimeError as e:
        msg = str(e)
        if "timed out" in msg.lower():
            raise ModelRetry(
                f"execute_code: timed out after {effective}s. "
                f"Use a shorter command or increase timeout.\n{msg}"
            ) from e
        raise ModelRetry(
            f"execute_code: command failed ({e}). Check the command and try again."
        ) from e
    except Exception as e:
        raise ModelRetry(f"execute_code: unexpected error ({e}).") from e
```

### Registration in `_native_toolset.py`

```python
# Pattern B: inline guard — no approval=True at registration
_register_tool(execute_code, is_concurrent_safe=False, visibility=_deferred_visible)
```

Placement: `# Execution` block alongside `run_shell_command`, not the `# Delegation tools` block.

### Category in `_deferred_tool_prompt.py`

```python
_NATIVE_CATEGORIES: dict[str, str] = {
    ...
    "execute_code": "code execution",
}
_NATIVE_CATEGORY_REPS: dict[str, list[str]] = {
    ...
    "code execution": ["execute_code"],
}
```

### `tool_display.py` display arg

```python
"execute_code": "cmd",
```

### `_commands.py` — remove `analyze_code` from `_DELEGATION_TOOLS`

`execute_code` is NOT added — it's a shell tool, not a delegation tool.

---

## Implementation Plan

### ✓ DONE — TASK-1 — Create `execute_code` tool and its test

```
files:
  - co_cli/tools/execute_code.py
  - tests/test_execute_code.py
done_when: >
  uv run pytest tests/test_execute_code.py -x passes;
  tests cover: (1) DENY pattern blocked and returns tool_error,
  (2) ApprovalRequired raised when not approved,
  (3) approved path calls shell.run_command and returns tool_output.
success_signal: >
  Agent running "python main.py" in the REPL triggers an approval prompt
  and on confirmation returns the script output.
```

### ✓ DONE — TASK-2 — Remove `analyze_code`; wire `execute_code` into all integration points

```
files:
  - co_cli/tools/agents.py
  - co_cli/config/_subagent.py
  - co_cli/agent/_native_toolset.py
  - co_cli/context/tool_display.py
  - co_cli/context/_deferred_tool_prompt.py
  - co_cli/commands/_commands.py
done_when: >
  grep -r "analyze_code\|max_requests_coder\|_coder_instructions" co_cli/ tests/ returns empty;
  registration placed in # Execution block in _native_toolset.py (not # Delegation tools);
  uv run python -c "
  from co_cli.agent._native_toolset import _build_native_toolset
  from co_cli.config._core import settings
  toolset, native_index = _build_native_toolset(settings)
  names = {info.name for info in native_index.values()}
  assert 'execute_code' in names, 'execute_code must be registered'
  assert 'analyze_code' not in names, 'analyze_code must be removed'
  print('OK')
  " exits 0 and prints OK.
success_signal: N/A (wiring change)
prerequisites: [TASK-1]
```

### ✓ DONE — TASK-3 — Test updates and regression verification

```
files:
  - tests/test_agents.py
  - tests/test_tool_prompt_discovery.py
  - tests/test_tool_registry.py
done_when: >
  uv run pytest -x passes (full suite);
  grep -r "analyze_code" tests/ returns empty;
  uv run python -c "
  from co_cli.agent._native_toolset import _build_native_toolset
  from co_cli.config._core import settings
  from co_cli.context._deferred_tool_prompt import build_category_awareness_prompt
  toolset, native_index = _build_native_toolset(settings)
  prompt = build_category_awareness_prompt(native_index)
  assert 'analyze_code' not in prompt, 'analyze_code must not appear in category prompt'
  assert 'sub-agents' in prompt, 'sub-agents category must still appear for remaining delegation tools'
  assert 'execute_code' in prompt, 'execute_code must appear in category prompt'
  print('OK')
  " exits 0 and prints OK.
success_signal: N/A (refactor)
prerequisites: [TASK-2]
```

---

## Testing

- TASK-1 adds `tests/test_execute_code.py` with three tests: DENY block, ApprovalRequired, approved execution.
- No mocks — tests use real `CoDeps` with real `ShellBackend` and real settings. `ApprovalRequired` is a pydantic-ai exception that can be caught in tests. The approved path runs a real command (e.g. `echo hello` via `execute_code`) with `ctx.tool_call_approved = True`.
- Existing `test_agents.py::test_analyze_code_no_model` is removed (the tool is gone). Remaining tests in `test_agents.py` (`test_fork_deps_resets_session_state`, `test_merge_turn_usage_alias_then_accumulate`) are unaffected.

---

## Open Questions

None — design derivable from existing shell.py and _shell_policy.py patterns.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev execute-code-shell-tool`

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `docs/specs/tools.md` | `analyze_code` still listed in tool catalog table and domain tools list | blocking | TASK-2 |

**Overall: 1 blocking (fixed inline before summary)**

Blocking finding fixed: `docs/specs/tools.md` updated to replace `analyze_code` with `execute_code` in both the domain tools list and the catalog table. `context.md` stale `max_requests_coder` row also removed. Sequential column and within-turn serialization prose updated for `execute_code`.

---

## Delivery Summary — 2026-04-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | pytest tests/test_execute_code.py -x passes; DENY, ApprovalRequired, approved path covered | ✓ pass |
| TASK-2 | grep returns empty for analyze_code/max_requests_coder/_coder_instructions in co_cli/; execute_code registered, analyze_code not | ✓ pass |
| TASK-3 | uv run pytest -x passes (full suite); grep tests/ for analyze_code negative assertions only | ✓ pass |

**Tests:** full suite — 489 passed, 0 failed
**Independent Review:** 1 blocking (stale doc reference — fixed inline)
**Doc Sync:** fixed (tools.md: execute_code sequential=yes, inline-approval footnote, domain tools list; context.md: removed max_requests_coder row)

**Overall: DELIVERED**
Replaced `analyze_code` sub-agent delegation tool with `execute_code` thin shell tool; all 489 tests pass; specs updated.

---

## Implementation Review — 2026-04-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | pytest tests/test_execute_code.py -x passes | ✓ pass | `execute_code.py:26` DENY check via `evaluate_shell_command`; `:30-31` inline `ApprovalRequired` guard (no ALLOW bypass); `:34` `shell.run_command` call; tests cover all 3 required paths |
| TASK-2 | grep empty for analyze_code/max_requests_coder/_coder_instructions in co_cli/; execute_code registered | ✓ pass | `agents.py` — `analyze_code` + `_coder_instructions` deleted; `_subagent.py:16-19` — 3 fields, coder removed; `_native_toolset.py:207` — `execute_code` registered DEFERRED `is_concurrent_safe=False`; `_commands.py:282-289` — frozenset lacks `analyze_code`; `tool_display.py:30` — `"execute_code": "cmd"`; `_deferred_tool_prompt.py:21` — `"execute_code": "code execution"` |
| TASK-3 | full suite passes; analyze_code absent from tests/ | ✓ pass | `test_agents.py` — `analyze_code` import + test removed; `test_tool_prompt_discovery.py:54-68` — new `test_execute_code_tool_discoverable_by_keywords`, negative assertion on `analyze_code`; `test_tool_registry.py:79,87` — `code execution` category asserted, `analyze_code` negated, sequential count updated to 3 |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale import `from typing import Any` | `tests/test_bootstrap.py:5` | blocking | Removed — test refactored away from `monkeypatch`, `Any` unused |
| Stale import `import pytest` | `tests/test_bootstrap.py:7` | blocking | Removed — same test refactor, `pytest` unreferenced |

### Tests
- Command: `uv run pytest -v`
- Result: 489 passed, 0 failed (third run)
- Log: `.pytest-logs/*-review-impl-3.log`
- Two transient failures in runs 1–2: `test_compact_produces_two_message_history` (LLM timeout under full-suite Ollama load — passes in isolation and on warm run) and `test_sync_knowledge_store_failure_returns_none` (ordering race — passes in isolation and on third run). Neither related to this delivery.

### Doc Sync
- Scope: full — `analyze_code` is a public API rename affecting `tools.md`, `context.md`
- Result: fixed — `tools.md` sequential column (yes for execute_code), approval footnote, within-turn serialization prose; `context.md` stale `max_requests_coder` row removed

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM online, Shell active (approval-gated), all integrations nominal
- `execute_code` approval path: ✓ confirmed at `execute_code.py:30-31` — inline `ApprovalRequired` guard fires for every non-DENY command regardless of policy tier; structurally identical to `run_shell_command`'s REQUIRE_APPROVAL path which is production-proven; direct test coverage via `test_execute_code_requires_approval_when_not_approved`

### Overall: PASS
`execute_code` thin shell tool replaces `analyze_code` sub-agent delegation; all 489 tests pass; two stale imports in `test_bootstrap.py` fixed; specs fully updated. Ship-ready.
