# REVIEW: Web Search Tool vs Top Systems

**Date:** 2026-02-08  
**Scope:** `web_search` + `web_fetch` in `co_cli/tools/web.py`, compared to current top CLI-agent systems.

## MVP Lens (from `CLAUDE.md`)

Focus on converged safety/policy practices first, then expand capability.  
For an agent product, minimum viable quality is: safe fetch targets, explicit permission policy, and predictable payload handling.

## Findings (Ordered by Severity)

### 1. High: `web_fetch` lacks network-target safety guards (SSRF/private-network access)

**Evidence**
- `web_fetch` accepts any `http(s)` URL and follows redirects (`co_cli/tools/web.py:108`, `co_cli/tools/web.py:116`).
- There is no host/IP validation before request execution (`co_cli/tools/web.py:113`).
- Design doc flags this as a known limitation (`docs/DESIGN-12-tool-web-search.md:78`).

**MVP recommendation**
- Add URL safety gate for `web_fetch`: block loopback/link-local/private ranges + metadata endpoints; re-check on redirects.

### 2. High: Web tools have no dedicated URL/domain permission layer

**Evidence**
- `web_search` and `web_fetch` are read-only and auto-run in normal mode (`co_cli/agent.py:131`, `co_cli/agent.py:132`).
- Design currently maps read-only => no approval (`docs/DESIGN-12-tool-web-search.md:13`, `docs/DESIGN-12-tool-web-search.md:73`).

**MVP recommendation**
- Add `web_permission_mode=allow|ask|deny` (default `allow` for backward compatibility), applied to `web_fetch` first.

### 3. Medium: `web_fetch` payload handling is not content-aware

**Evidence**
- Non-HTML responses still decode via `resp.text` (`co_cli/tools/web.py:132`, `co_cli/tools/web.py:133`).
- Truncation is char-based only (`co_cli/tools/web.py:135`).

**MVP recommendation**
- Add textual content-type allowlist and byte-limit guard before decode; return explicit `ModelRetry` on unsupported/binary payloads.

### 4. Medium: Tests miss key web-safety paths

**Evidence**
- Existing tests focus on happy/validation paths (`tests/test_web.py:45`, `tests/test_web.py:99`, `tests/test_web.py:127`).
- No tests for private-network redirect blocking, binary rejection, or payload-limit boundaries.

**MVP recommendation**
- Add focused tests for policy + network-target + payload guardrails.

### 5. Low (Post-MVP): Retrieval feature parity gaps

**Evidence**
- `web_search` currently supports only `query` + capped `max_results` (`co_cli/tools/web.py:40`, `co_cli/tools/web.py:53`).

**Post-MVP recommendation**
- Add optional `domains`, `recency`, and pagination/cursor metadata after safety baseline lands.

## Top-System Comparison Snapshot (Search Tooling)

| System | Built-in web search | URL fetch | Permission posture for web access | Relative position vs co-cli |
|---|---|---|---|---|
| co-cli | Yes (`web_search`) | Yes (`web_fetch`) | No dedicated URL/domain policy; read-only tools auto-run | Baseline |
| Claude Code | Yes (`WebSearch`) | Yes (`WebFetch`) | Explicit allow/ask/deny policy model | Ahead on policy controls |
| Gemini CLI | Yes (`google_web_search`) | Yes (`web_fetch`) | Confirmation/trust model + grounded web responses | Ahead on grounding + controls |
| Codex CLI | Yes | Yes (via web workflow) | Strong approval/sandbox policy surface | Ahead on policy surface |
| Copilot CLI | No first-class search tool in reviewed docs | Yes (`web_fetch`) | URL allow/deny + approval controls | Ahead on URL policy plane |
| Aider | No first-class search tool | URL ingestion via `/web` | Different model (URL ingest vs dedicated search tool) | Not direct parity |
| OpenCode | Yes (`websearch`) | Yes (`webfetch`) | Per-tool allow/ask/deny permissions | Ahead on permission ergonomics |
| Goose | Via extensions/MCP | Via extensions/MCP | Mode + per-tool permission controls | More extensible, less batteries-included |

## MVP-First Delivery Plan

1. **MVP-1:** `web_fetch` network safety gate (private IP/metadata/redirect checks) + tests.
2. **MVP-2:** `web_permission_mode` for web access (apply to `web_fetch`; default compatibility).
3. **MVP-3:** content-type and payload-size safeguards + tests.
4. **Post-MVP:** richer search controls (`domains`, `recency`, pagination).

## Strengths Worth Preserving

- Clear, small return contract with stable `display` + metadata (`co_cli/tools/web.py:96`, `co_cli/tools/web.py:141`).
- Tight timeout defaults and hard fetch-size cap (`co_cli/tools/web.py:13`, `co_cli/tools/web.py:14`, `co_cli/tools/web.py:15`).
- Good `ModelRetry` error mapping for self-correction loops (`co_cli/tools/web.py:67`, `co_cli/tools/web.py:121`).
