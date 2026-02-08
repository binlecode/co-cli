# TODO ROI Ranking

Last updated against current TODO set (2026-02-08).

Sources:
- `docs/REVIEW-web-search-vs-top-systems.md`
- `docs/RESEARCH-cli-agent-tools-landscape-2026.md`
- `docs/TODO-web-tool-hardening.md`

| TODO (Doc) | Effort | User Impact | Dependencies | ROI |
| --- | --- | --- | --- | --- |
| Web Tool Hardening (`docs/TODO-web-tool-hardening.md`) | Medium | High (security + trust) | None | **Best** |
| MCP Client Phase 1 (`docs/TODO-mcp-client.md`) | Medium | High | Sequence after Web Hardening Phase 1-2 | **High** |
| Slack Phase 2 (`docs/TODO-slack-tooling.md`) | Small | Medium | None | Medium-High |
| Subprocess Fallback Policy (`docs/TODO-subprocess-fallback-policy.md`) | Small | Low (niche) | None | Medium |
| Approval & Interrupt Tests (`docs/TODO-approval-interrupt-tests.md`) | Medium | Low-Medium (reliability) | Better after extraction, but partial value now | Medium-Low |
| Eval Tool-Calling Expansion (`docs/TODO-eval-tool-calling.md`) | Medium | Low-Medium (quality gate) | None | Medium-Low |
| Approval Flow Extraction (`docs/TODO-approval-flow-extraction.md`) | Medium | Low (enabler/refactor) | None | Low |
| Cross-Tool RAG (`docs/TODO-cross-tool-rag.md`) | Large | High (at scale) | sqlite-vec, Ollama | Low |

## Recommendations

- **Do first:** `TODO-web-tool-hardening.md` Phase 1 and Phase 2 (SSRF/network guardrails + permission policy).
- **Then:** `TODO-mcp-client.md` Phase 1 for extensibility and ecosystem parity.
- **Parallel quick win:** Slack Phase 2 (thread reply + reactions).

## Skip for Now

- **Cross-Tool RAG**: highest effort. Value mostly materializes at scale (100+ notes).
- **Approval Flow Extraction**: still not implemented and remains a refactor. Do it when a second consumer needs orchestration without Rich, or when approval-test depth becomes blocked.
