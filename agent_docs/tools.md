# Agents, Tools, and Config

## Tool Pattern

Native tools use `@agent_tool(...)` with `RunContext[CoDeps]`; `_build_native_toolset()` registers them with pydantic-ai. All runtime resources come from `ctx.deps` — never import settings directly. Never hold module-level state in tool files: tool modules are imported once and shared across all runs in the same process, so mutable globals cause test interference and session bleed. Never put approval prompts inside tools — that bypasses the deferred-approval mechanism and breaks approval-resume.

## Tool Approval

Tools that mutate system state (filesystem writes, external service writes, process spawning) use `approval=True` on `@agent_tool(...)` — this routes through the deferred-approval mechanism; putting approval logic inside the tool body bypasses it. Runtime-approval tools such as `shell` and `code_execute` may raise `ApprovalRequired` based on command policy. Read-only operations do not require approval. Approval UX lives in the chat loop.

## Tool Return Type

Tools returning user-facing data must use the project's `tool_output()` helper for structured returns; use `tool_error()` for failures. Never return a raw `str`, bare `dict`, or `list[dict]` — raw returns silently omit tracing metadata and the structured fields the chat loop depends on.

## CoDeps

Flat dataclass — access service handles, config, and paths via `ctx.deps.*` (e.g. `ctx.deps.shell`, `ctx.deps.config.memory.max_count`).

## Sub-Agent Isolation

Use the subagent deps factory in `deps.py`. Do not manually field-copy.

## Config

`Settings` uses nested Pydantic sub-models in `co_cli/config/` (one file per group). Add new fields to an existing group if it fits; only create a new nested group when it has meaningful cohesion. Config precedence: env vars > `~/.co-cli/settings.json` > defaults. (No project-local `.co-cli/settings.json` layer exists today; all user state is user-global.)

## User-Global Paths

`~/.co-cli/` (overridable via `CO_HOME`). No project-local state directory exists.

## Versioning

`MAJOR.MINOR.PATCH`; patch odd = bugfix, even = feature. Bump only in `pyproject.toml`. Git history is the changelog; releases use GitHub Releases — tag `vX.Y.Z` and push to trigger `.github/workflows/release.yml`.

## No `.env` Files

Use `settings.json` or env vars.

## Adding a Tool

- Tool file in `co_cli/tools/`; import and register in `_build_native_toolset()` in `co_cli/agent.py`.
- Return `tool_output()` / `tool_error()` — never a raw `str`, `dict`, or `list`.
- First docstring line is the tool schema description — make it count.
- `approval=True` for any tool that writes files, spawns processes, or calls external write APIs.
- `ALWAYS` visibility = present every turn; `DEFERRED` = discovered via search_tools on demand.
