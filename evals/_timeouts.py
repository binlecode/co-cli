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

EVAL_MEMORY_EXTRACTION_TIMEOUT_SECS: int = 15
"""Upper bound for one memory-extractor LLM call in evals.

The extractor uses NOREASON settings and a single write tool with a short delta
window, so it should complete materially faster than a full foreground turn.
15s leaves room for one local-model call plus file/DB writes while still
failing fast on regressions that would otherwise hide behind 60-120s turn
timeouts.
"""

EVAL_E2E_BOOTSTRAP_TIMEOUT_SECS: int = 30
"""End-to-end timeout for the bootstrap eval scenario (create_deps() + probes).

Individual IO-bound operations inside create_deps() (MCP connect, SQLite
init, Ollama context probe) should carry their own timeouts. This constant
bounds the full scenario run so a hang in any unguarded step still fails fast.
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

EVAL_DEEP_LEARNING_TURN_TIMEOUT_SECS: int = 360
"""Per-turn upper bound for the UAT deep-learning step (step_15 in compaction eval).

Empirical basis: M3 proactive compaction over a ~45K-token dropped zone measured
~289s end-to-end (summarizer LLM call + history rewrite + tool persistence).
360s = 289s measured + 70s headroom for slower hardware and HTTP jitter.
Keeps the turn from hanging indefinitely while still allowing realistic compaction
to complete before declaring a failure.
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
