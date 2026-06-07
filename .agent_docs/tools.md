# Agents, Tools, and Config

## Tool Pattern

Native tools use `@agent_tool(...)` with `RunContext[CoDeps]`; `_build_native_toolset()` registers them with pydantic-ai. All runtime resources come from `ctx.deps` — never import settings directly. Never hold module-level state in tool files: tool modules are imported once and shared across all runs in the same process, so mutable globals cause test interference and session bleed. Never put approval prompts inside tools — that bypasses the deferred-approval mechanism and breaks approval-resume.

## Tool Availability Gating

Integration tools (tools that require external config or runtime state) have two registration-time gates and one call-time fallback. Choose based on when availability is knowable.

**`requires_config="field_name"`** — build-time exclusion. `_build_native_toolset()` checks `getattr(config, field_name)` before registering; if falsy, the tool is never added to the toolset. Use when the tool cannot function under any circumstances without the config field — no point advertising it to the model at all.

**`check_fn=fn`** — per-turn hide. `fn(deps) -> bool` is wrapped into a pydantic-ai `prepare` callback invoked before each model turn. Returning `False` omits the tool from that turn's tool manifest; returning `True` includes it. The tool remains in the toolset between turns, so availability can change mid-session (credentials refresh, vault mounted/unmounted) without restart. Use when the config field may be present but runtime state is needed to confirm usability.

**Combining both** — use `requires_config` as the hard outer gate and `check_fn` as the soft inner gate. Example: `google_drive_*` is excluded entirely when `google_credentials_path` is absent (`requires_config`), then hidden per-turn when the token is expired (`check_fn`).

**Call-time failure** — `web_search` has neither gate; it fails at call time when `brave_search_api_key` is None. This is a gap, not a pattern to follow. Prefer `requires_config` for any new tool whose API key is required.

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

- Tool file in `co_cli/tools/`; decorate with `@agent_tool` (self-registers into `TOOL_REGISTRY`) and ensure its module is imported in `co_cli/agent/toolset.py` so the decorator runs.
- Return `tool_output()` / `tool_error()` — never a raw `str`, `dict`, or `list`.
- First docstring line is the tool schema description — make it count.
- `approval=True` for any tool that writes files, spawns processes, or calls external write APIs.
- `ALWAYS` visibility = present every turn; `DEFERRED` = hidden by the per-turn visibility filter and surfaced by name via the `tool_view` loader on demand (co-owned; no SDK `search_tools`).
- For integration tools: use `requires_config="field"` to exclude when unconfigured, `check_fn=fn` to hide per-turn when runtime state is unavailable. See **Tool Availability Gating**.
