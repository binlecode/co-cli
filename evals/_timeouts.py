"""Shared asyncio.timeout constants for evaluation (eval) files.

Evals execute full system behavior, often requiring multiple LLM turns,
chain-of-thought processing, and external API calls. These timeouts are
intentionally looser than functional tests to measure system degradation
and performance without false-positive failures, while still enforcing
a firm fail-fast upper bound for debugging.
"""

EVAL_TURN_TIMEOUT_SECS: int = 120
"""Standard upper bound for a single complex reasoning turn with tool executions.
Used in knowledge pipeline, tool chains, and history boundary evals.
"""

EVAL_CHAIN_TIMEOUT_SECS: int = 300
"""Upper bound for an entire multi-turn autonomous chain.
Used when an agent must recover from errors or search/fetch/save multiple times.
"""

EVAL_SUMMARIZATION_TIMEOUT_SECS: int = 30
"""Upper bound for background summarization tasks during evals.
Slightly relaxed compared to tests/ to handle heavy context loads.
"""

EVAL_PROBE_TIMEOUT_SECS: int = 5
"""Reachability probe timeout (e.g. checking if Ollama/MCP is alive before eval)."""

EVAL_BENCHMARK_TIMEOUT_SECS: int = 300
"""Timeout for long-running benchmark streams evaluating TTFT and throughput."""

EVAL_API_TIMEOUT_SECS: int = 30
"""Upper bound for external API calls inside evals (e.g. searching, fetching APIs)."""

EVAL_DB_TIMEOUT_SECS: int = 45
"""Upper bound for file and database indexing, compaction, and sqlite loads."""
