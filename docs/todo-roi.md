# TODO ROI Ranking

| TODO | Effort | User Impact | Dependencies | ROI |
| --- | --- | --- | --- | --- |
| **Model Fallback Chain** (OpenClaw pattern) | Medium | High (Gemini/Ollama graceful degradation) | None | **Best** |
| MCP Client Support — Phase 1 (`docs/TODO-mcp-client.md`) | Medium | High (extensibility + ecosystem parity) | None | **Best** |
| **Context Window Guard** (OpenClaw pattern) | Small | Medium (prevents silent truncation) | None | **High** |
| Subprocess Fallback Policy (`docs/TODO-subprocess-fallback-policy.md`) | Small | Medium (safety clarity + trust) | None | Medium-High |
| **Session Persistence** (OpenClaw pattern) | Medium | Medium-High (resume, cost tracking, audit) | None | Medium-High |
| Slack Tooling — Phase 2/3 (`docs/TODO-slack-tooling.md`) | Small-Medium | Medium | None | Medium-High |
| **Auth Profile Rotation** (OpenClaw pattern) | Medium | Medium (Brave rate limits, multi-account) | None | Medium |
| **User Workflow Preferences** | Small-Medium | High (personalization, fit to user patterns) | None | Medium-High |
| **Skills System** (Claude Code pattern) | Small-Medium | High (domain knowledge injection, zero-code extensibility) | None | Medium-High |
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

### User Workflow Preferences
- **What:** Local user-driven settings to adapt agent behavior to specific workflows (e.g., verbosity, tool usage patterns, approval preferences, output formats)
- **Why:** Enable personalization so the agent fits naturally into each user's unique work patterns and preferences
- **Implementation:**
  - Workflow profiles stored in user config (`~/.config/co-cli/workflows.json`)
  - Settings can control: default tool approval modes, output verbosity levels, preferred file formats, chat behavior (proactive vs reactive)
  - Profile switching via command flag or interactive menu
  - Auto-learn patterns from user approval history (optional)
- **Potential settings:**
  - `auto_approve_tools`: list of trusted tools that don't need approval prompts
  - `verbosity_level`: minimal/normal/verbose output
  - `output_preferences`: preferred formats for different data types
  - `proactive_suggestions`: whether agent offers suggestions vs waits for explicit requests

### Skills System
- **What:** Knowledge modules (markdown) that teach the agent domain expertise—patterns, workflows, conventions
- **Why:** Zero-code extensibility. Users inject knowledge without writing Python tools
- **Architecture:** Agent → Skills (optional guidance) → Tools (execution)
- **Implementation:**
  - Use native pydantic-ai `@agent.instructions` decorator for runtime injection
  - Skills directory: `.co-cli/skills/<name>/`
    - `SKILL.md` (required) - Core guidance loaded via `@agent.instructions`
    - `scripts/` (optional) - Skill-specific utilities (Codex pattern: deterministic/repeated ops)
    - `references/` (optional) - Deep-dive docs loaded on demand
  - Skill-specific scripts vs general tools:
    - General tools (co_cli/tools/) - Used across domains (shell, web_search)
    - Skill scripts (skills/<name>/scripts/) - Domain-specific utilities (django-security/check-csrf.py)
- **Examples:** `django-security/SKILL.md` + `scripts/check-csrf.py`, `react-patterns/SKILL.md`
- **2026 best practice:** All top systems (Codex, Claude Code, Gemini CLI, OpenCode) now use skills
- **Key reference:** Codex `core/src/skills/assets/samples/skill-creator/SKILL.md`

## Recommendations

- **Do first:** Model Fallback Chain OR MCP Client (both "Best" ROI; fallback is faster to implement)
- **Then:** Context Window Guard (quick defensive win)
- **After that:** Subprocess Fallback Policy and Session Persistence
- **Later:** Auth Profile Rotation (when Brave rate limits hit or multi-account needed)

## Skip for Now

- **Cross-Tool RAG**: highest effort; value mainly materializes with larger corpora and multi-source retrieval pressure.
