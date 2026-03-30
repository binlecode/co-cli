"""Shared asyncio.timeout constants for test files.

Single source of truth for all per-await timeouts. Import from here instead
of hardcoding numbers inline so a single edit relaxes or tightens a budget
across every test that uses it.

Usage::

    from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS, LLM_REASONING_TIMEOUT_SECS

    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await summarize_messages(...)
"""

# LLM inference
LLM_NON_REASONING_TIMEOUT_SECS: int = 10
"""Non-thinking model calls (reasoning_effort=none, ROLE_SUMMARIZATION).

>10s means the model is reasoning when it should not be — a bug in model
config or api_params (e.g. missing reasoning_effort=none).
Use only for bare-context calls (summarizer, signal detector, compaction) — no
registered tools in the agent.
"""

LLM_TOOL_CONTEXT_TIMEOUT_SECS: int = 20
"""Non-reasoning calls with full tool context (ROLE_TASK, 38 tools, ~10K schema tokens).

Tool schemas are sent in every request: 38 tools × avg schema = ~41K bytes ≈ 10K tokens.
Processing 10K schema tokens without reasoning takes ~12s on this hardware (confirmed:
reasoning_effort=none verified in request, no thinking output, pure KV-fill cost).
Use for tool-selection tests (test_tool_calling_functional) and approval-flow tests
(test_commands) that require the production tool set.
"""

LLM_MULTI_SEGMENT_TIMEOUT_SECS: int = LLM_TOOL_CONTEXT_TIMEOUT_SECS * 2
"""Non-reasoning agent.run() calls that execute at least one tool (two LLM segments).

agent.run() drives the full agent loop: first segment selects and executes the tool,
second segment processes the result. Each segment incurs full tool-context KV-fill cost.
Never wrap two sequential awaits under LLM_TOOL_CONTEXT_TIMEOUT_SECS — that budget
covers one call. Use this constant when agent.run() will trigger tool execution.
"""

LLM_DEFERRED_TURN_TIMEOUT_SECS: int = LLM_TOOL_CONTEXT_TIMEOUT_SECS * 3
"""Deferred-approval turns where BOTH segments pay full tool-context KV-fill cost.

When a tool is registered as deferred (requires approval via DeferredToolRequests)
and the user denies it, run_turn() drives two sequential agent.run() calls with no
tool execution between them:
  - Segment 1: model selects the tool → DeferredToolRequests returned
  - Approval prompt shown → user denies
  - Segment 2: model processes ToolDenied → generates verbose response

Both segments load the full 38-tool schema (~10K tokens). No tool actually executes,
so there is no token savings between segments. Total observed: ~35s on this hardware.
Use this constant for any test that denies a deferred tool via run_turn().
Do NOT use LLM_MULTI_SEGMENT_TIMEOUT_SECS for deferred-deny flows — it is too tight.
"""

LLM_REASONING_TIMEOUT_SECS: int = 60
"""Full chain-of-thought calls (ROLE_REASONING with thinking enabled).

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
"""Filesystem + SQLite operations: knowledge index sync, inject_opening_context,
session restore, task status reads.
"""
