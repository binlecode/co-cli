# TODO: Prompt Architecture Refactoring

## Completed

### Aspect-Driven Adaptive System Prompt

Replaced monolithic `system.md` + tool fragment assembly with aspect-driven, tier-based prompt composition.

**What changed:**
- 7 independent behavioral aspect files in `co_cli/prompts/aspects/` (identity, inquiry, fact_verify, multi_turn, response_style, approval, tool_output)
- Tier system in `model_quirks.py`: tier 1 (minimal), tier 2 (standard), tier 3 (full)
- Tier-dependent personality: tier 1 skips, tier 2 style-only, tier 3 full character+style
- Removed `system.md`, `_tool_general.md`, `_tool_shell.md`, `_tool_memory.md` (tool guidance now lives in docstrings via pydantic-ai tool schemas)
- Removed `tool_names` parameter from `get_system_prompt()`

**Size results (without knowledge/instructions):**

| Tier | Model | No personality | With personality | Budget |
|------|-------|---------------|-----------------|--------|
| 1 | GLM-4.7-flash | ~1,400 chars | ~1,400 (skipped) | <1,500 |
| 2 | llama3.1 | ~1,550 chars | ~2,000 (style only) | <2,500 |
| 3 | gemini (default) | ~1,900 chars | ~3,300 (full) | <4,000 |

**Files:** `co_cli/prompts/__init__.py`, `co_cli/prompts/model_quirks.py`, `co_cli/prompts/aspects/*.md`, `co_cli/prompts/personalities/_composer.py`

## Remaining

### Model Inconsistency on Complex Inquiries

**Status:** Known limitation
**Severity:** Low (workaround available)

**Issue:** Ollama glm-4.7-flash:q8_0 doesn't consistently follow the "1-2 sentences" constraint for complex analytical questions.

**Evidence:**
- Simple inquiry ("When is lunch today?") -> Concise: "No lunch event scheduled today"
- Complex inquiry ("What's my next meeting?") -> Verbose multi-paragraph summary

**Root Cause:**
- Model capability limitation (not prompt failure)
- Question ambiguity without explicit temporal context
- Large data volume (3.4KB, 7 events) triggers summarization mode

**Workaround:** Users can ask more specific questions:
- "What's my next meeting?" (ambiguous scope)
- "What's my next meeting today?" (explicit scope)

**Potential Solutions:**
1. Switch to cloud LLM (Gemini) for better instruction following
2. Add post-processing: detect verbose responses (>200 words), retry with stronger constraint
3. Accept as-is (recommended) - core reasoning gap is fixed, real users adapt questions

### Future Tier Enhancements

- Auto-detect tier from model context window size (no manual assignment needed)
- Per-aspect token counting (actual tokenizer, not char estimate)
- Tier 0 (ultra-minimal) for sub-3B models: identity only
