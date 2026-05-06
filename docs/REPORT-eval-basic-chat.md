# Eval Report: Basic Chat

## Run: 2026-05-03 02:58:46 UTC

**Model:** ollama / qwen3.5:35b-a3b-agentic  
**Total runtime:** 25695ms  
**Result:** 3/3 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `factual_question` | PASS | 13739ms |
| `multi_turn_context` | PASS | 7967ms |
| `instruction_following` | PASS | 3989ms |

### Step Traces

#### `factual_question` — PASS
- **run_turn** (13736ms): outcome=continue approval_calls=0
- **response_analysis** (0ms): tool_calls=False paris_in_response=True preview='Paris'

#### `multi_turn_context` — PASS
- **turn1** (3624ms): outcome=continue
- **turn2** (4337ms): outcome=continue
- **response_analysis** (0ms): secret_in_response=True preview='Got it. Stored. EVALTOKEN12986'

#### `instruction_following` — PASS
- **run_turn** (3984ms): outcome=continue
- **response_analysis** (0ms): bullet_count=3 preview='- Red\n- Blue\n- Yellow'

---
