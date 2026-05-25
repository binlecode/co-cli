# Peer Repos

Peer repos in `~/workspace_genai/` used for design research. See `RESEARCH-personality-peer-survey.md` for personality research.

## Repo List

| Repo | Relevance to co-cli |
|------|---------------------|
| `fork-claude-code` | Agent CLI, tool approval, config, compaction, TUI |
| `hermes-agent` | Direct co-cli peer — agent CLI, REPL, streaming |
| `codex` | Agent CLI from OpenAI; tool sandboxing, approval UX, multi-agent orchestration |
| `opencode` | Agent CLI, tool patterns, config |
| `pydantic-ai` | Python agent framework; capability bundles, tool approval, durable execution, MCP integration |
| `elizaos` | Character personality schema, tool policy layering, memory scoping |
| `letta` | Memory architecture (MemGPT-style), stateful agent design |
| `mem0` | Memory layer; semantic extraction, decay, multi-user personalization |
| `openclaw` | Multi-channel LLM agent gateway (PI core); compaction, loop detection, multi-provider routing, plugin skills/tools |

## System Alignment

Which repos are the primary reference for each co subsystem:

| co concern | Primary peers |
|---|---|
| Agent CLI loop, approval, compaction, streaming | `fork-claude-code`, `hermes-agent`, `codex`, `opencode`, `openclaw` |
| Python agent framework (tool patterns, capability bundles) | `pydantic-ai` — co uses this directly; peer reference for how it is designed against |
| Personality / character schema | `elizaos` |
| Memory architecture | `letta`, `mem0` |
| Skill / plugin lifecycle | `openclaw` |

Note: no peer currently covers co's **observability** surface (structured spans, `co tail`, trace viewers). Closest external reference is Logfire (pydantic-ai's companion product) — a service, not a repo.

## Target Model Context

Co's current model table (`_LLM_SETTINGS` in `co_cli/config/llm.py`) — relevant when researching how peers handle model routing:

| Provider | Model key | Reasoning mode |
|---|---|---|
| `ollama` | `qwen3.6` | reasoning + noreason |
| `gemini` | `gemini-3-flash-preview` | reasoning + noreason |
| `gemini` | `gemini-2.5-flash` | noreason only |
| `gemini` | `gemini-2.5-flash-lite` | noreason only |

Key constraints surfaced in the May 2026 code scan:

- **Hard-coded model table.** `validate_config()` rejects any model not in `_LLM_SETTINGS` and additionally requires a `reasoning` entry for the main agent slot. Adding a new Ollama family (e.g., Llama, Mistral, Phi) requires a code change — no user-level extensibility. Research how peers (codex, opencode, openclaw) handle model config to find patterns for making this table open.
- **No lightweight Ollama fallback.** Default is `qwen3.6:35b` (35B params). No smaller-model entry exists for modest hardware. Research how peers handle model tiering or capability-based fallback.
- **`gemini-3-flash-preview` key may be stale.** Current Google API model names use date-suffixed variants (`gemini-2.5-flash-preview-05-20`). This key doesn't match any current GA name — worth verifying against the Gemini API before relying on it in research comparisons.
- **No Anthropic/Claude target.** Co is built with Claude Code and uses pydantic-ai (which supports Anthropic natively), but Claude is absent from the model table. This is coherent with the local-first philosophy — Claude has no local deployment option — but worth noting when comparing peer model coverage.
- **`llm.max_ctx` ceiling is 65536.** Hardcoded cap on the probed Ollama context window. Modern models (including Qwen3) support much larger windows. Research how peers expose or tune context window limits.

## Open Research Areas

Gaps identified from the May 2026 architecture review that peer research should inform:

1. **Model table extensibility** — how do peers (codex, opencode, openclaw) let users add arbitrary models without code changes? Plugin/capability registration patterns.
2. **Approval persistence across sessions** — `session_approval_rules` resets at REPL exit. Research how peers (openclaw, fork-claude-code) persist approval decisions durably without compromising the trust boundary.
3. **Lighter local model paths** — how do peers tier model selection by hardware capability or task type? Relevant to co's single-model-handle design.
4. **Background learning UX** — dream daemon is opt-in (`dream.enabled = false`). Research how peers surface background agent activity to users without adding friction. Relevant to making dream's value visible by default.
5. **Personality feedback loop** — co's personality is session-static (canon as doctrine, no interaction feedback). Research whether peers (elizaos, openclaw) have patterns for stable character with interaction-driven adaptation.
