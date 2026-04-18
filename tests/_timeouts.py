"""Shared asyncio.timeout constants for test files.

Single source of truth for all per-await timeouts. Import from here instead
of hardcoding numbers inline so a single edit relaxes or tightens a budget
across every test that uses it.

Usage::

    from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS, LLM_TOOL_CONTEXT_TIMEOUT_SECS

    async with asyncio.timeout(LLM_NON_REASONING_TIMEOUT_SECS):
        result = await summarize_messages(...)
"""

# LLM inference
LLM_NON_REASONING_TIMEOUT_SECS: int = 10
"""Non-thinking model calls (noreason settings, reasoning_effort=none).

>10s means the model is reasoning when it should not be — a bug in model
config or api_params (e.g. missing reasoning_effort=none).
Use only for bare-context calls (summarizer, signal detector, compaction) — no
registered tools in the agent.
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
"""Filesystem + SQLite operations: knowledge index sync, append_recalled_memories,
session restore, task status reads.
"""
