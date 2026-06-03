"""Shared timeout budgets for tests — the single source of truth.

Two layers, both test-specific and both owned here. Neither belongs in
pyproject.toml, which carries only infra-level pytest config (addopts,
testpaths, pythonpath, asyncio loop scope, and ``session_timeout`` — the
whole-run wall-clock guard).

Layer 1 — per-await ``asyncio.timeout(N)`` budgets (LLM_*, HTTP_*, FILE_DB_*,
BG_TASK_*). Every IO-bound await is wrapped with one of these constants. That
is the primary guard — it fires from inside the test and identifies exactly
which call ran over. Import from here instead of hardcoding numbers inline so
a single edit relaxes or tightens a budget across every test that uses it.

Layer 2 — the per-test pytest-timeout ceiling (``PYTEST_PER_TEST_TIMEOUT_SECS``),
the safety net for sync hangs (fixture setup, subprocess, file IO with no
await) and awaits accidentally left unwrapped. ``conftest.pytest_configure``
applies it to ``config.option.timeout``; it is deliberately NOT set in
pyproject.toml, because it is a calibrated *testing* budget (derived from the
Layer-1 constants below), not infra config.

Usage::

    from tests._timeouts import LLM_NON_REASONING_TIMEOUT_SECS

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


LLM_COMPACTION_SUMMARY_TIMEOUT_SECS: int = 60
"""Compaction LLM summarizer calls (reasoning disabled, no tool schemas).

Output is long-form structured text (400–900 tokens). At ~30 tok/s on local
35B hardware, worst observed output (883 tokens) takes ~29s. 60s gives 2x
headroom without masking a stalled call.
Distinct from LLM_NON_REASONING_TIMEOUT_SECS, which is calibrated for
low-output calls where >10s indicates a misconfigured reasoning mode.
"""

LLM_REASONING_TIMEOUT_SECS: int = 30
"""Reasoning-mode calls (think enabled, simple prompt, no tool schemas).

For tests that intentionally exercise the reasoning settings path. The model
emits thinking tokens before the final response; even a one-word answer takes
several seconds on local 35B hardware. 30s covers worst-case thinking budget
on a low-output prompt without masking a stall.
"""

LLM_TOOL_CONTEXT_TIMEOUT_SECS: int = 50
"""Reasoning calls with full tool context (~16 built-in tools after MCP-strip, ~10K schema tokens).

Qwen3-family models on Ollama need reasoning enabled to emit structured tool_calls
reliably; with think disabled they fall back to free-form text. Reasoning + tool
schemas raises the per-call budget: prefill (~10K schema tokens) plus thinking
tokens before the tool_calls array typically lands in 20-40s on local 27-35B
hardware. 50s gives ~25% headroom; multi-step tests double via *2.
Use for tool-selection tests (test_tool_calling_functional) and approval-flow tests
(test_commands) that require the production tool set.
"""

# Per-test pytest-timeout ceiling (Layer 2 — applied via conftest, not pyproject.toml)
PYTEST_PER_TEST_TIMEOUT_SECS: int = 180
"""Per-test pytest-timeout safety-net ceiling. Applied by ``conftest.pytest_configure``.

Derived from the Layer-1 budgets above, not an infra constant — which is why
it lives here and not in pyproject.toml. The largest single per-await budget is
LLM_TOOL_CONTEXT_TIMEOUT_SECS doubled by callers (50s x 2 = 100s for
approval-roundtrip tool tests). The ceiling additionally covers infra prep
around the wrapped await — notably ensure_ollama_warm()'s mid-suite KV-cache
flush, which can take ~50s after a heavy preceding test. So
≈ 100s wrapped + ~50s flush + small overhead = 180s. Per-call budgets remain
warm-only and never include cold start; that separation is preserved here. A
test that legitimately needs more than this ceiling must wrap each sequential
LLM call with its own asyncio.timeout and use @pytest.mark.timeout(N) to raise
the outer safety net to N = sum(per-call budgets) + infra prep + overhead.
Raise individual constants if warm-call latency increases; raise this ceiling
only when correctly-wrapped sequential calls plus infra prep exceed it.
"""

# HTTP / external services
HTTP_HEALTH_TIMEOUT_SECS: int = 15
"""Integration health probes: Ollama /api/tags ping, MCP list, capabilities check."""

# Local async operations
FILE_DB_TIMEOUT_SECS: int = 30
"""Filesystem + SQLite operations: knowledge index sync, session restore, task status reads."""

# Background subprocess management
BG_TASK_COMPLETION_TIMEOUT_SECS: int = 15
"""Wait-for-completion ceiling on background subprocess `_monitor_task` awaits.

Covers fast shell commands like `seq` / `echo` loops used as test fixtures —
these are essentially instantaneous; 15s is a generous safety net, not a tight
budget. >15s means the monitor task hung, not that the subprocess is slow."""

BG_TASK_TEARDOWN_TIMEOUT_SECS: int = 5
"""Kill / log-appear ceiling on background subprocess teardown awaits.

Covers `kill_task` (SIGTERM→SIGKILL) and poll-for-log-file-existence loops.
Both should complete in sub-second under normal conditions; 5s is the
safety-net ceiling."""
