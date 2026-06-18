"""Shared LLM timeout constants for the context package."""

LLM_RUN_TIMEOUT_SECS: int = 120
"""Hard ceiling per agent.run_stream_events() call — from request sent to last token received.

Also used by summarize_messages() as its per-call ceiling. The summarization
timeout matches the run timeout because the /compact command (the only caller
without an outer run timeout) should be capped at the same bound.
On a warm model the heaviest summarization step measures ~41s; 120s gives 3×
headroom. Cold-model startup is infra, not behavior.
"""
