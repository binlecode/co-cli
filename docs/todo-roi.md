# TODO ROI Ranking

Last updated against current TODO set (2026-02-08).

Sources:
- `docs/RESEARCH-cli-agent-tools-landscape-2026.md`
- `docs/TODO-web-tool-hardening.md`
- `co_cli/main.py`
- `co_cli/tools/web.py`
- `co_cli/config.py`

| TODO (Doc) | Effort | User Impact | Dependencies | ROI |
| --- | --- | --- | --- | --- |
| MCP Client Support — Phase 1 (`docs/TODO-mcp-client.md`) | Medium | High (extensibility + ecosystem parity) | None | **Best** |
| Subprocess Fallback Policy (`docs/TODO-subprocess-fallback-policy.md`) | Small | Medium (safety clarity + trust) | None | Medium-High |
| Streaming Thinking Display (`docs/TODO-thinking-display.md`) | Small-Medium | Medium (debuggability, tool-routing visibility) | None | Medium-High |
| Slack Tooling — Phase 2/3 (`docs/TODO-slack-tooling.md`) | Small-Medium | Medium | None | Medium-High |
| Web Tool Hardening — Remaining Phase 3 (`docs/TODO-web-tool-hardening.md`) | Small-Medium | Medium (feature parity, lower security urgency) | Phase 1-2 done | Medium-Low |
| Cross-Tool RAG (`docs/TODO-cross-tool-rag.md`) | Large | High (at scale) | sqlite-vec, embedding/reranker stack | Low |

## Recommendations

- **Do first:** `TODO-mcp-client.md` Phase 1.
- **Then:** `TODO-subprocess-fallback-policy.md` and `TODO-thinking-display.md` Phase 1 as contained UX/safety improvements.
- **After that:** parity work in `TODO-web-tool-hardening.md` Phase 3.

## Skip for Now

- **Cross-Tool RAG**: highest effort; value mainly materializes with larger corpora and multi-source retrieval pressure.

## Done

- **Agent Tool-Call + Recursive Flow Hardening** (was `TODO-agent-toolcall-recursive-flow.md`): Phase C (provider/tool error normalization) and Phase D (orchestration extraction) implemented. `_orchestrate.py`, `_provider_errors.py`, `tools/_errors.py` expanded, `TerminalFrontend` added. TODO file removed.
