# Eval Report: Memory Extraction Flow

## Run: 2026-04-14 18:00:03 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Extraction timeout:** 15s per background extraction drain  
**Total runtime:** 7370ms  
**Result:** 2/2 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `background-round-trip` | PASS | 7369ms |
| `cadence-gate` | PASS | 0ms |

### Step Traces

#### `background-round-trip` — PASS
- **fire_and_forget_extraction launch** (0ms): launch_ms=0.0 (non-blocking)
- **drain_pending_extraction** (7343ms): cursor=2
- **write + DB index state** (0ms): files_written=1 db_results=1 cursor=2

#### `cadence-gate` — PASS
- **config: extract_every_n_turns** (0ms): n=2 (expected 2)
- **turn 1 gate check** (0ms): counter=1 fires=False (expected False)
- **turn 2 gate check** (0ms): counter=2 fires=True (expected True)
- **disabled gate (n=0)** (0ms): n=0 gate_disabled=True

