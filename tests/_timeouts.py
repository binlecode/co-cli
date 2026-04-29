"""Shared asyncio.timeout constants for test files.

Single source of truth for all per-await timeouts. Import from here instead
of hardcoding numbers inline so a single edit relaxes or tightens a budget
across every test that uses it.

## Two-layer timeout model

Every IO-bound await is wrapped with asyncio.timeout(N) using a constant from
this file. That is the primary guard — it fires from inside the test and
identifies exactly which call ran over.

pytest-timeout (pyproject.toml: timeout = 120) is the safety net. It catches:
- sync code that hangs (fixture setup, subprocess, file IO with no await)
- an await that was accidentally left unwrapped

Why 120s: the largest single per-await budget is LLM_REASONING_TIMEOUT_SECS
(60s). 120s = 2x that, leaving room for fixture setup/teardown around one
reasoning call. A test that legitimately needs more than 120s total must wrap
each sequential LLM call with its own asyncio.timeout and use
@pytest.mark.timeout(N) to raise the outer safety net to N = sum(per-call
budgets) + overhead. Raise individual constants if model latency increases;
raise the pytest ceiling only when correctly-wrapped sequential calls sum past it.

Usage::

    from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS, LLM_TOOL_CONTEXT_TIMEOUT_SECS

    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await summarize_messages(...)
"""

# LLM inference
LLM_NON_REASONING_TIMEOUT_SECS: int = 10
"""Non-thinking model calls (noreason settings: think=false, reasoning_effort=none).

>10s means the model is reasoning when it should not be — a bug in model
config or api_params (e.g. think=false not honored by Ollama for this model).
Use only for low-output bare-context calls (signal detectors, ~10–30 tokens) —
no registered tools in the agent. For compaction summaries (400–900 tokens
output) use LLM_COMPACTION_SUMMARY_TIMEOUT_SECS instead.
"""

LLM_SESSION_SUMMARY_TIMEOUT_SECS: int = 30
"""Session summarization calls (noreason, 5-point structured summary, ~200–400 tokens output).

At ~30 tok/s on local 35B hardware, a 400-token summary takes ~13s.
30s gives ~2–4x headroom. Distinct from LLM_NON_REASONING_TIMEOUT_SECS (10–30 tokens)
and LLM_COMPACTION_SUMMARY_TIMEOUT_SECS (400–900 tokens).
"""

LLM_COMPACTION_SUMMARY_TIMEOUT_SECS: int = 60
"""Compaction LLM summarizer calls (reasoning disabled, no tool schemas).

Output is long-form structured text (400–900 tokens). At ~30 tok/s on local
35B hardware, worst observed output (883 tokens) takes ~29s. 60s gives 2×
headroom without masking a stalled call.
Distinct from LLM_NON_REASONING_TIMEOUT_SECS, which is calibrated for
low-output calls where >10s indicates a misconfigured reasoning mode.
"""

LLM_TOOL_CONTEXT_TIMEOUT_SECS: int = 20
"""Non-reasoning calls with full tool context (reasoning_effort=none, 28 built-in tools, ~10K schema tokens).

MCP servers are stripped from test configs (mcp_servers={}) to keep the tool count at 28.
Tool schemas are sent in every request: 28 tools × avg schema = ~41K bytes ≈ 10K tokens.
Processing 10K schema tokens without reasoning takes ~12s on this hardware (confirmed:
reasoning_effort=none verified in request, no thinking output, pure KV-fill cost).
Use for tool-selection tests (test_tool_calling_functional) and approval-flow tests
(test_commands) that require the production tool set.
"""

LLM_GEMINI_NOREASON_TIMEOUT_SECS: int = 30
"""Gemini cloud noreason calls (thinking_level=low for Pro, thinking_budget=0 for Flash).

Gemini Pro minimum is thinking_level=low — not truly no-thinking like Ollama's think=false.
Network round-trip + minimal thinking trace exceeds the 10s Ollama budget.
30s allows for API latency variance without masking runaway thinking.
"""

LLM_REASONING_TIMEOUT_SECS: int = 60
"""Full chain-of-thought calls (reasoning/thinking enabled).

Thinking traces consume output budget before visible content; 60s gives the
32K num_predict budget room to complete without false timeout failures.
"""

# HTTP / external services
HTTP_EXTERNAL_TIMEOUT_SECS: int = 10
"""Outbound HTTP calls to external services (web search, web fetch, APIs)."""

HTTP_HEALTH_TIMEOUT_SECS: int = 15
"""Integration health probes: Ollama /api/tags ping, MCP list, capabilities check."""

# Local async operations
SUBPROCESS_TIMEOUT_SECS: int = 10
"""Background task / subprocess: start + first result cycle."""

SUBPROCESS_START_TIMEOUT_SECS: int = 5
"""Subprocess spawn or first-tick check (process just launched)."""

FILE_DB_TIMEOUT_SECS: int = 30
"""Filesystem + SQLite operations: knowledge index sync, session restore, task status reads."""
