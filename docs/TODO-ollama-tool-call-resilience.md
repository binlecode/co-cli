# TODO: Ollama Tool-Call Resilience

## RCA — Three-Layer Failure Chain

1. **Ollama rejects malformed tool-call JSON** — Small quantized model (glm-4.7-flash:q8_0, ~4B params) produces invalid JSON in tool-call arguments → Ollama returns HTTP 400 "invalid tool call arguments"
2. **pydantic-ai wraps as `ModelHTTPError`** (from `pydantic_ai.exceptions`) — This error is **not retried**. The `retries=3` config on the agent only covers `ModelRetry` raised inside tool functions, not HTTP-level failures from the model provider
3. **Chat loop generic `except Exception`** catches the error, prints it, and the turn ends — no recovery attempt

## Root Causes

- **Small quantized model** (~4B params) is unreliable at structured output / tool-call JSON generation
- **18 tool schemas** consume significant context budget, leaving less room for reasoning
- **System prompt lacks file-exploration examples** — model has no guidance on chaining shell commands for common tasks like finding and reading files

## Solution

### Phase 1 (MVP): Reflect `ModelHTTPError` 400 back to model for self-correction

All top CLI agents (Codex, Aider, Gemini-CLI, OpenCode) converge on treating HTTP 400 as **non-retryable** — blind retry sends the same prompt and often gets the same bad output. Instead, they use **reflection**: feed the error back to the model so it can self-correct.

- Catch `ModelHTTPError` where `status_code == 400` in the chat loop
- Inject a system/user message into the conversation: "Tool call failed: invalid JSON arguments. Please reformulate your tool call with valid JSON."
- Let the agent run again with the error context (max 2 reflection attempts, 0.5 s backoff)
- New config setting: `model_http_retries` (default `2`)
- Precedent: Codex returns `FunctionCallOutput` with error + `success: false`; Aider stores in `reflected_message`; pydantic-ai's own `ModelRetry` feeds validation errors back to the model
- Files: `co_cli/main.py`, `co_cli/config.py`

### Phase 2: Enrich system prompt for tool-calling guidance

- Add a "File Operations via Shell" section to the system prompt with `ls`/`cat`/`find`/`grep` examples showing how to chain shell commands for file exploration
- Add 2 eval cases for file-exploration prompts to the eval golden set
- Files: `co_cli/agent.py`, `evals/tool_calling.jsonl`

## Research — Peer System Patterns

### Small models and malformed tool calls (common, well-documented)

- Ollama GitHub: [#13519](https://github.com/ollama/ollama/issues/13519) — llama3.2:3b outputs tool calls as JSON in content instead of `tool_calls` field; [#12064](https://github.com/ollama/ollama/issues/12064) — tool call parsing errors; [#11800](https://github.com/ollama/ollama/issues/11800) — unexpected error format from gpt-oss models
- LiteLLM [#17807](https://github.com/BerriAI/litellm/issues/17807) — Ollama `/api/chat` fails to produce valid JSON for structured output
- Aider tracks `num_malformed_responses` in `base_coder.py` — showing this is a measured, expected failure mode

### HTTP 400 handling — converged pattern across top systems

| System | HTTP 400 | Recovery strategy | Source |
|--------|----------|-------------------|--------|
| **Codex** (Rust) | Non-retryable (`InvalidRequest`) | Returns `FunctionCallOutput` with error + `success: false` → model sees failure | `error.rs:191-226`, `mcp_tool_call.rs:43-55` |
| **Aider** (Python) | Non-retryable (`BadRequestError`, `retry: false`) | Stores in `reflected_message` for reflection-based recovery | `exceptions.py:6-52`, `base_coder.py:1470-1482` |
| **Gemini-CLI** (TS) | Explicitly non-retryable | Exponential backoff + jitter only for 429/5xx/network | `retry.ts:111-142` |
| **OpenCode** (TS) | Only retries 429/5xx/network | Respects `Retry-After` headers, exponential backoff fallback | `retry.ts:28-96` |

**Convergence**: All 4 systems classify 400 as terminal. Recovery uses reflection (error fed back to model), not blind retry. Retries reserved for transient failures: 429, 5xx, network timeouts.

### Retry strategies — common parameters

| System | Max retries | Backoff | Jitter | Retry-After |
|--------|-------------|---------|--------|-------------|
| **Codex** | 4 request / 10 stream | Exponential | — | — |
| **Aider** | Budget-based (60 s cap) | 0.125 s → 60 s exponential | — | — |
| **Gemini-CLI** | 3 | 5 s → 30 s exponential | ±30% | Yes |
| **OpenCode** | 3 | 2 s → 30 s exponential | — | Yes |

### pydantic-ai native mechanisms

- `ModelRetry` raised inside tools → feeds validation error back to model (reflection pattern)
- `retries` agent config (default 1) governs `ModelRetry` budget — does **not** cover `ModelHTTPError`
- HTTP transport retries via tenacity for transient network failures — separate from tool-call retries

## Verification

- Run eval: `uv run python scripts/eval_tool_calling.py --dim arg_extraction` with `LLM_PROVIDER=ollama`
- Manual: `uv run co chat` → ask "show me the eval script" → agent should use shell to find and read it
