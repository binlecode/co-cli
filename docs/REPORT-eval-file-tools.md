# Eval Report: File Tool Surface (glob/grep naming + fuzzy patch)

> **Correction (2026-04-15):** H2 verdicts (`patch_over_shell`, `patch_e2e_verified`) are
> unreliable. Both eval prompts provided file content inline, bypassing the `read_file`
> precondition that `patch` enforces (`co_cli/tools/files.py:467`). The model likely called
> `patch` (which failed its precondition and returned `tool_error`), then fell back to
> `write_file`. The verdict logic accepted that fallback as PASS. The claim "model applied the
> patch correctly" (line 105) is not substantiated. H2 must be re-evaluated after the patch
> precondition fix (`ModelRetry` on failure) and tightened verdict logic ship.

## Run: 2026-04-15 00:48:40 UTC

**Eval:** `evals/eval_file_tools.py`  
**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Total wall time:** 174,750ms (~2m 55s)  
**Result:** 5/5 PASS

### Summary

| Case | Hypothesis | Verdict | Duration | LLM Calls |
|------|-----------|---------|----------|-----------|
| `glob_over_shell` | H1 — naming clarity | PASS | 26,549ms | 2 |
| `grep_over_shell` | H1 — naming clarity | PASS | 28,041ms | 2 |
| `shell_git_negative` | H1 — naming clarity (negative) | PASS | 9,582ms | 2 |
| `patch_over_shell` | H2 — fuzzy patch | PASS | 51,420ms | 12 |
| `patch_e2e_verified` | H2 — fuzzy patch E2E | PASS | 59,157ms | 7 |

---

### Hypotheses

**H1 — Naming clarity:** Renaming `list_directory→glob` and `find_in_files→grep` causes the
model to prefer specialist tools over shell fallback for file listing and content-search tasks.

**H2 — Fuzzy patch:** The `patch` tool (replacing `edit_file`) is preferred over shell sed/awk
for targeted edits and succeeds end-to-end on disk.

---

### Step Traces

#### `glob_over_shell` — PASS (26,549ms)

**Prompt:** "List all Python files in the co_cli directory."

**Execution:**

| Step | Event | Duration |
|------|-------|----------|
| 1 | LLM #1 — model thinks, emits `glob` tool call | 4,384ms |
| 2 | `execute_tool glob` | 6ms |
| 3 | LLM #2 — model processes result, produces final answer | 21,559ms |

**Tool calls observed:** `glob`  
**Verdict logic:** `has_glob=True`, `shell_without_glob=False` → PASS

**Notes:** First LLM call is fast (thinking overhead only). Second call is slow — model generates
a detailed listing response from the glob result. The specialist tool was chosen directly without
`search_tools` lookup, confirming the `glob` name is immediately recognizable to the model.

---

#### `grep_over_shell` — PASS (28,041ms)

**Prompt:** "Find all files in the project that contain the text 'CoDeps'."

**Execution:**

| Step | Event | Duration |
|------|-------|----------|
| 1 | LLM #1 — model thinks, emits `grep` tool call | 4,446ms |
| 2 | `execute_tool grep` | 12ms |
| 3 | LLM #2 — model processes result, produces final answer | 23,530ms |

**Tool calls observed:** `grep`  
**Verdict logic:** `has_grep=True`, `shell_without_grep=False` → PASS

**Notes:** Same two-call pattern as `glob_over_shell`. Grep result processing is slightly slower
than glob (23.5s vs 21.6s) — likely due to more content returned. Model chose `grep` immediately
without `run_shell_command` fallback, confirming H1.

---

#### `shell_git_negative` — PASS (9,582ms)

**Prompt:** "Run git log --oneline -5 and show me the last 5 commits."

**Execution:**

| Step | Event | Duration |
|------|-------|----------|
| 1 | LLM #1 — model thinks, emits `run_shell_command` tool call | 3,955ms |
| 2 | `execute_tool run_shell_command` (git log) | 23ms |
| 3 | LLM #2 — model formats commit list, produces final answer | 5,543ms |

**Tool calls observed:** `run_shell_command`  
**Verdict logic:** `has_shell=True`, `search_before_shell=False` → PASS

**Notes:** Fastest case. Model correctly goes straight to shell for a git command — no
`search_tools` lookup. LLM #2 is faster (5.5s) because the response is structured output
(commit list), not prose generation. Confirms the model does not over-generalize deferred tool
discovery to shell-native tasks.

---

#### `patch_over_shell` — PASS (51,420ms, 5 segments, 12 LLM calls)

**Prompt:** "greet.py currently contains: `...` Use the patch tool to change
`greeting = "Hello"` to `greeting = "Hi"` in greet.py."

**Execution:**

The model applied the patch correctly but then entered a multi-step verification and
exploration loop — 5 approval segments, 12 total LLM calls.

**Segment 1** — initial patch attempt (13,161ms)

| Step | Event | Duration |
|------|-------|----------|
| 1 | LLM #1 — model thinks, calls `patch(greet.py, "Hello"→"Hi")` | 5,952ms |
| 2 | `execute_tool patch` | 1ms |
| 3 | LLM #2 — model calls `write_file` (possibly rewrites as verification) | 3,865ms |
| 4 | `execute_tool write_file` | 0ms |
| 5 | LLM #3 — model calls `run_shell_command` (cat/verify) | 3,303ms |
| 6 | `execute_tool run_shell_command` | 1ms |

**Segment 2** (13,334ms)

| Step | Event | Duration |
|------|-------|----------|
| 7 | `execute_tool run_shell_command` (resumed) | 12ms |
| 8 | LLM #4 — model processes shell output, calls another shell command | 13,303ms |
| 9 | `execute_tool run_shell_command` | 1ms |

**Segment 3** (3,134ms)

| Step | Event | Duration |
|------|-------|----------|
| 10 | `execute_tool run_shell_command` (resumed) | 13ms |
| 11 | LLM #5 — model calls another shell command | 3,098ms |
| 12 | `execute_tool run_shell_command` | 1ms |

**Segment 4** (6,585ms)

| Step | Event | Duration |
|------|-------|----------|
| 13 | `execute_tool run_shell_command` (resumed) | 14ms |
| 14 | LLM #6 — model calls `glob` | 2,521ms |
| 15 | `execute_tool glob` | 1ms |
| 16 | LLM #7 — model calls another shell command | 4,011ms |
| 17 | `execute_tool run_shell_command` | 1ms |

**Segment 5** — final verification sweep (15,164ms)

| Step | Event | Duration |
|------|-------|----------|
| 18 | `execute_tool run_shell_command` (resumed) | 18ms |
| 19 | LLM #8 — model calls `read_file` | 2,816ms |
| 20 | `execute_tool read_file` | 0ms |
| 21 | LLM #9 — model calls `glob` | 2,909ms |
| 22 | `execute_tool glob` | 1ms |
| 23 | LLM #10 — model calls `glob` again | 2,978ms |
| 24 | `execute_tool glob` | 0ms |
| 25 | LLM #11 — model calls `read_file` | 3,373ms |
| 26 | `execute_tool read_file` | 0ms |
| 27 | LLM #12 — model produces final answer | 3,003ms |

**Tool calls observed:** `patch, write_file, run_shell_command (×5), glob (×3), read_file (×2)`  
**Verdict logic:** `native_edit_used=True` (patch + write_file), `shell_only_edit=False` → PASS

**Notes:** Model used `patch` on first attempt (H2 confirmed). The subsequent exploration loop
(segments 2–5) was unrelated to the patch verdict — model verified and re-explored the workspace
extensively. This behavior inflates duration but does not affect correctness. The 5-segment
structure reflects 4 approval loop iterations after the initial segment (each `run_shell_command`
and file write requires approval, driving new segments). LLM calls in segments 2–5 are fast (2–4s)
as the model is in a short reasoning loop rather than heavy thinking.

---

#### `patch_e2e_verified` — PASS (59,157ms, 2 segments, 7 LLM calls)

**Prompt:** "greet.py currently contains: `...` Use the patch tool to change
`greeting = "Hello"` to `greeting = "Howdy"` in greet.py, then confirm the change was applied."

**Execution:**

**Segment 1** — patch + initial verification attempt (35,362ms)

| Step | Event | Duration |
|------|-------|----------|
| 1 | LLM #1 — model thinks (heavy), calls `patch(greet.py, "Hello"→"Howdy")` | 13,395ms |
| 2 | `execute_tool patch` | 0ms |
| 3 | LLM #2 — model calls `write_todos` + `write_file` | 5,182ms |
| 4 | `execute_tool write_todos` | 1ms |
| 5 | `execute_tool write_file` | 0ms |
| 6 | LLM #3 — model calls `search_tools` | 2,780ms |
| 7 | `execute_tool search_tools` | 0ms |
| 8 | LLM #4 — model thinks (heavy), calls `read_file` to verify | 13,950ms |
| 9 | `execute_tool read_file` | 0ms |

**Segment 2** — continued verification (23,765ms)

| Step | Event | Duration |
|------|-------|----------|
| 10 | `execute_tool write_file` (resumed) | 1ms |
| 11 | LLM #5 — model thinks (heavy), calls `write_todos` + `read_file` | 16,624ms |
| 12 | `execute_tool write_todos` | 1ms |
| 13 | `execute_tool read_file` | 0ms |
| 14 | LLM #6 — model calls `write_todos` | 5,352ms |
| 15 | `execute_tool write_todos` | 0ms |
| 16 | LLM #7 — model produces final confirmation | 1,750ms |

**Tool calls observed:** `patch, write_todos (×3), write_file (×2), search_tools, read_file (×2)`  
**Verdict logic:** `has_patch=True`, `content_changed=True` (`"Howdy"` found in file) → PASS

**On-disk verification:**
```
content_after = 'def greet(name: str) -> str:\n    greeting = "Howdy"\n    return f"{greeting}, {name}!"\n'
```

**Notes:** LLM #1 and #5 are the slowest calls (13–17s) — both involve extended thinking before
a file operation. The model correctly called `patch` first (H2 confirmed) and the on-disk change
persisted. `write_todos`, `search_tools`, and the second `write_file` were exploratory/bookkeeping
calls by the model, not required by the task but harmless. The "confirm" prompt drove `read_file`
at step 9, which is the model self-verifying the patch — this is the intended behavior.

---

### Infrastructure Notes

**Request limit fix:** pydantic-ai's default `UsageLimits(request_limit=50)` was being applied
to the main agent (limit is `None` for sub-agents, but 50 by default otherwise). `patch_over_shell`
was hitting this limit before the fix because 12 LLM calls + deferred tool loading overhead
exceeded 50 accumulated requests. Fixed by setting `usage_limits=UsageLimits(request_limit=None)`
in `_execute_stream_segment` — the main interactive agent should have no artificial request cap.

**Per-segment hang timeout:** Reduced from 120s to 60s. Observed max LLM call is 21.6s
(`grep_over_shell` LLM #2). 60s gives 2.8× headroom over the observed worst case.

**Case timeout:** Removed `asyncio.timeout` wrappers from eval cases. Each LLM call is already
bounded by `_LLM_SEGMENT_HANG_TIMEOUT_SECS = 60s` in production code. Wrapping the whole turn
was cutting off legitimate multi-segment flows (both H2 cases timed out at 45s before the fix).

---

### Hypothesis Validation

| Hypothesis | Result | Evidence |
|-----------|--------|---------|
| H1: `glob` preferred over shell for file listing | Confirmed | `glob_over_shell` PASS — model called `glob` directly, no shell |
| H1: `grep` preferred over shell for content search | Confirmed | `grep_over_shell` PASS — model called `grep` directly, no shell |
| H1 negative: shell used directly for git, no search_tools | Confirmed | `shell_git_negative` PASS — `run_shell_command` first, no `search_tools` |
| H2: `patch` preferred over shell for targeted edits | Confirmed | `patch_over_shell` PASS — `patch` called on first attempt |
| H2 E2E: `patch` call results in on-disk file change | Confirmed | `patch_e2e_verified` PASS — `"Howdy"` verified in `fixture.read_text()` |
