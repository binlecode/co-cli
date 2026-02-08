# TODO ROI Ranking

Last updated after web-search benchmark refresh and review (2026-02-08).

Sources:
- `docs/REVIEW-web-search-vs-top-systems.md`
- `docs/RESEARCH-cli-agent-tools-landscape-2026.md`

| TODO | Effort | User Impact | Dependencies | ROI |
| --- | --- | --- | --- | --- |
| Web Safety Hardening (`web_fetch` SSRF/private-network guards + redirect revalidation) | Small-Medium | High (security + trust) | None | **Best** |
| Web Permission Policy (`allow/ask/deny`, URL/domain allowlist/denylist) | Medium | High (control + safety) | None | **High** |
| MCP Client (Phase 1) | Medium | High | None | **High** |
| Web Retrieval Controls (`cached/live/disabled`, domains/recency, pagination metadata) | Medium | Medium-High | Prefer after safety/policy baseline | Medium-High |
| Slack Phase 2 | Small | Medium | None | Medium-High |
| Subprocess Fallback | Small | Low (niche) | None | Medium |
| Approval/Interrupt Tests | Medium | Low-Medium (reliability) | Can start now; fuller coverage after extraction | Medium-Low |
| Cross-Tool RAG | Large | High (at scale) | sqlite-vec, Ollama | Low |
| Approval Flow Extraction | Medium | Low (enabler/refactor) | None | Low |

## Recommendations

- **Do first:** Web safety hardening + web permission policy. These are the largest risk reducers and close the most visible gap vs top systems.
- **Then:** MCP Client (Phase 1) for extensibility and ecosystem parity.
- **Parallel quick win:** Slack Phase 2 (thread reply + reactions).

## Skip for Now

- **Cross-Tool RAG**: highest effort. Value mostly materializes at scale (100+ notes).
- **Approval Flow Extraction**: still not implemented and remains a refactor. Do it when a second consumer needs orchestration without Rich, or when approval-test depth becomes blocked.
