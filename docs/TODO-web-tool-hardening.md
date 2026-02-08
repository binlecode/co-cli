# TODO: Web Tool Hardening

**Origin:** Review of `web_search` + `web_fetch` against top CLI-agent systems (Claude Code, Gemini CLI, Codex, Copilot CLI, OpenCode, Goose, Aider).

---

## Peer Convergence Summary

All top systems that ship web-fetch tools converge on three safety layers co-cli currently lacks:

1. **Network-target guards** — Block private/loopback/link-local IPs and cloud metadata endpoints before request; re-check after redirects. (Claude Code, Gemini CLI, Codex, Copilot CLI)
2. **Domain/URL policy controls** — Allow/block domain lists and a permission mode (`allow|ask|deny`) separate from the read/write tool classification. (Claude Code, Copilot CLI, OpenCode)
3. **Content-aware payload handling** — Allowlist textual content types, reject binary, enforce byte-level body limits before full decode. (Gemini CLI, Codex)

Additional gaps identified: missing safety-path tests, limited search parameters (`domains`, `recency`), and no retrieval-mode toggle (`live|disabled`).

---

## Phase 1 — SSRF Protection + Content-Type Guard

Goal: close the highest-severity gaps — network-target safety and payload handling. Ship the smallest thing that blocks SSRF and binary-payload waste.

### SSRF / private-network guard

New helper `co_cli/tools/_url_safety.py` with `is_url_safe(url: str) -> bool`:

- Resolve hostname to IP(s)
- Block loopback (`127.0.0.0/8`, `::1`), link-local (`169.254.0.0/16`, `fe80::/10`), RFC 1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
- Block cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`)
- Return `False` for any blocked address, `True` otherwise

Integration in `web_fetch`:

- Call `is_url_safe(url)` before issuing the HTTP request — raise `ModelRetry` if unsafe
- After following redirects, re-check the final URL with `is_url_safe()` — raise `ModelRetry` if redirect target is unsafe

### Content-type allowlist

- Before reading the response body, check `Content-Type` header against allowlist: `text/*`, `application/json`, `application/xml`, `application/xhtml+xml`
- Reject non-matching types (binary, images, PDFs, etc.) with `ModelRetry` explaining the restriction
- Enforce byte-level body limit (e.g. 1 MB) before full decode — truncate and note truncation in response

### Tests

- Private IP blocking: `127.0.0.1`, `10.x.x.x`, `192.168.x.x`, `169.254.169.254`
- Redirect-to-private: URL that 302s to a private IP must be blocked
- Content-type rejection: binary content type returns `ModelRetry`
- Truncation edge cases: response at/over byte limit

### Items

- [x] Create `co_cli/tools/_url_safety.py` with `is_url_safe(url)` function
- [x] Add pre-request `is_url_safe()` check to `web_fetch` in `co_cli/tools/web.py`
- [x] Add post-redirect `is_url_safe()` re-check to `web_fetch`
- [x] Add content-type allowlist check before body read in `web_fetch`
- [x] Add byte-level body limit before full decode in `web_fetch`
- [x] Add functional tests: private IP blocking, redirect-to-private, content-type rejection, truncation
- [x] Update `docs/DESIGN-12-tool-web-search.md` security section

### File changes

| File | Change |
|---|---|
| `co_cli/tools/_url_safety.py` | New — `is_url_safe()` helper |
| `co_cli/tools/web.py` | Pre-request + post-redirect URL check, content-type guard, byte limit |
| `tests/test_web.py` | SSRF and content-type safety tests |
| `docs/DESIGN-12-tool-web-search.md` | Document SSRF protection + content-type guard |

---

## Phase 2 — Domain/URL Policy Controls

Goal: give users config-driven control over which domains `web_fetch` can reach, and add a permission mode for web access.

### Domain allowlist / blocklist

New settings in `co_cli/config.py`:

- `web_fetch_allowed_domains: list[str]` — if non-empty, only these domains (and subdomains) are permitted
- `web_fetch_blocked_domains: list[str]` — always blocked, checked even when allowlist is empty
- Checked in `web_fetch` after URL parsing, before request

### Search domain filtering

Optional `domains` parameter on `web_search` — maps to Brave API `site:` operator prefix in query string. Lets the agent scope searches to specific sites without user needing to spell out `site:` syntax.

### Web permission mode

New setting:

- `web_permission_mode: Literal["allow", "ask", "deny"]` — default `"allow"`
- `allow` — web tools run without approval (current behavior, backward-compatible)
- `ask` — web tools require approval via the deferred-approval flow
- `deny` — web tools raise `ModelRetry("web access disabled by policy")`

Integration: check `web_permission_mode` at the top of `web_search` and `web_fetch` tool functions.

### Items

- [x] Add `web_fetch_allowed_domains` and `web_fetch_blocked_domains` settings to `co_cli/config.py`
- [x] Implement domain check in `web_fetch` (before request, after URL parse)
- [x] Add optional `domains` parameter to `web_search` (Brave `site:` prefix)
- [x] Add `web_permission_mode` setting to `co_cli/config.py`
- [x] Implement permission mode check in `web_search` and `web_fetch`
- [x] Add functional tests: domain allowlist/blocklist, permission mode deny/ask
- [x] Update `docs/DESIGN-12-tool-web-search.md` config table with new settings

### File changes

| File | Change |
|---|---|
| `co_cli/config.py` | Add `web_fetch_allowed_domains`, `web_fetch_blocked_domains`, `web_permission_mode` |
| `co_cli/tools/web.py` | Domain checks, permission mode gate, `domains` param on `web_search` |
| `tests/test_web.py` | Domain policy and permission mode tests |
| `docs/DESIGN-12-tool-web-search.md` | Document domain policy + permission mode |

---

## Phase 3 (Post-MVP) — Search Modes + Richer Metadata

Goal: feature parity with top systems on search controls and result richness. Lower priority — ship after safety baseline (Phases 1-2) lands.

### Search mode toggle

New setting:

- `web_search_mode: Literal["live", "disabled"]` — default `"live"`
- `disabled` — `web_search` returns `ModelRetry("web search disabled by policy")` without calling Brave API

### Recency filtering

Add `recency_days` optional parameter to `web_search` — maps to Brave `freshness` parameter. Lets the agent request only recent results (e.g. last 7 days).

### Richer result metadata

Extend `web_search` return dict with additional fields from Brave API response:

- `published_date` per result (when available)
- `next_page_token` for pagination (Brave `offset` parameter)
- `total_estimated` count from Brave response

### Items

- [ ] Add `web_search_mode` setting to `co_cli/config.py`
- [ ] Implement mode check in `web_search`
- [ ] Add `recency_days` parameter to `web_search` (Brave `freshness` mapping)
- [ ] Extend `web_search` return dict with richer metadata fields
- [ ] Add functional tests: search mode disabled, recency filtering
- [ ] Refresh `docs/DESIGN-12-tool-web-search.md` with updated feature landscape
