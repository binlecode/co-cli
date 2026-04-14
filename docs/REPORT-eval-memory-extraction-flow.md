# Eval Report: Memory Extraction Flow

## Run: 2026-04-14 22:48:17 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Extraction timeout:** 15s per background extraction drain  
**Total runtime:** 23984ms  
**Result:** 3/3 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `background-round-trip` | PASS | 5875ms |
| `cadence-gate` | PASS | 0ms |
| `e2e-extraction-injection` | PASS | 18107ms |

### Step Traces

#### `background-round-trip` — PASS
- **fire_and_forget_extraction launch** (0ms): launch_ms=0.0 (non-blocking)
- **drain_pending_extraction** (5851ms): cursor=2
- **write + DB index state** (0ms): files_written=1 db_results=1 cursor=2

#### `cadence-gate` — PASS
- **config: extract_every_n_turns** (0ms): n=2 (expected 2)
- **turn 1 gate check** (0ms): counter=1 fires=False (expected False)
- **turn 2 gate check** (0ms): counter=2 fires=True (expected True)
- **disabled gate (n=0)** (0ms): n=0 gate_disabled=True

#### `e2e-extraction-injection` — PASS
- **run_turn 1 (state preference)** (11418ms): 2 messages
- **fire_and_forget_extraction (non-blocking)** (0ms): cursor_start=0
- **drain_pending_extraction** (6454ms): cursor=2 files_written=1
- **read extracted file** (0ms): body='User prefers pytest for all testing and does not want trailing comments in code.'
- **KnowledgeStore.search (body word probe)** (0ms): tried=['prefers', 'pytest', 'testing', 'trailing'] hit='prefers' db_results=1
- **inject_opening_context (direct probe)** (0ms): returned 2 messages
- **SystemPromptPart injection check** (0ms): injected=True preview="Relevant memories:\nFound 1 memory matching 'prefers':\n\n**user-prefers-pytest-for"


---

## Run: 2026-04-14 22:47:20 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Extraction timeout:** 15s per background extraction drain  
**Total runtime:** 37002ms  
**Result:** 2/3 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `background-round-trip` | PASS | 6205ms |
| `cadence-gate` | PASS | 0ms |
| `e2e-extraction-injection` | FAIL | 30795ms |

### Step Traces

#### `background-round-trip` — PASS
- **fire_and_forget_extraction launch** (0ms): launch_ms=0.0 (non-blocking)
- **drain_pending_extraction** (6180ms): cursor=2
- **write + DB index state** (0ms): files_written=1 db_results=1 cursor=2

#### `cadence-gate` — PASS
- **config: extract_every_n_turns** (0ms): n=2 (expected 2)
- **turn 1 gate check** (0ms): counter=1 fires=False (expected False)
- **turn 2 gate check** (0ms): counter=2 fires=True (expected True)
- **disabled gate (n=0)** (0ms): n=0 gate_disabled=True

#### `e2e-extraction-injection` — FAIL
- **run_turn 1 (state preference)** (23769ms): 14 messages
- **fire_and_forget_extraction (non-blocking)** (0ms): cursor_start=0
- **drain_pending_extraction** (6755ms): cursor=14 files_written=1
- **read extracted file** (0ms): body='User prefers pytest for all testing and does not want trailing comments in code.'
- **KnowledgeStore.search (body word probe)** (0ms): tried=['prefers', 'pytest', 'testing', 'trailing'] hit='prefers' db_results=1
- **inject_opening_context (direct probe)** (0ms): returned 1 messages
- **SystemPromptPart injection check** (0ms): injected=False preview=None
- **Failure:** inject_opening_context returned no SystemPromptPart — recall path broken

---

## Run: 2026-04-14 22:36:04 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Extraction timeout:** 15s per background extraction drain  
**Total runtime:** 24609ms  
**Result:** 2/3 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `background-round-trip` | PASS | 6507ms |
| `cadence-gate` | PASS | 0ms |
| `e2e-extraction-injection` | FAIL | 18100ms |

### Step Traces

#### `background-round-trip` — PASS
- **fire_and_forget_extraction launch** (0ms): launch_ms=0.0 (non-blocking)
- **drain_pending_extraction** (6485ms): cursor=2
- **write + DB index state** (0ms): files_written=1 db_results=1 cursor=2

#### `cadence-gate` — PASS
- **config: extract_every_n_turns** (0ms): n=2 (expected 2)
- **turn 1 gate check** (0ms): counter=1 fires=False (expected False)
- **turn 2 gate check** (0ms): counter=2 fires=True (expected True)
- **disabled gate (n=0)** (0ms): n=0 gate_disabled=True

#### `e2e-extraction-injection` — FAIL
- **run_turn 1 (state preference)** (11397ms): 2 messages
- **fire_and_forget_extraction (non-blocking)** (0ms): cursor_start=0
- **drain_pending_extraction** (6414ms): cursor=2 files_written=1
- **read extracted file** (0ms): body='User prefers pytest for all testing and does not want trailing comments in code.'
- **KnowledgeStore.search (extracted body as query)** (0ms): db_results=0
- **inject_opening_context (direct probe)** (0ms): returned 1 messages
- **SystemPromptPart injection check** (0ms): injected=False preview=None
- **Failure:** extracted content not found in DB — index step failed

---

## Run: 2026-04-14 22:31:47 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Extraction timeout:** 15s per background extraction drain  
**Total runtime:** 145076ms  
**Result:** 2/3 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `background-round-trip` | PASS | 6641ms |
| `cadence-gate` | PASS | 0ms |
| `e2e-extraction-injection` | FAIL | 138432ms |

### Step Traces

#### `background-round-trip` — PASS
- **fire_and_forget_extraction launch** (0ms): launch_ms=0.0 (non-blocking)
- **drain_pending_extraction** (6617ms): cursor=2
- **write + DB index state** (0ms): files_written=1 db_results=1 cursor=2

#### `cadence-gate` — PASS
- **config: extract_every_n_turns** (0ms): n=2 (expected 2)
- **turn 1 gate check** (0ms): counter=1 fires=False (expected False)
- **turn 2 gate check** (0ms): counter=2 fires=True (expected True)
- **disabled gate (n=0)** (0ms): n=0 gate_disabled=True

#### `e2e-extraction-injection` — FAIL
- **run_turn 1 (state preference)** (11609ms): 2 messages
- **fire_and_forget_extraction** (0ms): cursor_start=0
- **drain_pending_extraction** (6499ms): cursor=2 files_written=1 db_results=1
- **run_turn 2 (trigger recall)** (120002ms): 12 messages
- **SystemPromptPart injection check** (0ms): injected=False content_match=False preview=None
- **Failure:** no SystemPromptPart injection on turn 2 — recall not firing
