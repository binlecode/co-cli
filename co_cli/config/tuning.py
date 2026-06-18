"""Non-configurable size-control constants for the context pipeline, centralized.

User-tunable knobs live in CompactionSettings (config/compaction.py); these are the
fixed constants that drive summary sizing, the circuit breaker, boundary retention,
tool-result eviction/spill, and the char->token estimate. Grouped by function-driven
prefix so each name maps to the mechanism it drives.

Bare constants only — no pydantic model, no IO, no import-time side effects. This is a
leaf module: it imports nothing, so every other package can depend on it freely.
"""

# ---------------------------------------------------------------------------
# SUMMARY_* — summarizer output budget + the cap/fit arithmetic (summarization.py)
# ---------------------------------------------------------------------------

# Aim the summary at ~1/4 of the compressed region.
SUMMARY_BUDGET_RATIO = 0.25

# Floor mirrors hermes _MIN_SUMMARY_TOKENS; high enough that the worst-case cap
# (2000 * 1.3 = 2600) comfortably clears the fixed-section template scaffold plus
# the two mandatory verbatim quotes (## Active Task, ## Next Step), so a small
# region never truncates mid-structure (the Mode-B failure this guards against).
SUMMARY_BUDGET_FLOOR = 2_000

# Ceil pulled down from hermes' 12000 to fit co's 8192 ceiling: 6000 * 1.3 = 7800 < 8192.
SUMMARY_BUDGET_CEIL = 6_000

# Overshoot-tolerance cushion (not a bare cut-off margin — a margin would be
# ~1.04-1.13): hard cap = budget * this. Deliberately loose because over-running
# a summary target slightly is cheap while truncating a trailing section is not.
# Tunable from traces (output_tokens / budget = overshoot); headroom to ~1.36
# before CEIL * ratio reaches 8192.
SUMMARY_CAP_OVERSHOOT_RATIO = 1.3

# Defensive fallback for the noreason output ceiling when a future noreason
# settings entry omits max_tokens (every current config carries it: Ollama 8192,
# Gemini 16384). Matches the Ollama default so a missing entry never raises the cap.
SUMMARY_NOREASON_CEILING_FALLBACK = 8_192

# Fixed token headroom held back from the hard window for the pre-flight fit guard.
# NOT a ratio (its neighbours SUMMARY_*_RATIO are fractional) — a flat absolute
# reserve against the char/4 estimator's slop so a prompt estimated just under the
# window does not 400 at the provider. Deliberately small: the bias is toward
# *attempting* the summary (a false "too large" needlessly discards the recap), so
# this only catches the genuinely-oversized region. Trace-tunable from
# INPUT_TOO_LARGE vs late-400 rates.
# Independent of SUMMARY_BUDGET_FLOOR despite the shared 2_000 value — unrelated
# knobs, no shared expression; do NOT collapse them.
SUMMARY_FIT_SAFETY_MARGIN = 2_000

# ---------------------------------------------------------------------------
# BREAKER_* — summarizer circuit breaker (compaction.py)
# ---------------------------------------------------------------------------

# Consecutive summarization failures that trip the circuit breaker.
BREAKER_TRIP = 3

# Once tripped, allow one LLM probe attempt every N blocked calls.
# First probe fires at skip_count == TRIP + PROBE_EVERY (i.e. 13), then every
# PROBE_EVERY counts thereafter (23, 33, …). A successful probe resets the counter.
BREAKER_PROBE_EVERY = 10

# ---------------------------------------------------------------------------
# BOUNDARY_* — turn-group retention invariant (_compaction_boundaries.py)
# ---------------------------------------------------------------------------

# Minimum number of turn groups the planner must retain in the tail.
# Hardcoded correctness invariant — setting it to 0 breaks the planner.
# Not user-configurable. The last turn group is retained unconditionally
# even when its tokens alone exceed ``tail_fraction * budget``.
BOUNDARY_MIN_RETAINED_TURN_GROUPS = 1

# ---------------------------------------------------------------------------
# EVICT_* — old-tool-result eviction (history_processors.py)
# ---------------------------------------------------------------------------

# Keep the N most-recent tool returns per tool name; clear older.
# Borrowed from ``fork-claude-code/services/compact/timeBasedMCConfig.ts:33``
# (``keepRecent: 5``). Not convergent across peers — codex, hermes, and
# opencode do not have per-tool recency retention. Not tuned specifically for
# co-cli's tool surface; revisit via ``evals/eval_compaction_quality.py`` if a
# retention/fidelity tradeoff becomes measurable.
EVICT_KEEP_RECENT = 5

# ---------------------------------------------------------------------------
# SPILL_* — emit-time tool-result spill + preview (tool_io.py)
# ---------------------------------------------------------------------------

SPILL_THRESHOLD_CHARS = 4_000
SPILL_PREVIEW_CHARS = 1_500

# Sentinel tags wrapping a spilled tool result's preview placeholder. Open/close
# pair — keep them co-located; the spill writer emits both and history_processors
# detects an already-spilled part by the opening tag.
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"

# ---------------------------------------------------------------------------
# TOOLCAP_* — per-model-request tool-call cap (agent/toolset.py, orchestrate.py)
# ---------------------------------------------------------------------------

# Small ollama models lose coherence past ~3 parallel calls per response; well
# within the 64K-floor tail.
MAX_TOOL_CALLS_PER_MODEL_REQUEST = 3

# Consecutive cap violations before the loop hard-stops the turn.
TOOL_CAP_HARD_STOP_CONSECUTIVE: int = 3

# ---------------------------------------------------------------------------
# ESTIMATE_* — char->token proxy (tokens.py)
# ---------------------------------------------------------------------------

ESTIMATE_CHARS_PER_TOKEN = 4
