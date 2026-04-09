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
Calculated as: 4 sequential LLM reasoning calls (~80s total for a 35B model)
+ 3 API/Tool operations (e.g. search, fetch, save) (~15s) + ~25s buffer for HTTP jitter.
This guarantees enough time for complex RAG loops but prevents infinite ModelRetry loops.
"""


EVAL_SUMMARIZATION_TIMEOUT_SECS: int = 20
"""Upper bound for background summarization tasks during evals.
Slightly relaxed compared to tests/ to handle heavy context loads.
Calculated as: 1 non-reasoning LLM call (~10s) + 1 local DB injection (5s) + 5s buffer.
"""

EVAL_PROBE_TIMEOUT_SECS: int = 5
"""Reachability probe timeout (e.g. checking if Ollama/MCP is alive before eval).
Strictly bounds pre-flight checks so broken test infrastructure fails instantly.
"""

EVAL_BENCHMARK_TIMEOUT_SECS: int = 300
"""Timeout for long-running benchmark streams evaluating TTFT and throughput.
Local hardware processing massive 128,000 token context windows can take 60-90s
just for the Time-To-First-Token (TTFT), plus another 50-100s for sustained
generation. 300s (5 minutes) provides the necessary endurance margin.
"""

EVAL_API_TIMEOUT_SECS: int = 10
"""Upper bound for external API calls inside evals (e.g. searching, fetching APIs).
Forces tools to return a ToolError to the LLM rather than hanging the eval.
"""

EVAL_DB_TIMEOUT_SECS: int = 5
"""Upper bound for file and database indexing, compaction, and sqlite loads.
Since SQLite FTS5 operates locally and sequentially on-disk, anything exceeding
5 seconds strongly indicates a disk deadlock or transaction lock issue.
"""
