# TODO: Eval Framework for Tool-Calling Quality

## 1. Goal

Build a statistical eval suite that measures tool-calling quality across all 16 tools.
The suite runs golden JSONL cases through the real agent, extracts tool calls from the
response, and scores them against expected values. A CI-friendly gate fails the run when
accuracy drops below an absolute threshold or degrades relative to a saved baseline.

## 2. Motivation

- **Model swaps**: Switching Gemini model versions or quantisation levels can silently
  degrade tool selection or argument extraction. The eval catches regressions before they
  ship.
- **Prompt changes**: System prompt edits affect which tool the model picks. A scored
  suite makes the impact visible.
- **Tool surface changes**: Adding/renaming tools or changing signatures needs a
  regression check against existing prompts.

## 3. Eval Dimensions

Three orthogonal dimensions, each with its own case pool:

| Dimension | Description | Target cases |
|-----------|-------------|-------------|
| `tool_selection` | Model picks the correct tool for a user prompt | ~14 |
| `arg_extraction` | Model extracts the right arguments from the prompt | ~8 |
| `refusal` | Model does NOT call a tool when the prompt is out of scope | ~5 |

**Total: ~26 golden cases** covering all currently-enabled tools.
Slack tools are excluded from eval — backend not yet enabled. Add cases when it ships.

## 4. JSONL Case Format

Each line in `evals/tool_calling.jsonl`:

```jsonc
{
  "id": "ts-shell-01",
  "dim": "tool_selection",         // tool_selection | arg_extraction | refusal
  "prompt": "list files in /tmp",
  "expect_tool": "run_shell_command",  // null for refusal cases
  "expect_args": {"cmd": "ls /tmp"},   // null when not checked
  "arg_match": "subset"               // "exact" | "subset" | null
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Unique case ID (prefix: `ts-` tool_selection, `ae-` arg_extraction, `rf-` refusal) |
| `dim` | `str` | Eval dimension |
| `prompt` | `str` | User message sent to the agent |
| `expect_tool` | `str \| null` | Expected tool name (`null` = no tool call expected) |
| `expect_args` | `dict \| null` | Expected arguments (`null` = don't check args) |
| `arg_match` | `str \| null` | `"exact"` (full match), `"subset"` (expected keys present with matching values), or `null` (skip arg check) |

## 5. Tool Call Extraction

The eval must handle two code paths depending on tool type:

### Read-only tools (13 tools)
Use `agent.run()` directly. Extract `ToolCallPart` from the response messages:

```python
from pydantic_ai.messages import ToolCallPart

for msg in result.all_messages():
    if hasattr(msg, "parts"):
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                tool_name = part.tool_name
                args = part.args_as_dict()
```

### Approval tools (3 tools: `run_shell_command`, `create_email_draft`, `send_slack_message`)
The agent returns `DeferredToolRequests` instead of executing. Extract from the
deferred output:

```python
from pydantic_ai import DeferredToolRequests

result = await agent.run(prompt, deps=deps, usage_limits=limits)
if isinstance(result.output, DeferredToolRequests):
    for call in result.output.approvals:
        tool_name = call.tool_name
        args = call.args
        if isinstance(args, str):
            args = json.loads(args)
```

### Refusal cases
Expect no `ToolCallPart` in the response. Pass if the response contains only text
(no tool calls at all).

## 6. Deps Setup

Create a `CoDeps` with no real credentials — tools will fail at execution but the
model still selects and parameterises them correctly:

```python
from co_cli.deps import CoDeps
from co_cli.sandbox import SubprocessBackend

eval_deps = CoDeps(
    sandbox=SubprocessBackend(),
    obsidian_vault_path=None,
    google_credentials_path=None,
    slack_client=None,
    shell_safe_commands=[],
)
```

**How missing credentials are handled per tool type:**

- **Approval tools** (3): The agent returns `DeferredToolRequests` *before*
  execution, so missing credentials are irrelevant. The eval extracts tool name
  and args from `result.output.approvals`.
- **Read-only tools** (13): The tool executes, hits missing credentials, and
  raises `ModelRetry`. The eval does **not** need tool execution to succeed —
  it inspects `ToolCallPart` from the response messages *before* the retry
  loop corrupts the selection. The eval runner should set
  `UsageLimits(request_limit=2)` to prevent the agent from retrying with a
  different tool after the first `ModelRetry`.
- **Failure classification**: A run where the correct tool was selected but
  execution failed due to missing credentials counts as a **pass** for
  `tool_selection` and `arg_extraction`. Only the tool name and args matter.

## 7. Scoring

### Per-case scoring
Each case is run **3 times** (configurable via `--runs`). A case **passes** if a
**majority** of runs (>=2 of 3) produce the correct result.

### Pass criteria by dimension

| Dimension | Pass condition |
|-----------|---------------|
| `tool_selection` | `ToolCallPart.tool_name == expect_tool` |
| `arg_extraction` | Tool name matches AND args match per `arg_match` mode |
| `refusal` | No `ToolCallPart` in response |

### Arg matching modes

- **`exact`**: `actual_args == expect_args` (full dict equality)
- **`subset`**: Every key in `expect_args` exists in `actual_args` with the same value
  (extra keys are ignored)

### LLM determinism and transient errors

- **Temperature**: The eval runner should use `temperature=0` (via
  `ModelSettings`) to maximise determinism. This does not guarantee identical
  outputs across runs but reduces variance.
- **Transient errors**: Network timeouts, rate-limit 429s, and credential
  failures are not scoring failures. The eval runner should catch these,
  log a warning, and exclude the affected run from the majority vote
  (e.g. if 1 of 3 runs hits a transient error, pass/fail is decided by the
  remaining 2). If all runs for a case hit transient errors, mark the case
  as `ERROR` (not `FAIL`) and exclude it from gate calculations.
- **Request cap**: Each individual run uses
  `UsageLimits(request_limit=5)` to prevent runaway retries.

### Gates

- **Absolute gate**: Overall accuracy >= 80% (configurable via `--threshold`).
  Fail the run if below.
- **Relative gate** (when `--compare baseline.json` is provided): Per-dimension
  accuracy must not drop more than 10 percentage points (configurable via
  `--max-degradation`) versus the baseline.

## 8. CLI Interface

```
uv run python scripts/eval_tool_calling.py [OPTIONS]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--runs N` | 3 | Runs per case (odd number recommended for majority vote) |
| `--threshold F` | 0.80 | Absolute pass rate gate (0.0–1.0) |
| `--dim DIM` | all | Filter to a single dimension |
| `--case-id ID` | all | Run a single case by ID |
| `--save PATH` | — | Save results JSON to file (for later `--compare`) |
| `--compare PATH` | — | Compare against a saved baseline JSON |
| `--max-degradation F` | 0.10 | Max allowed per-dimension accuracy drop vs baseline |

**Exit codes:**
- `0` — all gates passed
- `1` — absolute gate failed
- `2` — relative gate failed (degradation detected)

## 9. Report Format

### Per-case table

```
CASE               DIM              TOOL EXPECTED       RESULT   RUNS
ts-shell-01        tool_selection   run_shell_command    PASS     3/3
ts-drive-01        tool_selection   search_drive_files   PASS     2/3
ae-email-01        arg_extraction   create_email_draft   FAIL     1/3
rf-chitchat-01     refusal          (none)               PASS     3/3
```

### Per-dimension summary

```
DIMENSION          CASES  PASSED  ACCURACY
tool_selection     12     11      91.7%
arg_extraction     8      6       75.0%
refusal            5      5       100.0%
─────────────────────────────────────────
OVERALL            25     22      88.0%
```

### Gate verdict

```
Absolute gate:  PASS (88.0% >= 80.0%)
Relative gate:  FAIL (arg_extraction dropped 15.0pp > 10.0pp max)
```

## 10. Golden Cases

Starter set (~25 cases). Expand as tools are added.

### tool_selection (~14 cases)

| ID | Prompt (sketch) | expect_tool |
|----|-----------------|-------------|
| ts-shell-01 | "list files in /tmp" | run_shell_command |
| ts-shell-02 | "what's my Python version?" | run_shell_command |
| ts-notes-01 | "find my notes about project X" | search_notes |
| ts-notes-02 | "show me the note titled meeting-2024" | read_note |
| ts-notes-03 | "what notes do I have?" | list_notes |
| ts-drive-01 | "find the Q4 budget spreadsheet" | search_drive_files |
| ts-drive-02 | "read the contents of design-doc.md from Drive" | read_drive_file |
| ts-email-01 | "show my recent emails" | list_emails |
| ts-email-02 | "draft an email to alice@example.com about the meeting" | create_email_draft |
| ts-email-03 | "find emails from dave about the invoice" | search_emails |
| ts-cal-01 | "what meetings do I have this week?" | list_calendar_events |
| ts-cal-02 | "when is my next dentist appointment?" | search_calendar_events |
| ts-slack-01 | "list the Slack channels" | list_slack_channels |
| ts-slack-02 | "send 'hello' to #general on Slack" | send_slack_message |

### arg_extraction (~8 cases)

| ID | Prompt (sketch) | expect_tool | expect_args | arg_match |
|----|-----------------|-------------|-------------|-----------|
| ae-shell-01 | "run 'echo hello world'" | run_shell_command | `{"cmd": "echo hello world"}` | exact |
| ae-shell-02 | "count lines in /etc/hosts" | run_shell_command | `{"cmd": "wc -l /etc/hosts"}` | subset |
| ae-email-01 | "draft email to bob@co.dev, subject 'Update', body 'See attached'" | create_email_draft | `{"to": "bob@co.dev", "subject": "Update"}` | subset |
| ae-drive-01 | "search Drive for 'quarterly report'" | search_drive_files | `{"query": "quarterly report"}` | subset |
| ae-notes-01 | "search notes for 'architecture decisions'" | search_notes | `{"query": "architecture decisions"}` | subset |
| ae-cal-01 | "search calendar for dentist appointments" | search_calendar_events | `{"query": "dentist"}` | subset |
| ae-slack-01 | "send 'deploy complete' to #ops" | send_slack_message | `{"channel": "#ops", "text": "deploy complete"}` | subset |
| ae-slack-02 | "show messages in #general" | list_slack_messages | `{"channel": "#general"}` | subset |

### refusal (~5 cases)

| ID | Prompt (sketch) | expect_tool |
|----|-----------------|-------------|
| rf-chitchat-01 | "what's the weather like?" | null |
| rf-chitchat-02 | "tell me a joke" | null |
| rf-opinion-01 | "what do you think about Python vs Go?" | null |
| rf-math-01 | "what's 42 * 17?" | null |
| rf-meta-01 | "what tools do you have?" | null |

## 11. File Checklist

| File | Status | Description |
|------|--------|-------------|
| `evals/tool_calling.jsonl` | Done | Golden cases (JSONL) |
| `scripts/eval_tool_calling.py` | Done | Eval runner script |
| `docs/TODO-eval-tool-calling.md` | Done | Design doc |
| `CLAUDE.md` | Done | Add entry to TODO inventory |

## 12. Key Patterns to Reuse

| Pattern | Source | Usage in eval |
|---------|--------|---------------|
| `get_agent()` | `co_cli/agent.py` | Create agent with all 16 tools registered |
| `CoDeps` | `co_cli/deps.py` | Dependency injection — construct with no real credentials |
| `SubprocessBackend` | `co_cli/sandbox.py` | Lightweight sandbox (no Docker needed for eval) |
| `DeferredToolRequests` | `pydantic_ai` | Detect approval-tool calls without executing them |
| `ToolCallPart` | `pydantic_ai.messages` | Extract tool name + args from read-only tool calls |
| `args_as_dict()` | `ToolCallPart` method | Normalise args (`str \| dict \| None` → `dict`) |
| `UsageLimits` | `pydantic_ai.usage` | Cap request count per eval case (e.g. `request_limit=5`) |
