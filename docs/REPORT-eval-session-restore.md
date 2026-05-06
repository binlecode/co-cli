# Eval Report: Session Restore

## Run: 2026-05-03 03:21:58 UTC

**Model:** ollama / qwen3.5:35b-a3b-agentic  
**Total runtime:** 21368ms  
**Result:** 3/3 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `prior_context_available` | PASS | 13520ms |
| `no_hallucination_from_absent_session` | PASS | 4083ms |
| `multi_session_most_recent_wins` | PASS | 3764ms |

### Step Traces

#### `prior_context_available` — PASS
- **seed_session** (0ms): token=SESTOKEN95055A
- **restore_session** (0ms): path=2026-01-01-T120000Z-sestoken.jsonl
- **load_transcript** (0ms): messages=2
- **run_turn** (13509ms): outcome=continue
- **response_analysis** (0ms): token_in_response=True preview='Understood, I have noted your token: SESTOKEN95055A SESTOKEN95055A'

#### `no_hallucination_from_absent_session` — PASS
- **load_transcript** (0ms): messages=0 (expected 0)
- **run_turn** (4077ms): outcome=continue
- **response_analysis** (0ms): absent_token_hallucinated=False preview="I can't see it. I have no visibility into prior conversation history or tokens from before this session."

#### `multi_session_most_recent_wins` — PASS
- **seed_sessions** (0ms): older=OLDTOKEN2659X newer=NEWTOKEN2659Y
- **restore_session** (0ms): path=2026-01-02-T100000Z-newtoken.jsonl
- **load_transcript** (0ms): messages=2
- **run_turn** (3756ms): outcome=continue
- **response_analysis** (0ms): new_in_response=True old_in_response=False preview='Understood, I have noted your token: NEWTOKEN2659Y The token you mentioned is: NEWTOKEN2659Y'

---
