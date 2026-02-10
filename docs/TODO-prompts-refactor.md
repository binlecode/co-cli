# TODO: Prompt Architecture Refactoring

## Known Issues

### Model Inconsistency on Complex Inquiries

**Status:** Known limitation
**Severity:** Low (workaround available)

**Issue:** Ollama glm-4.7-flash:q8_0 doesn't consistently follow the "1-2 sentences" constraint for complex analytical questions.

**Evidence:**
- ✓ Simple inquiry ("When is lunch today?") → Concise: "No lunch event scheduled today"
- ✗ Complex inquiry ("What's my next meeting?") → Verbose multi-paragraph summary

**Root Cause:**
- Model capability limitation (not prompt failure)
- Question ambiguity without explicit temporal context
- Large data volume (3.4KB, 7 events) triggers summarization mode

**Workaround:** Users can ask more specific questions:
- ❌ "What's my next meeting?" (ambiguous scope)
- ✅ "What's my next meeting today?" (explicit scope)

**Potential Solutions:**
1. Switch to cloud LLM (Gemini) for better instruction following
2. Add post-processing: detect verbose responses (>200 words), retry with stronger constraint
3. Accept as-is (recommended) - core reasoning gap is fixed, real users adapt questions

**Test Script:** `test_reasoning_gap.py` (reproduces issue consistently)
