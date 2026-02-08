# TODO ROI Ranking

Last updated after ModelRetry normalization shipped (v0.3.4).

| TODO | Effort | User Impact | Dependencies | ROI |
| --- | --- | --- | --- | --- |
| MCP Client (Phase 1) | Medium | High | None | **Best** |
| Slack Phase 2 | Small | Medium | None | **High** |
| Subprocess Fallback | Small | Low (niche) | None | Medium |
| Cross-Tool RAG | Large | High (at scale) | sqlite-vec, Ollama | Low |
| Approval Flow Extraction | Medium | None (enabler) | None | Low |
| Approval/Interrupt Tests | Medium | None (safety net) | Partial on extraction | Low |

## Recommendations

- **MCP Client**: best next ROI. Unlocks external tool servers, every peer is shipping this.
- **Slack Phase 2**: best quick win. Small effort, direct user value.

## Skip for Now

- **Cross-Tool RAG**: highest effort. Value mostly materializes at scale (100+ notes).
- **Approval Flow Extraction**: low priority refactor — current code works, do it when a second consumer needs the orchestration without Rich.
