# TODO: Pytest Audit Gap Coverage

**Task type: code-feature**

## Context

Open from `docs/TODO-pytest-suite-audit.md`. Three coverage gaps confirmed against latest code (read `tests/test_agent.py`, `co_cli/agent.py`, `co_cli/tools/subagent.py`, `co_cli/main.py`):

1. **Spec assertions** — `EXPECTED_TOOLS_CORE` (34 names, line 15) and `EXPECTED_APPROVAL_TOOLS` (8 names, line 72) in `tests/test_agent.py` are hardcoded sets manually synced to `_build_filtered_toolset()`. `test_build_agent_registers_all_tools` asserts exact set equality; `test_approval_tools_flagged` loops over the hardcoded approval set. Both break on any tool rename/add and protect implementation structure, not behavior.

2. **Sub-agent pure logic gaps** — `tests/test_subagent_tools.py` only covers `model_registry=None` guards and result-model validation. Two pure-Python production paths in `co_cli/tools/subagent.py` have no coverage: (a) `run_research_subagent()` web_policy gate — raises `ModelRetry` when `web_policy.search` or `.fetch` is not `"allow"`; (b) `_merge_turn_usage()` — on first call (`turn_usage is None`) aliases the usage object; on subsequent calls accumulates via `incr()`. An alias bug here corrupts multi-subagent turn usage stats.

3. **CLI loop gap** — `co_cli/main.py` has no test coverage. The immediately testable unit is `_cleanup_skill_run_state()` — pure env-var restore: restores set vars, removes absent ones, clears `deps.runtime.active_skill_name`. The remaining paths (`_chat_loop`, `_finalize_turn`, MCP init degradation, completer refresh, slash-command routing) are monolithic terminal-coupled loop code; extracting them into testable units is a separate refactor deferred from this delivery.

No phantom features, no stale module names. No existing `TODO-pytest-audit-gaps.md`.

## Problem & Outcome

**Problem:** Three production paths lack meaningful behavioral tests.

**Failure cost:**
- Spec assertions create friction on every tool refactor — rename one tool, two constants need manual updates, and the test still passes if the behavior broke silently.
- `run_research_subagent()` policy gate untested — a regression where the research agent runs when `web_policy` forbids web access goes undetected.
- `_merge_turn_usage()` alias bug untested — incorrect first-call aliasing corrupts multi-subagent usage accumulation silently (wrong token counts reported to the user).
- `_cleanup_skill_run_state()` untested — skill-injected env vars leaked into subsequent turns cause the model to see stale state.

**Outcome:** Spec assertions replaced with behavioral contracts; web_policy gate and usage merge logic covered; skill env cleanup verified. Full suite remains green.

## Scope

**In:**
- Refactor `tests/test_agent.py`: remove `EXPECTED_TOOLS_CORE` and `EXPECTED_APPROVAL_TOOLS`; replace with behavioral spot-checks
- Add two pure-logic tests to `tests/test_subagent_tools.py`: web_policy gate + `_merge_turn_usage` alias/accumulate
- Add three `_cleanup_skill_run_state` tests to `tests/test_commands.py`

**Out:**
- Sub-agent full LLM execution with a configured role model (deferred — requires ROLE_CODING/RESEARCH/ANALYSIS configured, no skip policy)
- `_finalize_turn` signal gate, `_chat_loop` slash-command routing, MCP init degradation, completer refresh (require chat loop harness or main.py extraction refactor — deferred)
- `run_turn()` HTTP retry and `_check_output_limits` (require real `AgentRunResult` — already deferred in audit)

## Behavioral Constraints

- No mocks, no `monkeypatch`, no `unittest.mock`. Real production code, real deps.
- TASK-1, TASK-2, TASK-3 have zero LLM calls. All pure Python.
- Refactored `test_agent.py` must still catch: duplicate tool registration, wrong approval flag on a side-effectful tool, domain tools present when config path absent.
- `_merge_turn_usage` imported directly from `co_cli.tools.subagent` — acceptable because there is no public interface that triggers it without a real LLM call, and the alias-vs-accumulate behavior is a critical production invariant.

## High-Level Design

**TASK-1 — test_agent.py behavioral refactor:**
`EXPECTED_TOOLS_CORE` and `EXPECTED_APPROVAL_TOOLS` removed. Replacements:
- No-duplicates check: keep as-is
- Core presence: spot-check that canonical tools (`run_shell_command`, `check_capabilities`, `web_search`, `save_memory`) are registered — not exhaustive set equality
- Sub-agent conditional: assert subagent tool is registered iff its role model is configured (test both present and absent cases)
- Approval behavioral spot-checks — confirmed against `agent.py` lines 116–159:
  - `start_background_task`, `save_memory`, `write_file`, `edit_file` → `requires_approval=True`
  - `run_shell_command` → `requires_approval=False` (shell approval is intra-tool via `ApprovalRequired`, not at agent layer — see `agent.py:141–144`)
  - `check_capabilities`, `read_file`, `search_knowledge` → `requires_approval=False`
- Keep all four tests that are already behavioral: `test_web_search_ask_requires_approval`, `test_web_fetch_ask_requires_approval`, `test_build_agent_excludes_domain_tools_when_config_absent`, `test_build_task_agent_excludes_domain_tools_when_config_absent`
- Keep `test_build_task_agent_registers_same_tools_as_main_agent` — it compares two live results, not a hardcoded set

**TASK-2 — sub-agent pure logic:**
1. Web_policy gate: construct `CoDeps` with `model_registry=None` and `web_policy=WebPolicy(search="ask", fetch="allow")`; call `run_research_subagent` via `RunContext`; assert `ModelRetry` raised. The policy gate in `subagent.py:152–156` fires before the registry check at `161–162` — `model_registry=None` is safe and removes hidden env-var dependency. Repeat with `fetch="ask"`.
2. `_merge_turn_usage`: build `RunContext` with `deps.runtime.turn_usage=None`; call once with `u1 = RunUsage(request_tokens=10, response_tokens=20, total_tokens=30)`; assert `turn_usage is u1` (alias); snapshot `snapshot = copy(u1)`; call again with `u2 = RunUsage(request_tokens=5, response_tokens=5, total_tokens=10)`; assert `turn_usage.request_tokens == 15` (accumulated); assert `snapshot.request_tokens == 10` (copy not mutated — verifies `_run_subagent_attempt`'s copy() decoupling holds).

**TASK-3 — `_cleanup_skill_run_state`:**
Three cases. No LLM, no network, no filesystem beyond `os.environ`:
1. Key was set before skill — restore original value
2. Key was absent before skill — remove it (not left set)
3. `deps.runtime.active_skill_name = "my-skill"` → cleared to `None`

## Implementation Plan

### TASK-1 — Refactor spec assertions in test_agent.py

**files:** `tests/test_agent.py`

**done_when:**
```
uv run pytest tests/test_agent.py
```
All tests pass. `grep -n 'EXPECTED_TOOLS_CORE\|EXPECTED_APPROVAL_TOOLS' tests/test_agent.py` returns no matches.

**success_signal:** N/A — refactor, no user-visible change.

---

### TASK-2 — Sub-agent pure logic: web_policy gate + usage merge

**files:** `tests/test_subagent_tools.py`

**done_when:**
```
uv run pytest tests/test_subagent_tools.py -k "web_policy or merge_turn_usage"
```
Both new tests pass: `test_research_web_policy_gate_raises_model_retry` and `test_merge_turn_usage_alias_then_accumulate`.

**success_signal:** N/A — internal logic coverage.

---

### TASK-3 — Extract and test _cleanup_skill_run_state

**files:** `co_cli/context/_skill_env.py` (new), `co_cli/main.py`, `tests/test_commands.py`

**Step 1 — Extract:** Move `_cleanup_skill_run_state` from `co_cli/main.py` into a new `co_cli/context/_skill_env.py`. Update `main.py` to import from there. `co_cli/main.py` runs side-effectful module-level code on import (OTel provider, `Agent.instrument_all()`); importing it in-process during tests overwrites the harness `TracerProvider`.

**Step 2 — Test:** Add three tests to `tests/test_commands.py` importing `_cleanup_skill_run_state` from `co_cli.context._skill_env`. New tests construct `CoDeps()` directly — do NOT call `_make_ctx()`.

**done_when:**
```
uv run pytest tests/test_commands.py -k cleanup_skill
grep -n '_cleanup_skill_run_state' tests/test_commands.py
grep -n 'from co_cli.context._skill_env' tests/test_commands.py
```
Three new tests pass. Greps confirm the extraction module is the import source.

**success_signal:** N/A — internal logic coverage.

---

## Testing

Each task has its own targeted run in `done_when`. After all three tasks, run:

```
uv run pytest tests/test_agent.py tests/test_subagent_tools.py tests/test_commands.py
uv run pytest  # full suite
```

## Open Questions

None — all answered by reading source.


## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev pytest-audit-gaps`
