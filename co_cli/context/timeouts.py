"""Shared LLM timeout constants for the context package."""

SUMMARIZE_CALL_TIMEOUT_SECS: int = 120
"""Per-call ceiling for the single, tool-less /compact LLM call (summarize_messages,
summarization.py) — the only model call without an outer run timeout. Plain absolute
elapsed time, since one call streams no events.

On a warm model the heaviest summarization step measures ~41s; 120s gives 3× headroom.
Cold-model startup is infra, not behavior.

The model-generation STALL window for the agent run loop is no longer defined here: it
is operator-tunable via ``llm.run_stall_timeout_secs`` (config/llm.py), consumed by
``_StallTimer`` / ``_execute_run`` (agent/orchestrate.py).
"""
