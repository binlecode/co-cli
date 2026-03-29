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
LLM_NON_REASONING_TIMEOUT_SECS: int = 60
"""Non-thinking model calls (reasoning_effort=none, ROLE_SUMMARIZATION).

60s accommodates thinking models (e.g. qwen3.5-think variants) that can still emit
reasoning tokens on complex prompts (tool-calling, multi-step tasks) despite
reasoning_effort=none. Fast models resolve in 1–5s; individual calls on thinking
models have been observed at 25–30s under sequential load — 60s catches truly hung
calls without false-positive timeouts from thinking-model variance.
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
