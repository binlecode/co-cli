# Remaining Adoptions Backlog

Design logic for completed items has been merged into the relevant DESIGN docs. This file tracks only incomplete work.

**Status key:** PARTIAL = partially implemented, OPEN = not yet implemented, BLOCKED = blocker identified

---

### 2.5 FinishReasonLength Detection — DONE

**Source:** Aider (`base_coder.py:1492-1505`, `FinishReasonLength` exception handling)

**Design:** Replaced the 95%-of-`max_tokens` heuristic in `_orchestrate.py` with an exact check: `result.response.finish_reason == "length"`. Access path is `AgentRunResult.response.finish_reason` — `response` is a property returning the last `ModelResponse`, which carries `finish_reason: FinishReason | None` (normalized to OTel values: `"stop"`, `"length"`, `"content_filter"`, `"tool_call"`, `"error"`). `FinishReason` imported from `pydantic_ai` directly. When the model hits its output token limit, the user sees: *"Response may be truncated (hit output token limit). Use /continue to extend."* Normal responses emit nothing.

---

### 3.6 Progressive Knowledge Loading — BLOCKED

**Source:** Claude Code (`plugins/plugin-dev/skills/agent-development/SKILL.md`, index + on-demand reference sections)

**Problem:** When the lakehouse tier ships, all article content should not be loaded upfront — doing so degrades context quality as the corpus grows.

**Design:** `articles/*/index.md` as summary, `references/` and `examples/` loaded on demand. `recall_article` returns the index; `read_article_detail` loads specific sections.

**Blocker:** Lakehouse tier (`TODO-knowledge-articles.md`) is not implemented. Progressive loading must be a first-class design decision when that work starts, not a retrofit — track here so it is not missed.

**Where:** `tools/knowledge.py` (future).

---

## Status Summary

| Item | Status |
|------|--------|
| 2.5 FinishReasonLength Detection | DONE |
| 3.6 Progressive Knowledge Loading | BLOCKED |

**Last checked:** 2026-02-25
