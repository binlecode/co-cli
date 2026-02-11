# FIX: CoDeps Settings Injection — Eliminate Hybrid Access Pattern

## Problem

CoDeps had two competing config access patterns:

1. **Flat fields** (`ctx.deps.brave_search_api_key`) — batch 1-5 tools
2. **Whole Settings object** (`ctx.deps.settings.memory_max_count`) — memory tools

This created:

- **Ambiguity** for new code — which pattern to use?
- **Divergence trap** — `deps.auto_confirm` and `deps.settings.auto_confirm` could hold different values at runtime
- **DI violation** — `truncate_history_window` had `ctx: RunContext[CoDeps]` but read the global singleton instead of using dependency injection

## Fix

### 1. Flatten memory + history settings into CoDeps

Removed `settings: "Settings"` field from `CoDeps`. Added 8 flat fields:

| Field | Default | Was |
|-------|---------|-----|
| `memory_max_count` | 200 | `ctx.deps.settings.memory_max_count` |
| `memory_dedup_window_days` | 7 | `ctx.deps.settings.memory_dedup_window_days` |
| `memory_dedup_threshold` | 85 | `ctx.deps.settings.memory_dedup_threshold` |
| `memory_decay_strategy` | "summarize" | `ctx.deps.settings.memory_decay_strategy` |
| `memory_decay_percentage` | 0.2 | `ctx.deps.settings.memory_decay_percentage` |
| `max_history_messages` | 40 | global `settings.max_history_messages` |
| `tool_output_trim_chars` | 2000 | global `settings.tool_output_trim_chars` |
| `summarization_model` | "" | global `settings.summarization_model` |

`create_deps()` in `main.py` reads `Settings` once and injects scalar values.

### 2. Async history processor uses DI

`truncate_history_window` now reads `ctx.deps.max_history_messages` and `ctx.deps.summarization_model` instead of importing the global singleton.

`truncate_tool_returns` (sync processor) still reads global settings directly — framework constraint, sync processors have no `RunContext`.

## Files Modified

| File | Change |
|------|--------|
| `co_cli/deps.py` | Remove `settings` field, add 8 flat fields |
| `co_cli/main.py` | Update `create_deps()` constructor |
| `co_cli/tools/memory.py` | `ctx.deps.settings.X` → `ctx.deps.X` (6 callsites) |
| `co_cli/_history.py` | `truncate_history_window` uses `ctx.deps`, comment on sync processor |
| `tests/test_history.py` | Update `_real_run_context` to pass new fields to CoDeps |
| `tests/test_memory_lifecycle.py` | Update `mock_ctx` fixture — flat fields, no Settings import |
