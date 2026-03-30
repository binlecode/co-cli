# Pytest Suite Audit — Open Items

Date: 2026-03-30

## Open Findings

### 1. `test_agent.py` — tool inventory as spec assertion

`EXPECTED_TOOLS_CORE` (line 15) hard-codes a set of 34 tool names and asserts exact equality against `build_agent()` output. This makes `test_build_agent_registers_all_tools` a spec mirror: every tool rename or addition requires updating a constant rather than testing a behavioral contract.

`EXPECTED_APPROVAL_TOOLS` (line 72) has the same problem for approval wiring.

What these tests should verify instead:
- Side-effectful tools (filesystem writes, shell, memory writes, service calls) all require approval
- Read-only tools do not require approval
- No duplicates are registered
- Domain tools are absent when their config path is not set (this part is already correct — `test_build_agent_excludes_domain_tools_when_config_absent`)

The exact count and name-set assertions should be removed or narrowed to the behavioral contract above.

Production path: `co_cli/agent.py` — `build_agent()`, tool registration loop, approval flag wiring.

### 2. Sub-agent tests — only unavailable-guard and result-model validation

`tests/test_subagent_tools.py` covers:
- `run_coder_subagent` / `run_research_subagent` / `run_analysis_subagent` / `run_thinking_subagent` with `model_registry=None` → `ModelRetry("unavailable")` (lines 31–149)
- `make_subagent_deps` isolation invariants (line 40)
- `ResearchResult` / `CoderResult` confidence out-of-range validation (line 123)
- `ThinkingResult` field values (line 131)

Missing coverage:
- Actual delegated execution with a configured model — structured result returned, usage merged into parent turn
- Research sub-agent empty-result retry path (`summary == ""` or low confidence → ModelRetry)
- Budget accounting: `request_budget` field decremented and propagated through `make_subagent_deps`

Production paths in `co_cli/tools/_subagent_agents.py` and `co_cli/tools/subagent.py` not exercised by any test.

### 3. CLI loop (`co_cli/main.py`) — no meaningful coverage

No test file covers `main.py` behaviors beyond startup failure. Uncovered paths:

- MCP init degradation: `_init_mcp_servers` partial failure → session continues with degraded capability set
- Completer refresh after session capability init: `_chat_loop` refresh after `initialize_session_capabilities`
- Slash-command integration inside `_chat_loop`: slash commands routed and executed within the loop
- Skill env cleanup: `skill_env_cleanup` called on turn exit
- Foreground turn finalization: `_finalize_foreground_turn` path after `run_turn` returns

These are integration-level behaviors — testing them requires either a real chat loop invocation or extracting them into separately testable units.
