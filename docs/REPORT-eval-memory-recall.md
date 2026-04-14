# Eval Report: Memory Recall

## Run: 2026-04-14 02:46:35 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 31875ms  
**Result:** 4/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | PASS | 11610ms |
| `recall-partial-kw` | PASS | 3620ms |
| `recall-no-match` | PASS | 3189ms |
| `recall-empty-store` | PASS | 13457ms |

### Step Traces

#### `recall-topic-match` — PASS
- **seed_memory** (0ms): 2 file(s) — 'user prefers light theme for all develop' ['preference']; 'Project uses PostgreSQL for the database' ['decision']
- **run_turn (inference)** (11579ms): prompt: 'light theme'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='light theme' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'light theme':

**Memory 1** (created 2026-04-11)
Tags: preference
user prefers light theme for all development environments
`

#### `recall-partial-kw` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode preferences acros' ['preference']
- **run_turn (inference)** (3545ms): prompt: 'dark mode preferences'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='dark mode' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'dark mode preferences':

**Memory 1** (created 2026-04-12)
Tags: preference
User prefers dark mode preferences across all applications
`

#### `recall-no-match` — PASS
- **seed_memory** (0ms): 1 file(s) — 'User prefers dark mode in all applicatio' ['preference']
- **run_turn (inference)** (3165ms): prompt: 'What is 2 + 2?'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

#### `recall-empty-store` — PASS
- **seed_memory** (0ms): 0 file(s) — empty store
- **run_turn (inference)** (13439ms): prompt: 'light theme'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

---

## Run: 2026-04-14 02:45:01 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 136098ms  
**Result:** 3/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | PASS | 14021ms |
| `recall-partial-kw` | PASS | 14895ms |
| `recall-no-match` | PASS | 13449ms |
| `recall-empty-store` | ERROR | 0ms |

### Step Traces

#### `recall-topic-match` — PASS
- **seed_memory** (0ms): 2 file(s) — 'user prefers light theme for all develop' ['preference']; 'Project uses PostgreSQL for the database' ['decision']
- **run_turn (inference)** (13992ms): prompt: 'light theme'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='light theme' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'light theme':

**Memory 1** (created 2026-04-11)
Tags: preference
user prefers light theme for all development environments
`

#### `recall-partial-kw` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode preferences acros' ['preference']
- **run_turn (inference)** (14816ms): prompt: 'dark mode preferences'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='dark mode' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'dark mode preferences':

**Memory 1** (created 2026-04-12)
Tags: preference
User prefers dark mode preferences across all applications
`

#### `recall-no-match` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode in all applicatio' ['preference']
- **run_turn (inference)** (13421ms): prompt: 'What is 2 + 2?'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

#### `recall-empty-store` — ERROR
- **Failure:** The next request would exceed the request_limit of 15

---

## Run: 2026-04-14 02:39:37 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 141911ms  
**Result:** 3/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | PASS | 18000ms |
| `recall-partial-kw` | PASS | 25456ms |
| `recall-no-match` | PASS | 19237ms |
| `recall-empty-store` | ERROR | 0ms |

### Step Traces

#### `recall-topic-match` — PASS
- **seed_memory** (0ms): 2 file(s) — 'user prefers light theme for all develop' ['preference']; 'Project uses PostgreSQL for the database' ['decision']
- **run_turn (inference)** (11613ms): prompt: 'light theme'
- **extraction drain** (6356ms): 2 file(s) in .co-cli/memory: 001-user-prefers-light-theme-for-all-develop.md, 002-project-uses-postgresql-for-the-database.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='light theme' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'light theme':

**Memory 1** (created 2026-04-11)
Tags: preference
user prefers light theme for all development environments
`

#### `recall-partial-kw` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode preferences acros' ['preference']
- **run_turn (inference)** (12876ms): prompt: 'dark mode preferences'
- **extraction drain** (12496ms): 2 file(s) in .co-cli/memory: 001-user-prefers-dark-mode-preferences-acros.md, dark-mode-preference-d89e6b11.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='dark mode' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'dark mode preferences':

**Memory 1** (created 2026-04-12)
Tags: preference
User prefers dark mode preferences across all applications
`

#### `recall-no-match` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode in all applicatio' ['preference']
- **run_turn (inference)** (14463ms): prompt: 'What is 2 + 2?'
- **extraction drain** (4746ms): 1 file(s) in .co-cli/memory: 001-user-prefers-dark-mode-in-all-applicatio.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

#### `recall-empty-store` — ERROR
- **Failure:** The next request would exceed the request_limit of 15

---

## Run: 2026-04-14 02:11:40 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 97444ms  
**Result:** 4/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | PASS | 16347ms |
| `recall-partial-kw` | PASS | 22608ms |
| `recall-no-match` | PASS | 17478ms |
| `recall-empty-store` | PASS | 41010ms |

### Step Traces

#### `recall-topic-match` — PASS
- **seed_memory** (1ms): 2 file(s) — 'user prefers light theme for all develop' ['preference']; 'Project uses PostgreSQL for the database' ['decision']
- **run_turn (inference)** (13043ms): prompt: 'light theme'
- **extraction drain** (3275ms): 2 file(s) in .co-cli/memory: 001-user-prefers-light-theme-for-all-develop.md, 002-project-uses-postgresql-for-the-database.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='light theme' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'light theme':

**Memory 1** (created 2026-04-11)
Tags: preference
user prefers light theme for all development environments
`

#### `recall-partial-kw` — PASS
- **seed_memory** (0ms): 1 file(s) — 'User prefers dark mode preferences acros' ['preference']
- **run_turn (inference)** (14058ms): prompt: 'dark mode preferences'
- **extraction drain** (8478ms): 2 file(s) in .co-cli/memory: 001-user-prefers-dark-mode-preferences-acros.md, dark-mode-preference-2cb70666.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='dark mode' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'dark mode preferences':

**Memory 1** (created 2026-04-12)
Tags: preference
User prefers dark mode preferences across all applications
`

#### `recall-no-match` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode in all applicatio' ['preference']
- **run_turn (inference)** (14114ms): prompt: 'What is 2 + 2?'
- **extraction drain** (3332ms): 1 file(s) in .co-cli/memory: 001-user-prefers-dark-mode-in-all-applicatio.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

#### `recall-empty-store` — PASS
- **seed_memory** (0ms): 0 file(s) — empty store
- **run_turn (inference)** (25716ms): prompt: 'light theme'
- **extraction drain** (15266ms): 3 file(s) in .co-cli/memory: user-prefers-a-light-theme-for-the-interface-wh-0afa9b6a.md, user-prefers-a-light-theme-for-the-interface-wh-557b732c.md, user-prefers-a-light-theme-for-the-interface-wh-b2c47c2c.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

---

## Run: 2026-04-14 00:15:50 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 44718ms  
**Result:** 4/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | PASS | 19448ms |
| `recall-partial-kw` | PASS | 3961ms |
| `recall-no-match` | PASS | 3962ms |
| `recall-empty-store` | PASS | 17347ms |

### Step Traces

#### `recall-topic-match` — PASS
- **seed_memory** (1ms): 2 file(s) — 'User prefers pytest for testing. Set up ' ['preference']; 'Project uses PostgreSQL for the database' ['decision']
- **run_turn (inference)** (19008ms): prompt: 'Set up testing for my Python project'
- **extraction drain** (0ms): 2 file(s) in .co-cli/memory: 001-user-prefers-pytest-for-testing.-set-up-.md, 002-project-uses-postgresql-for-the-database.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='pytest' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'Set up testing for my Python project':

**Memory 1** (created 2026-04-11)
Tags: preference
User prefers pytest for testing. Set up testing for my Python pro`

#### `recall-partial-kw` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode preferences acros' ['preference']
- **run_turn (inference)** (3938ms): prompt: 'dark mode preferences'
- **extraction drain** (0ms): 1 file(s) in .co-cli/memory: 001-user-prefers-dark-mode-preferences-acros.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='dark mode' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'dark mode preferences':

**Memory 1** (created 2026-04-12)
Tags: preference
User prefers dark mode preferences across all applications
`

#### `recall-no-match` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode in all applicatio' ['preference']
- **run_turn (inference)** (3938ms): prompt: 'What is 2 + 2?'
- **extraction drain** (0ms): 1 file(s) in .co-cli/memory: 001-user-prefers-dark-mode-in-all-applicatio.md
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

#### `recall-empty-store` — PASS
- **seed_memory** (0ms): 0 file(s) — empty store
- **run_turn (inference)** (17324ms): prompt: 'Set up testing for my Python project'
- **extraction drain** (0ms): 0 file(s) in .co-cli/memory: 
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

---

## Run: 2026-04-14 00:12:41 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 161568ms  
**Result:** 3/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | PASS | 19375ms |
| `recall-partial-kw` | FAIL | 120023ms |
| `recall-no-match` | PASS | 3960ms |
| `recall-empty-store` | PASS | 18211ms |

### Step Traces

#### `recall-topic-match` — PASS
- **seed_memory** (1ms): 2 file(s) — 'User prefers pytest for testing. Set up ' ['preference']; 'Project uses PostgreSQL for the database' ['decision']
- **run_turn (inference)** (18917ms): prompt: 'Set up testing for my Python project'
- **extraction drain** (0ms): fire_and_forget_extraction + drain (mirrors consolidate_turn_result)
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='pytest' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'Set up testing for my Python project':

**Memory 1** (created 2026-04-11)
Tags: preference
User prefers pytest for testing. Set up testing for my Python pro`

#### `recall-partial-kw` — FAIL
- **seed_memory** (0ms): 1 file(s) — 'User prefers vim keybindings in all edit' ['preference']
- **run_turn (inference)** (120001ms): prompt: 'vim keybindings'
- **extraction drain** (1ms): fire_and_forget_extraction + drain (mirrors consolidate_turn_result)
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False keyword='vim' found=False
- **Failure:** no injection
- **System prompt received:** `(none)`

#### `recall-no-match` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode in all applicatio' ['preference']
- **run_turn (inference)** (3934ms): prompt: 'What is 2 + 2?'
- **extraction drain** (0ms): fire_and_forget_extraction + drain (mirrors consolidate_turn_result)
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

#### `recall-empty-store` — PASS
- **seed_memory** (0ms): 0 file(s) — empty store
- **run_turn (inference)** (18188ms): prompt: 'Set up testing for my Python project'
- **extraction drain** (0ms): fire_and_forget_extraction + drain (mirrors consolidate_turn_result)
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

---

## Run: 2026-04-13 23:19:53 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 50542ms  
**Result:** 4/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | PASS | 18775ms |
| `recall-partial-kw` | PASS | 10881ms |
| `recall-no-match` | PASS | 3561ms |
| `recall-empty-store` | PASS | 17325ms |

### Step Traces

#### `recall-topic-match` — PASS
- **seed_memory** (0ms): 2 file(s) — 'User prefers pytest for testing. Set up ' ['preference']; 'Project uses PostgreSQL for the database' ['decision']
- **run_turn** (18341ms): prompt: 'Set up testing for my Python project'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='pytest' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'Set up testing for my Python project':

**Memory 1** (created 2026-04-10)
Tags: preference
User prefers pytest for testing. Set up testing for my Python pro`

#### `recall-partial-kw` — PASS
- **seed_memory** (0ms): 1 file(s) — 'User prefers vim keybindings in all edit' ['preference']
- **run_turn** (10862ms): prompt: 'vim keybindings'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='vim' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'vim keybindings':

**Memory 1** (created 2026-04-11)
Tags: preference
User prefers vim keybindings in all editors
`

#### `recall-no-match` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode in all applicatio' ['preference']
- **run_turn** (3537ms): prompt: 'What is 2 + 2?'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

#### `recall-empty-store` — PASS
- **seed_memory** (0ms): 0 file(s) — empty store
- **run_turn** (17307ms): prompt: 'Set up testing for my Python project'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

---

## Run: 2026-04-13 23:17:50 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 163557ms  
**Result:** 3/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | PASS | 18813ms |
| `recall-partial-kw` | FAIL | 120021ms |
| `recall-no-match` | PASS | 4551ms |
| `recall-empty-store` | PASS | 20171ms |

### Step Traces

#### `recall-topic-match` — PASS
- **seed_memory** (1ms): 2 file(s) — 'User prefers pytest for testing. Set up ' ['preference']; 'Project uses PostgreSQL for the database' ['decision']
- **run_turn** (18372ms): prompt: 'Set up testing for my Python project'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=True keyword='pytest' found=True
- **System prompt received:** `Relevant memories:
Found 1 memory matching 'Set up testing for my Python project':

**Memory 1** (created 2026-04-10)
Tags: preference
User prefers pytest for testing. Set up testing for my Python pro`

#### `recall-partial-kw` — FAIL
- **seed_memory** (0ms): 1 file(s) — 'User prefers vim keybindings. Configure ' ['preference']
- **run_turn** (120001ms): prompt: 'Configure my editor settings'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False keyword='vim' found=False
- **Failure:** no injection
- **System prompt received:** `(none)`

#### `recall-no-match` — PASS
- **seed_memory** (1ms): 1 file(s) — 'User prefers dark mode in all applicatio' ['preference']
- **run_turn** (4525ms): prompt: 'What is 2 + 2?'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

#### `recall-empty-store` — PASS
- **seed_memory** (0ms): 0 file(s) — empty store
- **run_turn** (20148ms): prompt: 'Set up testing for my Python project'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

---

## Run: 2026-04-13 23:14:29 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total runtime:** 9676ms  
**Result:** 1/4 passed

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `recall-topic-match` | ERROR | 0ms |
| `recall-partial-kw` | ERROR | 0ms |
| `recall-no-match` | ERROR | 0ms |
| `recall-empty-store` | PASS | 9675ms |

### Step Traces

#### `recall-topic-match` — ERROR
- **Failure:** [Errno 2] No such file or directory: '/var/folders/_t/pq20t72n3kl4ygs90tm4pz2m0000gn/T/tmpc5aqqtpo/.co-cli/memory/001-user-prefers-pytest-for-testing.md'

#### `recall-partial-kw` — ERROR
- **Failure:** [Errno 2] No such file or directory: '/var/folders/_t/pq20t72n3kl4ygs90tm4pz2m0000gn/T/tmpd1by87pz/.co-cli/memory/001-user-prefers-vim-keybindings-in-all-edit.md'

#### `recall-no-match` — ERROR
- **Failure:** [Errno 2] No such file or directory: '/var/folders/_t/pq20t72n3kl4ygs90tm4pz2m0000gn/T/tmpt24mgqo_/.co-cli/memory/001-user-prefers-dark-mode-in-all-applicatio.md'

#### `recall-empty-store` — PASS
- **seed_memory** (0ms): 0 file(s) — empty store
- **run_turn** (9236ms): prompt: 'Set up testing for my Python project'
- **scan SystemPromptPart for 'Relevant memories:'** (0ms): injected=False no keyword expected
- **System prompt received:** `(none)`

---
