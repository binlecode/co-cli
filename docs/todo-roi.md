# TODO ROI Ranking

Last updated against current TODO set (2026-02-08).

Sources:
- `docs/RESEARCH-cli-agent-tools-landscape-2026.md`
- OpenClaw reference implementation analysis
- `co_cli/main.py`
- `co_cli/tools/web.py`
- `co_cli/config.py`

| TODO | Effort | User Impact | Dependencies | ROI |
| --- | --- | --- | --- | --- |
| **Model Fallback Chain** (OpenClaw pattern) | Medium | High (Gemini/Ollama graceful degradation) | None | **Best** |
| MCP Client Support — Phase 1 (`docs/TODO-mcp-client.md`) | Medium | High (extensibility + ecosystem parity) | None | **Best** |
| **Context Window Guard** (OpenClaw pattern) | Small | Medium (prevents silent truncation) | None | **High** |
| Subprocess Fallback Policy (`docs/TODO-subprocess-fallback-policy.md`) | Small | Medium (safety clarity + trust) | None | Medium-High |
| **Session Persistence** (OpenClaw pattern) | Medium | Medium-High (resume, cost tracking, audit) | None | Medium-High |
| Slack Tooling — Phase 2/3 (`docs/TODO-slack-tooling.md`) | Small-Medium | Medium | None | Medium-High |
| **Auth Profile Rotation** (OpenClaw pattern) | Medium | Medium (Brave rate limits, multi-account) | None | Medium |
| Cross-Tool RAG (`docs/TODO-cross-tool-rag.md`) | Large | High (at scale) | sqlite-vec, embedding/reranker stack | Low |

## Pattern Details (from OpenClaw)

### Model Fallback Chain
- **What:** Declarative primary/fallback model list in config (`gemini-2.0-flash` → `ollama:glm-4.7-flash`)
- **Why:** Enables graceful degradation when Gemini quota exhausted or provider down
- **Implementation:**
  - Config: `ModelFallback` dataclass with `primary` + `fallbacks` list
  - Agent factory: return fallback config
  - Chat loop: iterate candidates on `FailoverError`, classify error reason
  - Error classification: extend `_provider_errors.py` with `FailoverReason` enum
- **Key OpenClaw files:** `src/agents/model-fallback.ts`, `src/agents/pi-embedded-runner/run.ts:73-200`

### Context Window Guard
- **What:** Hard minimum (16K tokens) and warning threshold (32K) validation
- **Why:** Detects misconfigured models (e.g., `tinyllama:1b` with 2K context), prevents silent truncation
- **Implementation:**
  - Config: `KNOWN_MODELS` dict with `context_window` metadata
  - Agent factory: validate before creating agent, warn/fail based on thresholds
  - History processor: respect model's context in `truncate_history_window()`
  - Status display: show context window at startup
- **Key OpenClaw files:** `src/agents/context-window-guard.ts`, `src/agents/pi-embedded-runner/run.ts:115-140`

### Session Persistence
- **What:** JSON store (`sessions.json`) + JSONL transcript per session
- **Why:** Enable `/resume <session-id>`, token cost tracking, audit trail
- **Implementation:**
  - Phase 1: Minimal `SessionEntry` dataclass (id, timestamps, token counts, provider/model)
  - Module: `co_cli/_sessions.py` with `load_session()`, `save_session()`
  - Integration: Save on each turn in main loop, show session ID on exit
  - Phase 2: JSONL transcript, resume command
- **Key OpenClaw files:** `src/config/sessions/store.ts`, `src/config/sessions/types.ts`

### Auth Profile Rotation
- **What:** Multi-profile credential store with exponential backoff cooldown (`5^(errorCount-1)` capped 1h)
- **Why:** Handle Brave Search rate limits, future multi-account Google/Slack
- **Implementation:**
  - Phase 1: Cooldown for Brave Search only (`api-profiles.json` with `lastFailedAt`, `errorCount`)
  - Check cooldown before `web_search`, mark used/failed after execution
  - Phase 2: Multi-profile support for Google + Slack with round-robin
- **Key OpenClaw files:** `src/agents/auth-profiles/usage.ts`, `src/agents/auth-profiles/order.ts`

## Recommendations

- **Do first:** Model Fallback Chain OR MCP Client (both "Best" ROI; fallback is faster to implement)
- **Then:** Context Window Guard (quick defensive win)
- **After that:** Subprocess Fallback Policy and Session Persistence
- **Later:** Auth Profile Rotation (when Brave rate limits hit or multi-account needed)

## Skip for Now

- **Cross-Tool RAG**: highest effort; value mainly materializes with larger corpora and multi-source retrieval pressure.

## Done

- **Agent Tool-Call + Recursive Flow Hardening** (was `TODO-agent-toolcall-recursive-flow.md`): Phase C (provider/tool error normalization) and Phase D (orchestration extraction) implemented. `_orchestrate.py`, `_provider_errors.py`, `tools/_errors.py` expanded, `TerminalFrontend` added. TODO file removed.
- **Streaming Thinking Display** (was `TODO-thinking-display.md`): Implemented via `TerminalFrontend.on_thinking_delta/on_thinking_commit`, verbose-gated thinking rendering in `_orchestrate.py`, and regression coverage in `tests/test_display.py` + `tests/test_orchestrate.py`. TODO file removed.
- **Web Tool Hardening MVP** (was `TODO-web-tool-hardening.md`): Implemented unified per-tool `web_policy`, updated config/deps/runtime wiring, and added coverage for deny/ask/security paths. TODO file removed.
