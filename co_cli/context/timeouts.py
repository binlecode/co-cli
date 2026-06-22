"""Shared LLM timeout constants for the context package."""

LLM_RUN_TIMEOUT_SECS: int = 120
"""Max wall-time the agent will wait for model progress before giving up.

Two callers, two shapes of the same bound:

- _execute_run() (orchestrate.py): a model-generation STALL window, NOT an
  absolute run deadline. It is re-armed on each stream event and DISARMED while
  any tool is executing (no events flow between a tool call and its result), so
  it fires only when the model produces no progress for this many seconds with
  no tool in flight. Per-tool timeouts (e.g. shell, web fetch) own tool liveness;
  a long legit tool is bounded by its own timeout, not this loop.
- summarize_messages() (summarization.py): the per-call ceiling for the single,
  tool-less /compact LLM call (the only caller without an outer run timeout) —
  here the bound is plain absolute elapsed time, since one call streams no events.

On a warm model the heaviest summarization step measures ~41s; 120s gives 3×
headroom. Cold-model startup is infra, not behavior.
"""
