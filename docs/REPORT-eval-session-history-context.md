# Eval Report: Session History Context

## Run: 2026-04-14 22:02:09 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 10709ms  
**Result:** 4/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `persist-load-roundtrip` | PASS | 7ms |
| `incremental-append` | PASS | 0ms |
| `empty-history-new-session` | PASS | 0ms |
| `restored-history-in-context` | PASS | 10700ms |

### Step Traces

#### `persist-load-roundtrip` — PASS
- **persist_session_history** (6ms): wrote 4 messages to 2026-04-14-T220158Z-036dbb34.jsonl
- **load_transcript** (0ms): loaded=4 expected=4
- **content integrity check** (0ms): first_user_msg='My preferred language is zyxwquartz-session-history-eval-uni' sentinel_present=True

#### `incremental-append` — PASS
- **persist turn 1 (2 messages)** (0ms): persisted_message_count=0 → appends all 2
- **persist turn 2 (4 messages, count=2)** (0ms): persisted_message_count=2 → appends only 2 new
- **load_transcript** (0ms): loaded=4 expected=4
- **duplicate check** (0ms): user_messages=['First turn message.', 'Second turn with zyxwquartz-session-history-eval-unique-append-marker.'] has_duplicate=False has_sentinel=True

#### `empty-history-new-session` — PASS
- **persist_session_history (empty messages)** (0ms): file_exists=False
- **load_transcript** (0ms): loaded=0 (expected 0)

#### `restored-history-in-context` — PASS
- **persist prior history** (0ms): wrote 2 messages
- **load_transcript** (0ms): loaded=2 messages
- **run_turn (with restored history)** (10101ms): asked about prior codename
- **response analysis** (0ms): sentinel_in_response=True preview='Got it, your project codename is zyxwquartz-session-history-eval-unique-codename.zyxwquartz-session-history-eval-unique-'

---
