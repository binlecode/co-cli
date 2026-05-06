# RESEARCH: Hermes Ollama tool-call stability — gaps in co-cli

Scope: catalogue every mechanism in `~/workspace_genai/hermes-agent/` that hardens
tool-call reliability when running small or quantized local models on Ollama
(Qwen3, GLM, Llama, DeepSeek), then map each pattern onto co-cli's current
surface and identify gaps.

Trigger: investigating `tests/test_flow_tool_calling_functional.py::test_tool_selection_shell_git_status`
which intermittently failed with `tool_name == None` under qwen3.6:27b-agentic.
Root cause was forcing noreason mode; secondary question was whether co-cli
should adopt other hermes hardening patterns.

Verification basis: every claim below was verified against hermes source via
direct grep + read. Patterns the upstream survey returned without verifiable
line numbers are listed in a separate "unverified" section.

## 1. Tier 1 — direct session-stability impact

These are the patterns where the failure mode is "session crashes" or "model
output is silently lost", not just "annoying".

### 1.1 `_repair_tool_call_arguments` — JSON argument recovery

- Hermes: `run_agent.py:591-647`, called at `:6807` and `:10668`
- What it does: applies six repair passes to malformed tool-call argument JSON:
  empty/whitespace → `{}`; Python `None` literal → `{}`; control-char escape
  via `json.loads(strict=False)` re-serialize; trailing-comma strip; balance
  unclosed braces/brackets (bounded to 50 iterations); trim excess closing
  delimiters.
- Failure mode: GLM-5.1 / quantized Qwen on Ollama emit truncated or
  trailing-comma JSON inside `tool_calls.arguments`. Without repair, the
  request fails with HTTP 400 "invalid tool call arguments" and the session
  dies.
- Co-cli status: missing. pydantic-ai validates tool args at the SDK layer
  with no repair fallback — malformed JSON raises and bubbles up as an
  unrecoverable error.

### 1.2 `query_ollama_num_ctx` — per-request num_ctx injection

- Hermes: `run_agent.py:139` (import), `:2044` (per-request inject)
- What it does: probes Ollama `/api/show` for the loaded Modelfile's
  `num_ctx` and injects it into every chat request body so the OpenAI-compat
  endpoint uses the model's full window instead of Ollama's 2048-token
  default.
- Failure mode: without injection, Ollama serves the request at 2048 tokens
  even when the Modelfile declares a larger window — tool schemas get
  truncated out of the system prompt and the model hallucinates.
- Co-cli status: partial. `co_cli/bootstrap/check.py:68` probes `/api/show`
  once at bootstrap and uses the value as a compaction ceiling
  (`co_cli/bootstrap/core.py:243-262`). The probed value is **never injected
  into the request body**. We get away with it today because qwen3.6's
  Modelfile already declares `num_ctx=65536` and Ollama honors that as the
  Modelfile-level default; any model whose Modelfile doesn't declare an
  explicit num_ctx would silently truncate.

### 1.3 `_stream_stale_timeout = float("inf")` for local + 30s heartbeats

- Hermes: `run_agent.py:7165-7167` (timeout disable), `:7184-7203`
  (heartbeat loop)
- What it does: detects `is_local_endpoint(base_url)` and disables the 180s
  stale-stream timeout; pings `_touch_activity()` every 30s to keep the
  gateway alive during long prefill.
- Failure mode: small local models on a 32K+ context can take 300+ seconds to
  emit the first token. Default HTTP read timeouts kill the connection mid
  prefill.
- Co-cli status: missing, but **not directly applicable**. Co-cli's HTTP
  read timeout is 300s (`co_cli/llm/factory.py:16`) and pydantic-ai owns the
  streaming path — there is no place to wedge in a heartbeat. The 300s
  ceiling is enough headroom for our current workload (qwen3.6 prefill
  observed at 25-50s).

### 1.4 `_sanitize_surrogates` — replace lone surrogate code points

- Hermes: `run_agent.py:433` (definition), called at `:8454, :8456, :8482,
  :10097, :10099` and one more site
- What it does: walks every string field in the outgoing message dict
  (content, name, tool_calls.arguments, reasoning_content) and replaces lone
  U+D800–U+DFFF surrogates with U+FFFD before serializing.
- Failure mode: byte-token-level reasoning models (Kimi K2.5, GLM-5, some
  Qwen3 quantizations) occasionally emit lone surrogates that crash
  `json.dumps()` inside the OpenAI SDK with `UnicodeEncodeError`.
- Co-cli status: missing. We have not observed this in production yet, but
  the failure is silent until it isn't.

### 1.5 `sanitize_tool_schemas` — llama.cpp grammar compatibility

- Hermes: `tools/schema_sanitizer.py:40` (`sanitize_tool_schemas`),
  `:90` (`strip_nullable_unions`), `:152` (`_sanitize_node`)
- What it does: walks tool JSON schemas before send and fixes shapes that
  llama.cpp's GBNF grammar generator cannot consume — bare-string types
  (`"object"` instead of `{"type": "object"}`), `type: [X, "null"]` arrays,
  nullable `anyOf`/`oneOf` unions, missing `properties` on object types,
  unconstrained `additionalProperties`, invalid `required` entries.
- Failure mode: llama.cpp returns HTTP 400 "Unable to generate parser for
  this template" and the request is rejected outright.
- Co-cli status: missing. pydantic-ai produces clean schemas from typed
  Python signatures, so our hand-defined tools are safe today. The risk
  surface opens when MCP is re-enabled — upstream MCP servers ship raw
  schemas with no scrubbing, and several common MCP servers do emit nullable
  unions.

## 2. Tier 2 — recovery loops (silent-failure prevention)

These patterns recover from output drift instead of crashing or silently
producing the wrong result.

### 2.1 `_repair_tool_call` — fuzzy tool-name normalization

- Hermes: `run_agent.py:5169-5240`
- What it does: when `tool_calls.function.name` does not match the
  registry, applies five repair passes — exact case-insensitive, normalize
  separators (`-`/space → `_`), CamelCase→snake_case, strip trailing
  `_tool` / `-tool` / `tool` suffix (twice for double-tacks), then
  difflib fuzzy match with cutoff 0.7. Cites issue #14784 in-source for the
  original reports (`TodoTool_tool`, `Patch_tool`, `BrowserClick_tool`).
- Failure mode: models trained on class-style tool names emit
  `TodoTool` when the registry has `todo`. Without repair, agent loops on
  "Unknown tool" or aborts.
- Co-cli status: missing. We have not observed this pattern with qwen3.6,
  but adding the repair is cheap insurance.

### 2.2 `_should_treat_stop_as_truncated` + `_is_ollama_glm_backend`

- Hermes: `run_agent.py:3027` (backend check), `:3037` (truncation check),
  called at `:11120`
- What it does: detects Ollama-hosted GLM models (or `zai` provider GLM)
  that misreport `finish_reason="stop"` when the response was actually
  truncated. Conservative gate: model contains "glm", local endpoint,
  message history contains tool results, current assistant has no
  tool_calls, content suggests incomplete response.
- Failure mode: GLM on Ollama silently runs out of tokens mid-response
  but reports `stop`, so the agent stops too early instead of triggering
  a `/continue` retry.
- Co-cli status: missing. Only matters if you run GLM models — which
  co-cli does not target today, but the check is GLM-specific and
  inexpensive.

### 2.3 `_deduplicate_tool_calls` — strip duplicate tool calls

- Hermes: `run_agent.py:5152-5167`, called at `:12816`
- What it does: removes duplicate `(name, args)` pairs within a single
  model response while preserving order.
- Failure mode: smaller Qwen / GLM variants occasionally emit the same
  tool_call twice. Without dedup, the tool runs twice — wasted tokens,
  potential side effects, double approval prompts.
- Co-cli status: missing.

### 2.4 XML tool-call tag stripping

- Hermes: `run_agent.py:~3120-3150` (regex `r'</(?:tool_call|tool_calls|tool_result|function_call|function_calls|function)>\s*'`)
- What it does: strips standalone XML tool-call blocks from assistant
  message content when the model emits them outside the structured
  `tool_calls` array.
- Failure mode: models that fall back to text-mode tool calling (e.g.,
  some open-source fine-tunes) emit `<tool_call>{...}</tool_call>` in
  content and an empty `tool_calls` array. Without stripping, the visible
  output is polluted; without parsing, the tool call is lost.
- Co-cli status: missing. qwen3.6 in noreason mode was observed emitting
  `<tool_code>\nshell git status\n</tool_code>` (probe at
  `tmp/probe_qwen36_with_tools.py`), so the failure mode is reachable in
  practice.

### 2.5 Length-continuation retry on `finish_reason == "length"`

- Hermes: `run_agent.py:~10731-10762` (continuation flag), main loop
  rebuilds the request with the partial response prepended.
- What it does: detects truncated responses and re-invokes the model in
  a continuation mode to resume generation. Bounded retry count to
  prevent infinite loops.
- Failure mode: a tool-calling response that hits max_tokens leaves
  arguments JSON half-emitted; without continuation the partial is lost
  and the agent terminates.
- Co-cli status: partial. `co_cli/context/orchestrate.py:498-501` detects
  `finish_reason == "length"` but only emits a status hint suggesting the
  user run `/continue` — there is no automatic retry.

## 3. Tier 3 — applicable but lower priority

### 3.1 `_NEVER_PARALLEL_TOOLS` — block interactive tools from parallel exec

- Hermes: `run_agent.py:303` (`frozenset({"clarify"})`)
- What it does: when a model emits multiple tool calls and one is in
  the never-parallel set, executes them sequentially instead of via
  ThreadPoolExecutor.
- Failure mode: interactive tools like `clarify` race with concurrent
  tools and the user sees the prompt flash before the other tools finish.
- Co-cli status: missing. pydantic-ai's parallel execution mode is
  configurable per-agent (`parallel_tool_call_execution_mode` attribute);
  setting it to sequential when `clarify` is present is the equivalent.

### 3.2 `_escape_invalid_chars_in_json_strings` — pre-pass control-char escape

- Hermes: `run_agent.py:549-588`
- What it does: char-walk to escape literal control chars (0x00-0x1F)
  inside JSON string values before parse. This is repair pass 0 of
  `_repair_tool_call_arguments` (1.1) — bundled with that fix.
- Failure mode: llama.cpp backends emit literal tabs/newlines inside
  JSON string values; `json.loads(strict=False)` rejects them.
- Co-cli status: missing. Picked up automatically if 1.1 is ported.

## 4. Patterns mentioned but not verified

The upstream survey returned these without line numbers I could spot-check.
Treat as suspected-but-unconfirmed; not in the gap list above unless a
future verification pass confirms them.

- Compression warning replay via `status_callback` (claimed at
  `run_agent.py:10142-10144`).
- "Empty text after tool calls" tolerant handling (claimed at
  `run_agent.py:11050-11100`).
- Non-ASCII character stripping for ASCII-only backends (claimed
  `_strip_non_ascii` symbol).
- Thinking-only assistant message filtering (claimed
  `_is_thinking_only_assistant`, `_drop_thinking_only_and_merge_users`).
- Kimi/Moonshot thinking-mode `reasoning_content` passthrough enforcement.
- DeepSeek thinking-content passthrough.
- Schema budget trimming on token overflow (claimed but no concrete
  symbol cited; may be implicit in compression).
- Preflight token-estimate compression (claimed at
  `run_agent.py:10270-10330`; co-cli has its own compaction subsystem
  in `co_cli/context/compaction.py`, comparison out of scope).

## 5. Recommended fixes — ROI prioritized

Ranking is for "small Ollama model + tool calls" reliability, weighted
by probability of hitting the failure × cost of the fix.

### 5.1 Highest payoff, low cost

1. **Port `_repair_tool_call_arguments` (1.1)** — drop ~80 lines of
   pure-Python JSON repair into a defensive wrapper around pydantic-ai's
   tool-args parser. Catches the GLM/Qwen malformed-JSON crash class
   entirely. Includes the control-char escape pass (3.2) as a side
   effect.
2. **Port `_sanitize_surrogates` (1.4)** — ~40 lines, runs once before
   the message dict goes to the SDK. Cheap insurance against byte-token
   reasoning models. Zero regression risk.
3. **Inject `num_ctx` per request (1.2 upgrade)** — co-cli already
   probes; just thread the probed value through `LlmSettings.noreason_model_settings`
   and `reasoning_model_settings` as `extra_body["options"]["num_ctx"]`.
   Today's safety net is the qwen3.6 Modelfile; this fix removes the
   silent dependency on Modelfile authorship.

### 5.2 Medium payoff

4. **`_repair_tool_call` fuzzy tool-name (2.1)** — useful only for
   models that emit class-style names; we have not seen this in
   qwen3.6:27b-agentic. Defer until we see it in a production run.
5. **Length-continuation retry (2.5)** — promote the existing
   status hint at `co_cli/context/orchestrate.py:498-501` to an
   automatic resume. Bigger change — touches orchestrate and the
   pytest expectations around `finish_reason`.
6. **`_deduplicate_tool_calls` (2.3)** — ~10 lines; cost is low, but
   we have not observed duplicates from qwen3.6.
7. **XML tool-call tag stripping (2.4)** — observed in our own probes,
   so the failure mode is reachable. Lower priority because the fix
   we shipped (enable reasoning for tool-routing tests) eliminates the
   most common path to the failure.

### 5.3 Low priority / observe-first

8. **`_NEVER_PARALLEL_TOOLS` analog (3.1)** — depends on whether we
   add interactive tools that race with concurrent tools. Today the
   only candidate is `clarify`.
9. **GLM stop-misreport detection (2.2)** — only matters if we add
   GLM to the supported model set.

## 6. Skip / not applicable

- **Stream-stale timeout + heartbeats (1.3)** — pydantic-ai owns the
  streaming path; there is no insertion point. Co-cli's HTTP read timeout
  of 300s in `co_cli/llm/factory.py:16` is enough for our current workload.
- **Qwen Portal headers** (`run_agent.py:~1998-2010`) — co-cli targets
  Ollama, not the Qwen portal API.
- **`sanitize_tool_schemas` for llama.cpp (1.5)** — pydantic-ai produces
  clean schemas from typed Python signatures. Revisit only when MCP is
  re-enabled and external schema sources can land in the registry.
- **Anthropic prompt caching (`prompt_caching.py`)** — Anthropic-only;
  Ollama's OpenAI-compatible endpoint has no `cache_control` field. Not a
  gap, just a provider limit.
- **Qwen Portal-specific message normalization** — same reason as Qwen
  Portal headers.
