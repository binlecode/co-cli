# TODO ROI Ranking

Last updated against current TODO set (2026-02-08).

Sources:
- `docs/RESEARCH-cli-agent-tools-landscape-2026.md`
- `docs/TODO-agent-toolcall-recursive-flow.md`
- `docs/TODO-web-tool-hardening.md`
- `co_cli/main.py`
- `co_cli/tools/web.py`

| TODO (Doc) | Effort | User Impact | Dependencies | ROI |
| --- | --- | --- | --- | --- |
| Agent Tool-Call + Recursive Flow Hardening (`docs/TODO-agent-toolcall-recursive-flow.md`) | Medium | High (loop safety, approval correctness, reliability) | None | **Best** |
| Web Tool Hardening — Remaining Phase 2/3 (`docs/TODO-web-tool-hardening.md`) | Small-Medium | High (security + trust + policy parity) | Phase 1 done | **Best** |
| MCP Client Support — Phase 1 (`docs/TODO-mcp-client.md`) | Medium | High (extensibility + ecosystem parity) | Sequence after web policy hardening | **High** |
| Streaming Thinking Display (`docs/TODO-thinking-display.md`) | Small-Medium | Medium (debuggability, tool-routing visibility) | None | Medium-High |
| Slack Tooling — Phase 2/3 (`docs/TODO-slack-tooling.md`) | Small-Medium | Medium | None | Medium-High |
| Subprocess Fallback Policy (`docs/TODO-subprocess-fallback-policy.md`) | Small | Medium (safety clarity) | None | Medium |
| Approval & Interrupt Regression Tests (`docs/TODO-approval-interrupt-tests.md`) | Medium | Medium (reliability regression shield) | Higher value after orchestration extraction | Medium |
| Cross-Tool RAG (`docs/TODO-cross-tool-rag.md`) | Large | High (at scale) | sqlite-vec, embedding/reranker stack | Low |

## Recommendations

- **Do first:** `TODO-agent-toolcall-recursive-flow.md` Phase A (per-turn recursion budget carry-over) and `TODO-web-tool-hardening.md` Phase 2 (domain/permission policy).
- **Then:** `TODO-mcp-client.md` Phase 1 for extensibility and benchmark parity.
- **Parallel quick wins:** `TODO-thinking-display.md` Phase 1 and `TODO-subprocess-fallback-policy.md` (both small/contained).

## Skip for Now

- **Cross-Tool RAG**: highest effort; value mainly materializes with larger corpora and multi-source retrieval pressure.
- **Deep orchestration extraction work** (inside `TODO-agent-toolcall-recursive-flow.md` Phase D): keep behind concrete need (second consumer/headless runtime).
