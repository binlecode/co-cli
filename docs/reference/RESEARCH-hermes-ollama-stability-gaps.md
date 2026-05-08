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

### 1.1 `query_ollama_num_ctx` — per-request num_ctx injection — CLOSED

- Hermes: `run_agent.py:139` (import), `:2044` (per-request inject)
- What it does: probes Ollama `/api/show` for the loaded Modelfile's
  `num_ctx` and injects it into every chat request body so the OpenAI-compat
  endpoint uses the model's full window instead of Ollama's 2048-token
  default.
- Failure mode: without injection, Ollama serves the request at 2048 tokens
  even when the Modelfile declares a larger window — tool schemas get
  truncated out of the system prompt and the model hallucinates.
- Co-cli status: **closed**. `_LLM_SETTINGS` in `co_cli/config/llm.py`
  hardcodes `extra_body["options"]["num_ctx"]` per model/mode; `_ollama_settings()`
  passes it through to every request. Injection is static (not dynamically
  probed), with `max_ctx` as the contract pivot: the probed Modelfile value
  must be `>= max_ctx` (floor check, `bootstrap/core.py:_check_ollama_num_ctx_floor`),
  and the static config value must be `<= max_ctx` (ceiling check,
  `bootstrap/check.py:validate_ollama_num_ctx`). `validate_config()` rejects
  any model without an `_LLM_SETTINGS` entry, so all supported models get
  the injection.

### 1.3 `_sanitize_surrogates` — replace lone surrogate code points — CLOSED

- Hermes: `run_agent.py:433` (definition), called at `:8454, :8456, :8482,
  :10097, :10099` and one more site
- What it does: walks every string field in the outgoing message dict
  (content, name, tool_calls.arguments, reasoning_content) and replaces lone
  U+D800–U+DFFF surrogates with U+FFFD before serializing.
- Failure mode: byte-token-level reasoning models (Kimi K2.5, GLM-5, some
  Qwen3 quantizations) occasionally emit lone surrogates that crash
  `json.dumps()` inside the OpenAI SDK with `UnicodeEncodeError`.
- Co-cli status: **closed**. `sanitize_surrogate_codepoints` is implemented
  in `co_cli/context/history_processors.py:418-479` (regex `[\ud800-\udfff]`,
  walks `content` on request parts and both `content` and `args` on response
  parts) and registered as a history processor in `co_cli/agent/core.py:149`.

### 1.4 `sanitize_tool_schemas` — llama.cpp grammar compatibility — CLOSED

- Hermes: `tools/schema_sanitizer.py:40` (`sanitize_tool_schemas`),
  `:90` (`strip_nullable_unions`), `:152` (`_sanitize_node`)
- What it does: walks tool JSON schemas before send and fixes shapes that
  llama.cpp's GBNF grammar generator cannot consume — bare-string types
  (`"object"` instead of `{"type": "object"}`), `type: [X, "null"]` arrays,
  nullable `anyOf`/`oneOf` unions, missing `properties` on object types,
  unconstrained `additionalProperties`, invalid `required` entries.
- Failure mode: llama.cpp returns HTTP 400 "Unable to generate parser for
  this template" and the request is rejected outright.
- Co-cli status: **closed**. `context7` ships as the default MCP server and
  arbitrary user-defined MCP servers are accepted via `settings.json`. Native
  tool schemas (typed Python signatures → pydantic-ai) remain clean. MCP
  ingestion-time sanitization is implemented via `co_cli/tools/mcp_schema.py`
  (`sanitize_mcp_schema`) and a `_SanitizingMCPServer` proxy in
  `co_cli/agent/mcp.py` that patches `inputSchema` on every `list_tools()`
  response before pydantic-ai consumes it.
  Plan: `docs/exec-plans/active/2026-05-07-112044-mcp-schema-sanitizer.md`.

## 2. Tier 2 — recovery loops (silent-failure prevention)

These patterns recover from output drift instead of crashing or silently
producing the wrong result.

### 2.1 `_repair_tool_call` — fuzzy tool-name normalization — DEFERRED

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
- Co-cli status: **deferred**. Only relevant for models that emit class-style
  names; defer until a production run surfaces the failure.

### 2.2 `_deduplicate_tool_calls` — strip duplicate tool calls — CLOSED

- Hermes: `run_agent.py:5152-5167`, called at `:12816`
- What it does: removes duplicate `(name, args)` pairs within a single
  model response while preserving order.
- Failure mode: smaller Qwen / GLM variants occasionally emit the same
  tool_call twice. Without dedup, the tool runs twice — wasted tokens,
  potential side effects, double approval prompts.
- Co-cli status: **closed**. Implemented as `before_node_run` on
  `CoToolLifecycle` (`co_cli/tools/lifecycle.py`) — fires once per
  `CallToolsNode` before approval prompts and parallel tool dispatch.
  Uses `_dedup_tool_call_parts` to drop later `ToolCallPart`s whose
  `(tool_name, args)` matches an earlier one; non-`ToolCallPart` parts
  pass through unchanged. Coverage: `tests/test_flow_tool_call_dedup.py`.

### 2.3 XML tool-call tag stripping - DEFERRED

- Hermes: `run_agent.py:~3120-3150` (regex `r'</(?:tool_call|tool_calls|tool_result|function_call|function_calls|function)>\s*'`)
- What it does: strips standalone XML tool-call blocks from assistant
  message content when the model emits them outside the structured
  `tool_calls` array.
- Failure mode: models that fall back to text-mode tool calling (e.g.,
  some open-source fine-tunes) emit `<tool_call>{...}</tool_call>` in
  content and an empty `tool_calls` array. Without stripping, the visible
  output is polluted; without parsing, the tool call is lost.
- Co-cli status: missing. The failure mode is reachable with open-source
  fine-tunes that fall back to text-mode tool calling.

### 2.4 Length-continuation retry on `finish_reason == "length"`

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

### 3.1 `_NEVER_PARALLEL_TOOLS` — block interactive tools from parallel exec — CLOSED

- Hermes: `run_agent.py:303` (`frozenset({"clarify"})`)
- What it does: when a model emits multiple tool calls and one is in
  the never-parallel set, executes them sequentially instead of via
  ThreadPoolExecutor.
- Failure mode: interactive tools like `clarify` race with concurrent
  tools and the user sees the prompt flash before the other tools finish.
- Co-cli status: **closed**. `ToolInfo.is_concurrent_safe` on native tools
  maps to `ToolDefinition.sequential` via `_build_native_toolset`
  (`_native_toolset.py:148`). For MCP tools, `_SequentialMCPToolset` in
  `co_cli/agent/mcp.py` wraps each MCP toolset and patches
  `ToolDefinition.sequential = not info.is_concurrent_safe` in
  `get_tools()` at step time, using a live reference to `tool_index`.
  pydantic-ai's `get_parallel_execution_mode()` returns `'sequential'`
  for the whole batch when any tool has `sequential=True`, so one
  non-concurrent-safe tool in a mixed batch forces full serialization.

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

1. ~~**Port `_sanitize_surrogates` (1.3)**~~ — closed; `sanitize_surrogate_codepoints`
   implemented in `history_processors.py` and registered in `agent/core.py`.
2. ~~**Inject `num_ctx` per request (1.1)**~~ — closed; static injection
   via `_LLM_SETTINGS` extra_body is in place for all supported models.

### 5.2 Medium payoff

3. ~~**`_repair_tool_call` fuzzy tool-name (2.1)**~~ — deferred; only relevant for
   models that emit class-style names. Revisit if a production run surfaces the failure.
4. **Length-continuation retry (2.4)** — promote the existing
   status hint at `co_cli/context/orchestrate.py:498-501` to an
   automatic resume. Bigger change — touches orchestrate and the
   pytest expectations around `finish_reason`.
5. ~~**`_deduplicate_tool_calls` (2.2)**~~ — closed; `before_node_run`
   on `CoToolLifecycle` dedups `(tool_name, args)` pairs in each
   `ModelResponse` before approval and dispatch.
6. **XML tool-call tag stripping (2.3)** — reachable with open-source
   fine-tunes that fall back to text-mode tool calling. Worth porting.

### 5.3 Low priority / observe-first

7. ~~**`_NEVER_PARALLEL_TOOLS` analog (3.1)**~~ — closed; `_SequentialMCPToolset`
   propagates `is_concurrent_safe` to `ToolDefinition.sequential` for MCP tools,
   matching native tool behavior in `_native_toolset.py`.

## 6. Skip / not applicable

- **Qwen Portal headers** (`run_agent.py:~1998-2010`) — co-cli targets
  Ollama, not the Qwen portal API.
- ~~**`sanitize_tool_schemas` for llama.cpp (1.4)**~~ — closed; see §1.4.
- **Anthropic prompt caching (`prompt_caching.py`)** — Anthropic-only;
  Ollama's OpenAI-compatible endpoint has no `cache_control` field. Not a
  gap, just a provider limit.
- **Qwen Portal-specific message normalization** — same reason as Qwen
  Portal headers.
