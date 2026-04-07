# RESEARCH: Codex Tool Lifecycle vs co-cli

> Source-code-driven comparison only.
> This version mirrors the standalone peer lifecycle notes.
> It records only what is present in `~/workspace_genai/codex/`, what is present in current `co-cli`, and the direct differences observed in source.

## 1. Scope

Compared code:

- `co-cli`
- `~/workspace_genai/codex/`

Main codex files checked:

- `../codex/codex-rs/tools/src/tool_spec.rs`
- `../codex/codex-rs/tools/src/responses_api.rs`
- `../codex/codex-rs/core/src/tools/router.rs`
- `../codex/codex-rs/core/src/tools/registry.rs`
- `../codex/codex-rs/core/src/tools/context.rs`
- `../codex/codex-rs/core/src/hook_runtime.rs`
- `../codex/codex-rs/protocol/src/models.rs`

Main co-cli files checked:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)
- [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py)
- [files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py)
- [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py)
- [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)

---

## 2. Verified Facts About codex

### 2.1 Tool spec and wire schema

`codex` defines tool wire shapes in `../codex/codex-rs/tools/src/tool_spec.rs`.

Observed in `ToolSpec`:

- `Function(ResponsesApiTool)`
- `ToolSearch`
- `LocalShell`
- `ImageGeneration`
- `WebSearch`
- `Freeform`

Observed in `ResponsesApiTool` in `../codex/codex-rs/tools/src/responses_api.rs`:

- `name`
- `description`
- `strict`
- optional `defer_loading`
- `parameters`
- `output_schema`

Observed schema assembly:

- `create_tools_json_for_responses_api(...)` serializes the assembled `ToolSpec` list for the Responses API
- `mcp_tool_to_responses_api_tool(...)` converts MCP tool definitions into function-tool specs
- `mcp_tool_to_deferred_responses_api_tool(...)` converts MCP tool definitions into deferred function-tool specs

### 2.2 Registry and routing

`codex` separates tool specification from execution handlers.

Observed in `../codex/codex-rs/core/src/tools/registry.rs`:

- `ToolRegistryBuilder.push_spec(...)`
- `ToolRegistryBuilder.push_spec_with_parallel_support(...)`
- `ToolRegistryBuilder.register_handler(...)`
- `ToolRegistryBuilder.build()` returning `(Vec<ConfiguredToolSpec>, ToolRegistry)`

Observed registry keying:

- handler lookup uses `tool_handler_key(...)`
- keys are `tool_name` or `{namespace}:{tool_name}`

Observed in `../codex/codex-rs/core/src/tools/router.rs`:

- `ToolRouter.from_config(...)` builds specs plus registry together
- `ToolRouter` stores both `specs` and `model_visible_specs`
- when `code_mode_only_enabled` is true, nested code-mode tools are filtered out of `model_visible_specs`

### 2.3 Execution contract and mutation gating

`codex` uses a typed handler trait for dispatch.

Observed in `ToolHandler` in `../codex/codex-rs/core/src/tools/registry.rs`:

- `kind()`
- `matches_kind(...)`
- async `is_mutating(...)`
- `pre_tool_use_payload(...)`
- `post_tool_use_payload(...)`
- async `handle(...)`

Observed in `dispatch_any(...)`:

- handler existence is checked before execution
- payload kind compatibility is checked before execution
- pre-tool hooks run when a handler provides a pre-tool payload
- `is_mutating(...)` is evaluated before execution
- mutating tools wait on `turn.tool_call_gate`
- post-tool hooks run when a handler provides a post-tool payload

### 2.4 Hook runtime

`codex` contains a dedicated hook-runtime layer in `../codex/codex-rs/core/src/hook_runtime.rs`.

Observed hook entrypoints:

- `run_pre_tool_use_hooks(...)`
- `run_post_tool_use_hooks(...)`

Observed request fields passed into those hook calls:

- `session_id`
- `turn_id`
- `cwd`
- `transcript_path`
- `model`
- `permission_mode`
- `tool_name`
- `tool_use_id`
- `command`
- `tool_response` for post-tool hooks

### 2.5 Tool invocation and result forms

`codex` carries a typed invocation object and a typed output contract.

Observed in `ToolInvocation` in `../codex/codex-rs/core/src/tools/context.rs`:

- `session`
- `turn`
- `tracker`
- `call_id`
- `tool_name`
- `tool_namespace`
- `payload`

Observed in `ToolPayload`:

- `Function`
- `ToolSearch`
- `Custom`
- `LocalShell`
- `Mcp`

Observed in `ToolOutput`:

- `log_preview()`
- `success_for_logging()`
- `to_response_item()`
- optional `post_tool_use_response()`
- `code_mode_result()`

Observed output implementations:

- `FunctionToolOutput` stores `body`, `success`, and optional `post_tool_use_response`
- `ExecCommandToolOutput` stores `raw_output`, `wall_time`, `max_output_tokens`, `process_id`, `exit_code`, `original_token_count`, and `session_command`

Observed result shaping:

- `ExecCommandToolOutput.truncated_output()` applies token-based truncation through `formatted_truncate_text(...)`
- `ExecCommandToolOutput.to_response_item()` emits a function-call output item containing the formatted response text

### 2.6 Sandbox override surface

`codex` defines per-command sandbox override types in `../codex/codex-rs/protocol/src/models.rs`.

Observed in `SandboxPermissions`:

- `UseDefault`
- `RequireEscalated`
- `WithAdditionalPermissions`

Observed helper methods:

- `requires_escalated_permissions()`
- `requests_sandbox_override()`
- `uses_additional_permissions()`

---

## 3. Verified Facts About co-cli Relevant To The Comparison

### 3.1 Tool registration and visibility

In `co-cli`, native tools are registered through `_reg()` in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py).

The current native metadata type is `ToolInfo` in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py).

Observed `ToolInfo` fields populated by `_reg()`:

- `approval`
- `load` (type: `LoadPolicy` enum — `ALWAYS` or `DEFERRED`)
- `search_hint`
- `integration`
- `max_result_size`

Observed visibility controls:

- `session.discovered_tools`
- `runtime.resume_tool_names`
- `ToolInfo.load` with `LoadPolicy.ALWAYS` / `LoadPolicy.DEFERRED`

### 3.2 Approval handling

Observed in `co-cli`:

- deferred approval collection in [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- approval subject resolution in [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- remembered session approvals in `deps.session.session_approval_rules`

Observed approval subject categories:

- `shell`
- `path`
- `domain`
- `tool`

Observed shell-specific policy in [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py):

- `DENY`
- `ALLOW`
- `REQUIRE_APPROVAL`

### 3.3 Tool results and display

Observed in `co-cli`:

- native tool results are produced through `tool_output(...)` in [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)
- `tool_output(...)` returns `ToolReturn`
- `return_value` carries the string sent back through the native tool result path
- `metadata` carries extra app-side fields
- `tool_output(...)` requires `ctx: RunContext[CoDeps]` as a keyword arg and reads the current tool's `max_result_size` from `ToolInfo` to persist oversized results
- `tool_output_raw(...)` is the ctx-free variant for helper functions that lack a RunContext

Observed display handling in [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py):

- string results are passed through directly
- dict payloads are summarized into compact key/value text

### 3.4 Concurrency safety

`co-cli` contains a per-resource lock store in [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py).

Observed in `ResourceLockStore`:

- locks are keyed by resource identifier
- `try_acquire(...)` is non-blocking and raises `ResourceBusyError` if the key is already held

Observed in [files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py):

- `edit_file(...)` acquires a lock keyed by the resolved file path before read-modify-write

---

## 4. Verified Differences Present In codex And Not Observed In co-cli

The items below were checked directly in the current source files listed above.

### 4.1 Separate handler trait with execution payload hooks

Observed in `codex`:

- `ToolHandler.kind(...)`
- async `ToolHandler.is_mutating(...)`
- `ToolHandler.pre_tool_use_payload(...)`
- `ToolHandler.post_tool_use_payload(...)`
- async `ToolHandler.handle(...)`

Not observed in checked `co-cli` native registration and execution files:

- native registration in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py) stores `ToolInfo` metadata and hands execution to pydantic-ai tool functions directly
- the checked `co-cli` files do not define a native handler trait matching `ToolHandler`

### 4.2 Separate registry-builder and router layers with namespace-keyed dispatch

Observed in `codex`:

- `ToolRegistryBuilder`
- `ToolRegistry`
- `ToolRouter`
- namespace-aware dispatch keys through `tool_handler_key(...)`

Not observed in checked `co-cli` native registration path:

- native tools are added to a `FunctionToolset` in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- MCP toolsets are assembled separately
- the checked `co-cli` files do not define a native namespace-keyed dispatch layer equivalent to `ToolRegistryBuilder` plus `ToolRouter`

### 4.3 Registry-level mutating gate before tool execution

Observed in `codex`:

- `dispatch_any(...)` checks `is_mutating(...)`
- mutating tools wait on `turn.tool_call_gate` before execution

Observed in `co-cli`:

- per-resource locking in [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)
- file-path locking in [files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py)

Not observed in checked `co-cli` files:

- a general registry-level mutating gate applied across all native tools before execution

### 4.4 Trait-based tool outputs that encode multiple response-item shapes

Observed in `codex`:

- `ToolOutput.to_response_item(...)`
- `ToolOutput.code_mode_result(...)`
- `FunctionToolOutput`
- `ExecCommandToolOutput`
- typed payload variants such as `Function`, `ToolSearch`, and `Mcp`

Not observed in checked `co-cli` native tool result path:

- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py) returns `ToolReturn(return_value, metadata)`
- the checked `co-cli` files do not define a native output trait that converts results into multiple response-item variants

### 4.5 Per-command sandbox override enum

Observed in `codex`:

- `SandboxPermissions::UseDefault`
- `SandboxPermissions::RequireEscalated`
- `SandboxPermissions::WithAdditionalPermissions`

Observed in `co-cli`:

- `requires_approval` on native tool registration
- shell `DENY` / `ALLOW` / `REQUIRE_APPROVAL`

Not observed in checked `co-cli` files:

- a typed per-command sandbox-override enum matching `SandboxPermissions`

### 4.6 Dedicated pre-tool and post-tool hook runtime in the dispatch path

Observed in `codex`:

- `run_pre_tool_use_hooks(...)`
- `run_post_tool_use_hooks(...)`
- `dispatch_any(...)` invokes those hook runners when the handler provides payloads

Not observed in checked `co-cli` tool lifecycle files:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

---

## 5. Verified Differences Present In co-cli And Not Observed In codex

### 5.1 Semantic approval subjects with session-remembered scope keys

Observed in `co-cli`:

- `ApprovalSubject`
- subject kinds `shell`, `path`, `domain`, and `tool`
- remembered approvals stored in `session_approval_rules`

Not observed in the checked `codex` lifecycle files:

- no approval-subject value type matching `ApprovalSubject`

### 5.2 Per-resource mutation locks in the native tool path

Observed in `co-cli`:

- `ResourceLockStore`
- path-keyed locking in `edit_file(...)`

Not observed in the checked `codex` lifecycle files:

- no per-resource lock store equivalent to [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)

### 5.3 Per-tool result-size threshold in native registration metadata

Observed in `co-cli`:

- `_reg(..., max_result_size=...)` in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- `ToolInfo.max_result_size` in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- `tool_output(...)` reading that threshold in [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

Not observed in the checked `codex` registration files:

- no native registry metadata field matching `max_result_size`

---

## 6. Minimal Fact Summary

Directly observed in `codex` and not observed in the checked `co-cli` lifecycle files:

1. a separate `ToolHandler` execution trait with pre-tool and post-tool payload hooks
2. separate registry-builder and router layers with namespace-keyed dispatch
3. a registry-level mutating gate via `turn.tool_call_gate`
4. trait-based outputs that encode multiple response-item shapes
5. a typed per-command sandbox override enum
6. dedicated pre-tool and post-tool hook runners in the dispatch path

Directly observed in `co-cli` and not observed in the checked `codex` lifecycle files:

1. semantic approval subjects with session-remembered scope keys
2. per-resource mutation locks in the native file-edit path
3. per-tool result-size thresholds stored in native registration metadata

---

## 7. Tool Surface Gap

This section is based on the built-in tool assembly in `../codex/codex-rs/core/src/tools/spec.rs` plus the exported tool definitions in `../codex/codex-rs/tools/src/lib.rs`. The `co-cli` side is based on native registrations in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py).

### 7.1 Tools provided by codex and not observed in current co-cli native tools

There is overlap on directory listing, shell execution, web search, and delegated-agent concepts. The additional native Codex tools directly observed in the checked source and not observed in current `co-cli` native registrations are:

- interactive command execution surfaces: `exec_command`, `write_stdin`, `shell_command`
- explicit planning and permission tools: `update_plan`, `request_permissions`, `request_user_input`
- patching and tool-discovery helpers: `apply_patch`, `tool_search`, `tool_suggest`
- MCP resource tools: `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource`
- image and REPL tools: `view_image`, `js_repl`, `js_repl_reset`, `ImageGeneration`
- code-mode control tools: `code`, `wait`
- multi-agent lifecycle tools richer than the current co-cli surface: `spawn_agent`, `send_input` or `send_message`, `assign_task`, `resume_agent`, `wait_agent`, `close_agent`, `list_agents`, `spawn_agents_on_csv`, `report_agent_job_result`
- optional utility/test surfaces: `test_sync_tool`

Some Codex tools are feature-gated in `ToolsConfig`, but they are still directly present in the native built-in tool assembly checked here.

### 7.2 Tools provided by co-cli and not observed in the checked codex built-in tool assembly

Observed in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py) and not observed in `../codex/codex-rs/core/src/tools/spec.rs` built-ins:

- todo tools: `write_todos`, `read_todos`
- knowledge and article tools: `search_knowledge`, `search_articles`, `read_article`, `save_article`
- broader persistent memory tools: `list_memories`, `save_memory`, `update_memory`, `append_memory`
- background task controls: `start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks`
- native Obsidian integration: `list_notes`, `search_notes`, `read_note`
- native Google integration tools: `search_drive_files`, `read_drive_file`, `list_gmail_emails`, `search_gmail_emails`, `create_gmail_draft`, `list_calendar_events`, `search_calendar_events`

The checked Codex files also support app and MCP tool import, but this section compares only the native built-in surfaces assembled in `spec.rs`.
