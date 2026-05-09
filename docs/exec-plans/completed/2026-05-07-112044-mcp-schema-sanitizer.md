# Plan: MCP Schema Sanitizer

Task type: code

## Context

co-cli ships `context7` as the default MCP server and accepts arbitrary
user-defined MCP servers via `settings.json` → `mcp_servers` or the
`CO_MCP_SERVERS` env var. pydantic-ai ingests MCP tool `inputSchema` dicts
directly from each server's `list_tools()` response and passes them to the
model without any sanitization.

For Ollama / llama.cpp backends, malformed schemas cause HTTP 400 "Unable to
generate parser for this template" — the request is rejected outright and the
tool is silently lost. For the Anthropic API, nullable union shapes cause
schema rejection.

Problematic schema shapes emitted by real-world MCP servers:
- `"type": "object"` as a bare string instead of `{"type": "object"}`
- `"type": ["string", "null"]` arrays — llama.cpp grammar generator rejects
- `anyOf`/`oneOf` with a null branch — llama.cpp grammar generator rejects
- Object nodes missing `properties` key
- `required` entries that name fields absent from `properties`
- Missing top-level `type` on nodes that have `properties` or `required`

Peer reference: `hermes-agent` implements a two-stage pipeline — MCP
ingestion normalization (`_normalize_mcp_input_schema`) at registration time,
followed by a pre-send backend-compat pass (`sanitize_tool_schemas`) at model
call time. Context documented in `docs/reference/RESEARCH-hermes-ollama-stability-gaps.md`
§1.4 (now closed).

Native co-cli tools are unaffected — pydantic-ai generates their schemas from
typed Python signatures, which are always clean. The sanitizer applies to MCP
schemas only.

### Current-state validation (inline)

- ✓ `co_cli/agent/mcp.py:78-114` — `discover_mcp_tools()` calls
  `await entry.server.list_tools()` and iterates `Tool` objects. Only
  `t.name` and `t.description` are read here; `t.inputSchema` is passed
  through pydantic-ai to the model untouched.
- ✓ `co_cli/agent/mcp.py:33-75` — `_build_mcp_toolsets()` instantiates
  `MCPServerStdio` / `MCPServerSSE` / `MCPServerStreamableHTTP` from
  `pydantic_ai.mcp`. No schema post-processing exists.
- ✓ `co_cli/config/mcp.py:49-55` — `DEFAULT_MCP_SERVERS` ships `context7`
  as the always-on default server.
- ✓ `co_cli/tools/` — all 31 native tools use typed Python signatures; zero
  manual schema construction (confirmed by audit, 2026-05-07).
- ✓ pydantic-ai `Tool` objects returned by `list_tools()` expose
  `.inputSchema` as a mutable dict attribute — safe to patch in place after
  `list_tools()` returns and before pydantic-ai serializes for the model.
- ✓ No existing schema sanitization module in `co_cli/`.
- ✓ No active plan conflicts with this slug.

## Problem & Outcome

**Problem:** MCP tool schemas arrive from upstream servers with shapes that
llama.cpp's grammar generator and the Anthropic API reject. co-cli forwards
them raw, so malformed schemas produce silent HTTP 400 failures on Ollama
backends and API-level rejections on Anthropic — both lose the tool call
without a clear error.

**Failure cost:** Context7 (the default server) likely emits clean schemas,
so the default config is low-risk today. The risk surface is unbounded for
user-added MCP servers — common community MCP servers (filesystem, GitHub,
Slack) are known to emit nullable unions and missing-`properties` objects.
Backends targeted: Ollama and Gemini.

**Outcome:**
- A pure schema sanitizer (`co_cli/tools/mcp_schema.py`) normalizes every
  MCP tool `inputSchema` to a shape all supported backends accept.
- Sanitization runs in `_build_mcp_toolsets()` by wrapping each `MCPServer`
  so `list_tools()` results are patched before pydantic-ai consumes them.
- Native tool schemas are unaffected — the sanitizer is MCP-path-only.
- Tests verify each repair class with real (non-mock) schema dicts.

## Scope

### In scope
- `co_cli/tools/mcp_schema.py` — new module with pure sanitizer logic.
- `co_cli/agent/mcp.py` — wrap each `MCPServer` in `_build_mcp_toolsets()`
  to apply sanitization after `list_tools()`.
- `tests/test_flow_mcp_schema.py` — behavioral tests for each repair class.

### Out of scope
- Native tool schemas — clean by construction; sanitizer does not touch them.
- `sanitize_tool_schemas` as a pre-send pass on all tools (hermes Layer 2
  pattern) — the MCP ingestion hook is sufficient; the extra pass adds
  complexity for zero additional protection on native schemas.
- MCP server config schema validation (prefix, timeout, etc.) — separate
  concern.
- `sanitize_tool_schemas` on user-provided tools outside MCP — no such
  surface exists in co-cli today.

## Behavioral Constraints

1. **Pure function**: `sanitize_mcp_schema(schema: dict) -> dict` returns a
   deep copy — never mutates the input. Called once per tool at ingestion;
   cost is negligible.
2. **Repairs applied unconditionally**: every MCP schema passes through the
   sanitizer regardless of backend. Anthropic, Ollama, and OpenAI-compat all
   benefit from the same normalization.
3. **Information-preserving**: sanitization never drops semantically
   meaningful fields. `description`, `title`, `default`, `examples` survive
   all transforms. When a nullable union collapses to its non-null branch,
   the null branch is dropped — no extra hint fields added.
4. **Recursive**: repair walks `properties`, `items`, `additionalProperties`,
   `anyOf`, `oneOf`, `allOf`, `$defs`, `definitions`. Nested schemas inherit
   all repairs.
5. **Top-level guarantee**: output is always `{"type": "object", ...}` with
   a `properties` dict — even for a null or non-dict input.
6. **Native tools unaffected**: the sanitizer is only called from the MCP
   ingestion path. `_build_native_toolset()` does not call it.
7. **Idempotent**: applying the sanitizer twice produces the same result as
   applying it once.

## High-Level Design

### `co_cli/tools/mcp_schema.py`

Single public entry point: `sanitize_mcp_schema(schema: Any) -> dict`.

Internal helpers (not exported):

```
_sanitize_node(node: Any) -> dict
  ├─ if not dict → return {"type": "object", "properties": {}}
  ├─ bare string type → wrap: "object" → {"type": "object"}
  ├─ type array ["X", "null"] → {"type": "X"}
  ├─ anyOf/oneOf with single null branch → collapse to non-null branch
  │    preserve description/title/default/examples from wrapper
  ├─ missing "type" when "properties" or "required" present → inject "object"
  ├─ missing "properties" on object node → inject {}
  ├─ prune "required" to names present in "properties"
  └─ recurse into: properties values, items, additionalProperties,
                   anyOf[], oneOf[], allOf[], $defs values, definitions values

sanitize_mcp_schema(schema):
  result = _sanitize_node(copy.deepcopy(schema))
  ensure top-level has "type": "object" and "properties": {}
  return result
```

Repair priority order (applied sequentially within `_sanitize_node`):
1. Non-dict guard (return sentinel object).
2. Bare-string `type` → dict wrap.
3. `type` array → single type + nullable flag.
4. `anyOf`/`oneOf` nullable collapse.
5. Infer missing `type` from presence of `properties`/`required`.
6. Inject missing `properties` on object nodes.
7. Prune invalid `required` entries.
8. Recurse into child nodes.

### MCP ingestion hook in `co_cli/agent/mcp.py`

Wrap each `MCPServer` with a lightweight proxy class `_SanitizingMCPServer`
that overrides `list_tools()`:

```python
class _SanitizingMCPServer:
    """Thin MCPServer proxy that sanitizes inputSchema on list_tools()."""

    def __init__(self, inner):
        self._inner = inner

    async def list_tools(self):
        tools = await self._inner.list_tools()
        for t in tools:
            t.inputSchema = sanitize_mcp_schema(t.inputSchema or {})
        return tools

    def __getattr__(self, name):
        return getattr(self._inner, name)
```

In `_build_mcp_toolsets()`, wrap `mcp_server` before using it:

```python
sanitizing_server = _SanitizingMCPServer(mcp_server)
inner = sanitizing_server.approval_required() if approval else sanitizing_server
entries.append(MCPToolsetEntry(
    toolset=DeferredLoadingToolset(inner),
    server=sanitizing_server,   # list_tools() calls go through proxy
    ...
))
```

**Why proxy, not post-hoc mutation in `discover_mcp_tools()`:** pydantic-ai
stores the server reference and calls `list_tools()` again at model-call time
to build the schema list for the request. Mutating Tool objects after a single
`discover_mcp_tools()` call would not cover the model-call-time `list_tools()`
invocation. The proxy ensures every `list_tools()` call — at discovery time
and at request-build time — returns sanitized schemas.

### TASK-3 — Anthropic nullable guard (verify-first)

pydantic-ai's Anthropic adapter converts `parameters` to `input_schema` when
building Anthropic API requests. Verify by reading
`pydantic_ai/providers/anthropic.py` (or equivalent) whether it already:
- strips `nullable: true` extension fields, and
- collapses any remaining `anyOf [X, null]` that the sanitizer's
  `keep_nullable_hint` path left behind.

If pydantic-ai handles this: close TASK-3 as N/A, note the finding here.
If not: add a thin Anthropic-specific post-processor in `co_cli/agent/mcp.py`
or hook into the model request path.

## Tasks

### ✓ DONE — TASK-1: Implement `co_cli/tools/mcp_schema.py`

- **files:**
  - `co_cli/tools/mcp_schema.py` (new)
- **prerequisites:** —
- **done_when:**
  `python -c "from co_cli.tools.mcp_schema import sanitize_mcp_schema; print('ok')"` exits 0.
  All TASK-3 schema-unit assertions pass.
- **success_signal:** Pure sanitizer handles all six repair classes, is
  recursive, and returns a deep copy.

Implement `sanitize_mcp_schema` and `_sanitize_node` per the design above.
No imports from outside stdlib + `copy`. No dependencies on pydantic-ai
or co_cli internals — this module must be importable in isolation.

### ✓ DONE — TASK-2: MCP ingestion hook in `_build_mcp_toolsets()`

- **files:**
  - `co_cli/agent/mcp.py`
- **prerequisites:** [TASK-1]
- **done_when:**
  `python -c "from co_cli.agent.mcp import _build_mcp_toolsets; print('ok')"` exits 0.
  Integration assertion in TASK-3 (`test_sanitizer_applied_at_list_tools`)
  passes.
- **success_signal:** Every MCPServer created in `_build_mcp_toolsets()` is
  wrapped in `_SanitizingMCPServer`; `entry.server.list_tools()` returns
  tools with sanitized `inputSchema`.

Add `_SanitizingMCPServer` proxy class (private, leading underscore) to
`co_cli/agent/mcp.py`. Update `_build_mcp_toolsets()` to wrap each
`mcp_server` before the `approval_required()` / bare branch. Update
`MCPToolsetEntry` construction so both `toolset` and `server` go through
the sanitizing proxy.

Import `sanitize_mcp_schema` from `co_cli.tools.mcp_schema` inside
`_build_mcp_toolsets()` (lazy, to mirror the existing lazy pydantic_ai.mcp
import pattern and avoid top-level cost when MCP is not configured).

### ✓ DONE — TASK-3: Behavioral tests

- **files:**
  - `tests/test_flow_mcp_schema.py` (new)
- **prerequisites:** [TASK-1, TASK-2]
- **done_when:**
  `uv run pytest tests/test_flow_mcp_schema.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mcp-schema.log`
  passes with all assertions below.
- **success_signal:** N/A (test file).

**Schema-unit tests** (test the pure sanitizer, no I/O):

1. `test_bare_string_type_wrapped` — `_sanitize_node("object")` → returns
   `{"type": "object", "properties": {}}`.

2. `test_type_array_collapsed` — `{"type": ["string", "null"], "description": "x"}`
   → `{"type": "string", "description": "x"}`.

3. `test_anyof_nullable_collapsed` — `{"anyOf": [{"type": "string"}, {"type":
   "null"}], "description": "y"}` → `{"type": "string", "description": "y"}`.

4. `test_missing_properties_injected` — `{"type": "object"}` → output has
   `"properties": {}`.

5. `test_missing_type_inferred` — `{"properties": {"x": {"type": "integer"}},
   "required": ["x"]}` → `"type": "object"` injected in output.

6. `test_invalid_required_pruned` — `{"type": "object", "properties": {"a":
   {"type": "string"}}, "required": ["a", "b"]}` → `"required": ["a"]`.

7. `test_null_input` — `sanitize_mcp_schema(None)` → `{"type": "object",
   "properties": {}}`.

8. `test_recursive_nested` — a schema with a `properties` value that itself
   has a `type: ["integer", "null"]` array → inner node is also repaired.

9. `test_deep_copy_no_mutation` — original dict unchanged after
   `sanitize_mcp_schema(original)`.

10. `test_idempotent` — `sanitize_mcp_schema(sanitize_mcp_schema(schema)) ==
    sanitize_mcp_schema(schema)` for a schema containing multiple repair classes.

**Integration test** (tests the proxy wiring, no live MCP server):

11. `test_sanitizer_applied_at_list_tools` — construct a `_SanitizingMCPServer`
    wrapping a `_FakeMCPServer` whose `list_tools()` returns a `Tool` object
    with a malformed `inputSchema` (e.g. `type: ["string", "null"]`). Call
    `await proxy.list_tools()`. Assert returned tool's `inputSchema` is
    sanitized. `_FakeMCPServer` is a real in-test class (no mocks) with a
    real `list_tools()` coroutine.

Use no mocks. `_FakeMCPServer` is a minimal real class:
```python
class _FakeMCPServer:
    async def list_tools(self):
        tool = SimpleNamespace()
        tool.name = "test_tool"
        tool.description = "a tool"
        tool.inputSchema = {"type": ["string", "null"]}
        return [tool]
```

## Testing

### Test files
- `tests/test_flow_mcp_schema.py` — schema unit tests (1–10) + integration (11).

### Test pattern
- Pure-function tests (1–10): call `sanitize_mcp_schema()` directly, assert
  output. No async, no deps, no fixtures.
- Integration test (11): `asyncio.run()` or `pytest.mark.asyncio` wrapping the
  `_SanitizingMCPServer` call. `_FakeMCPServer` is defined inline in the test
  file. No live network, no subprocess.
- No mocks anywhere. No test-only MCP config.

### Lint / quality gate
- `scripts/quality-gate.sh lint` after each task.
- `scripts/quality-gate.sh full` before ship.

## Open Questions

1. **Q:** Does pydantic-ai's `Tool` dataclass use `__slots__` or freeze
   `inputSchema`? If so, in-place mutation in `_SanitizingMCPServer.list_tools()`
   would fail and we'd need to reconstruct `Tool` objects.
   **Resolved:** `mcp.types.Tool` extends `BaseMetadata` with `model_config = ConfigDict(extra="allow")` — not frozen. `t.inputSchema` is mutable dict assignment. No reconstruction needed.

2. **Q:** Does pydantic-ai call `list_tools()` once (at connect time) or
   on every model call?
   **Resolved:** `MCPServer.list_tools()` caches results in `_cached_tools` (default `cache_tools=True`). The proxy's `list_tools()` is called first during discovery (`_discover_one`), mutates tool objects in place, and populates the cache. Subsequent internal `get_tools()` → `self.list_tools()` calls return the cached (already-sanitized) objects.

## Delivery Summary — 2026-05-09

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | import exits 0; TASK-3 schema-unit assertions pass | ✓ pass |
| TASK-2 | import exits 0; `test_sanitizer_applied_at_list_tools` passes | ✓ pass |
| TASK-3 | `uv run pytest tests/test_flow_mcp_schema.py -x` passes | ✓ pass |

**Tests:** scoped (`tests/test_flow_mcp_schema.py`) — 11 passed, 0 failed
**Doc Sync:** fixed (`system.md` — stale model default; `co_cli/agent/mcp.py` docstring updated)

**Note:** Pre-delivery, dropped TASK-3 (Anthropic nullable guard) — Anthropic is not a supported provider (`LlmSettings` allows only `"ollama" | "gemini"`). Nullable unions are collapsed to the non-null branch with no hint field added.

**Overall: DELIVERED**
All three tasks shipped. Sanitizer handles all six repair classes, is recursive, idempotent, and returns a deep copy. Every MCP server built in `_build_mcp_toolsets()` is wrapped in `_SanitizingMCPServer`; discovery and model-call-time `list_tools()` return sanitized schemas via the cached-mutation pattern.

## Deferred items

- **Pre-send pass on all tools** (hermes Layer 2) — deferred; native tools
  are clean by construction and MCP tools are sanitized at ingestion.
- **`_NEVER_PARALLEL_TOOLS` analog for interactive MCP tools** — unrelated
  to schema; tracked in the hermes-gaps research doc.
- **`_repair_tool_call` fuzzy tool-name** — unrelated to schema; deferred
  until observed in a production run (per research doc §5.2).
