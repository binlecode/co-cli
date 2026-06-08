# RESEARCH: pydantic-ai SDK Usage in co-cli

**Pinned version:** `pydantic-ai==1.81.0` (slim extras: anthropic, google, openai, mcp, …) — unchanged at sync
**Survey date:** 2026-06-07 · **Last synced to source:** 2026-06-08
**Method:** Two passes. Pass 1 = fan-out summary across all importing modules. Pass 2 = direct source verification of every refactor-critical claim (`bootstrap/schema_budget.py`, `agent/toolset.py`, `agent/mcp.py`, `agent/build.py`, `context/history_processors.py`, `context/_compaction_boundaries.py`, `observability/serialize.py`, `llm/surrogate_recovery_model.py`, `deps.py:ToolInfo`). Line numbers verified as of the last-synced date.
**Purpose:** Drive the next refactor. Findings are ranked by refactor leverage, not by symbol.

> **What changed in pass 2** (read this if you saw v1): three v1 claims were wrong and are corrected below — (a) the `schema_budget.py` private import **cannot** be removed by reading co's own tool metadata (co stores no JSON schema; the schema is SDK-generated), so the fix is import-path-only; (b) the message-part "taxonomy smear" was **overstated** — the real duplication is narrow and mostly within-file, so it is down-ranked from "highest-leverage" to "optional"; (c) the suggestion to fold `_SequentialMCPToolset` into `_SanitizingMCPServer` is **architecturally unsound** (different object layers) and is retracted. Pass 2 also surfaced the genuine #1 smell that v1 missed: `_unwrap_function_toolset`.

> **2026-06-08 sync** (re-verified against current source, all line numbers re-checked): three drifts since the survey — (a) `schema_budget.py` now lives at **`co_cli/bootstrap/schema_budget.py`** (paths updated throughout; line numbers unchanged); (b) the tool-metadata catalog field is named **`tool_catalog`** (not `tool_index` — that name exists nowhere in the tree); (c) the unused `SystemPromptPart` import in `observability/serialize.py` (old §3.6) **has already been removed** — that finding is resolved and struck below. All other findings (§3.1 coupling, §3.2 import paths, §3.3 stream seam, §3.5 output type, the `UsageLimits(None)` ceremony) remain valid and verified.

---

## 0. Executive summary

co-cli is a **deep, structural consumer** of pydantic-ai — well past "instantiate an Agent and call `.run()`". Five layers:

1. **Agent + run lifecycle** — `Agent`, `AgentRunResult`, streamed-event consumption, usage/limit types.
2. **The message/part type system** — ~20 `pydantic_ai.messages` types pattern-matched, hand-constructed, serialized, and rewritten across compaction/history/summarization/persistence/observability.
3. **Toolset composition** — `FunctionToolset`, `CombinedToolset`, two `WrapperToolset` subclasses, `.filtered()`, `ToolDefinition` patching.
4. **Deferred-tool / approval protocol** — `DeferredToolRequests/Results`, `ApprovalRequired`, `ToolApproved`, `ToolDenied`, `ModelRetry`.
5. **Model wrapping** — `SurrogateRecoveryModel(WrapperModel)` overriding `request`/`request_stream`, plus provider/model factories and `ModelSettings`.

**Health verdict (refactor-oriented):**

| Rank | Finding | Severity | Effort | §|
|---|---|---|---|---|
| 1 | `bootstrap/schema_budget.py` is the most SDK-internal-coupled module: private `_run_context` import **and** `_unwrap_function_toolset` topology heuristic | 🔴 high | low–med | 3.1 |
| 2 | Inconsistent import paths (`RunContext`, `RunUsage` from 2–3 module paths) | 🟠 med | trivial | 3.2 |
| 3 | JSON-repair stream proxy pins correctness to a **private** agent-graph internal (`_agent_graph._streaming_handler`) via a docstring assumption | 🟠 med | n/a (document) | 3.3 |
| 4 | Message-part type-set knowledge spread across ~7 context modules; narrow copy-paste duplication | 🟡 low–med | med | 3.4 |
| 5 | Orchestrator declared `Agent[CoDeps, Any]` though its real output is `str | DeferredToolRequests` | 🟡 low | trivial | 3.5 |
| 6 | `UsageLimits(request_limit=None)` ceremony (the unused `SystemPromptPart` import is already removed) | 🟡 low | trivial | 3.6 |

**Cleanly justified, do not touch:** Agent lifecycle (§2.1), the two `WrapperToolset` subclasses (§2.3), client-side compaction's deep `messages` use (§2.2, on-design per `feedback_context_management_self_contained`), `SurrogateRecoveryModel` (§2.5), `ModelMessagesTypeAdapter` persistence, the approval protocol types (§2.4 — owned by the `drop-capability-api` plan, not a cleanup target).

---

## 1. Full symbol inventory

### `pydantic_ai` (top level)

| Symbol | Role |
|---|---|
| `Agent` | Orchestrator + task-agent class; type alias `SessionAgent` |
| `RunContext` | Tool/instruction/processor context carrying `deps` (CoDeps) |
| `DeferredToolRequests` | Output-union marker → "approvals pending" control signal |
| `DeferredToolResults` | Response object carrying per-call approval decisions |
| `ApprovalRequired` | Base class for co's `QuestionRequired`; SDK approval-pause hook |
| `ToolApproved` | "Approve as-is, keep original args" (clarify tool) |
| `ToolDenied` | "Reject this call" with reason string |
| `ModelRetry` | Tool-side retryable-error exception |
| `AgentRunResult` | Result carrier — `.output`, `.usage()`, `.new_messages()`, `.all_messages()` |
| `AgentRunResultEvent` | Terminal stream event wrapping the result |

### `pydantic_ai.messages`

Containers: `ModelMessage`, `ModelRequest`, `ModelResponse`, `ModelResponsePart`, `ModelMessagesTypeAdapter`.
Parts: `TextPart`, `ThinkingPart`, `ToolCallPart`, `ToolReturnPart`, `ToolReturn`, `SystemPromptPart`, `UserPromptPart`, `RetryPromptPart`.
Streaming: `FinalResultEvent`, `FunctionToolCallEvent`, `FunctionToolResultEvent`, `PartStartEvent`, `PartDeltaEvent`, `PartEndEvent`, `TextPartDelta`, `ThinkingPartDelta`.

Imported across **38 modules** (most are the per-tool `ToolReturn` import; the heavy *matching* lives in ~7 `context/` modules — see §2.2).

### `pydantic_ai.toolsets` / `.tools`

`AbstractToolset`, `WrapperToolset`, `FunctionToolset`, `CombinedToolset` (`.combined`), `ToolsetTool` (`.abstract`), `ToolDefinition` (`pydantic_ai.tools`).

### `pydantic_ai.models` / providers / settings / usage / exceptions

`model_request` (`.direct`); `ModelRequestParameters`, `StreamedResponse` (`.models`); `WrapperModel` (`.models.wrapper`); `OpenAIChatModel` (`.models.openai`); `GoogleModel`, `GoogleModelSettings` (`.models.google`); `OllamaProvider`, `GoogleProvider` (`.providers.*`); `ModelSettings` (`.settings`); `RunUsage`, `UsageLimits` (`.usage`); `ModelHTTPError`, `ModelAPIError`, `UnexpectedModelBehavior` (`.exceptions`).

### ⚠️ Private / non-canonical paths

- `from pydantic_ai._run_context import RunContext` — **bootstrap/schema_budget.py:23** (private module; public re-export exists at `pydantic_ai`).
- `from pydantic_ai.result import RunUsage` — **bootstrap/schema_budget.py:24** (public home is `pydantic_ai.usage`).

---

## 2. Cluster-by-cluster analysis

### 2.1 Agent + run lifecycle — ✅ justified, load-bearing

**Verified (build.py):**
- `build_orchestrator` (build.py:40–49): `Agent(raw_model, deps_type=CoDeps, instructions=…, model_settings=…, retries=…, output_type=[str, DeferredToolRequests], history_processors=list(spec.history_processors), toolsets=[deps.toolset])`. Per-turn instructions attached via `agent.instructions(per_turn)` (build.py:51–52).
- `build_task_agent` (build.py:104–111): second `Agent` per delegation, `output_type=spec.output_type`, `toolsets=[_CallSeamToolset(FunctionToolset())]` — task tools routed through the **same** call_tool wrapper as the orchestrator (build.py:97–99 comment: "parity with the deleted capability on task agents").
- Orchestrator drives the turn via `agent.run_stream_events(...)` (orchestrate.py ~379–394); task agents use `agent.run(...)`. Results read back via `.output`, `.usage()`, `.new_messages()`, `.all_messages()`.

**Assessment:** Canonical, intended usage. co does **not** hand-reconstruct messages from the event stream — it lets the agent build `ModelRequest`/`ModelResponse` and pulls them from the result. Correct boundary. The two-agent split (orchestrator vs task) is inherent to delegation, not a defect.

**Refactor note (low):** Both builders annotate `Agent[CoDeps, Any]` (build.py:15, 40, 57, 104). The orchestrator's true contract is `str | DeferredToolRequests` (and is already hinted that way at `main.py` / `commands/types.py`). See §3.5.

### 2.2 The message/part type system — ✅ heavy but on-design; 🟡 narrow duplication

**Where the matching actually lives (verified):**
- `history_processors.py` — sanitize request string-parts `(UserPromptPart, SystemPromptPart, RetryPromptPart, ToolReturnPart)` (575–576), response text-parts `(TextPart, ThinkingPart)` (589) + `ToolCallPart.args` (595–605); dedup/evict/spill `ToolReturnPart` (84–92, 119, 162, 331); `ToolCallPart.args_as_dict()` indexing (243–249). **The rewrite contract itself is already centralized** in `_rewrite_tool_returns` (94–127) and shared by dedup/evict/spill/strip — good existing practice.
- `compaction.py` — `ModelRequest`+`UserPromptPart` focus extraction (414–416); `ModelResponse`+`ToolCallPart` counting (459–461).
- `prompt_text.py` — `ModelResponse`+`ToolCallPart` doom-loop hashing (39–42); `ModelRequest`+`ToolReturnPart` shell-error scan (85–88).
- `summarization.py` — `UserPromptPart`/`TextPart`/`ToolCallPart`/`ToolReturnPart` serialize for summarizer (262–275); token estimation via `ToolCallPart.args_as_dict()` + `ToolReturnPart.content` (57–61).
- `_compaction_boundaries.py` — `ModelRequest`+`UserPromptPart` turn boundary (69–71 **and** 125); `ModelResponse`+`(TextPart, ThinkingPart)` first-run anchor (109–110).
- `observability/serialize.py` — `UserPromptPart`/`TextPart`/`ThinkingPart`/`ToolCallPart` → span JSON, with the `getattr(part, "part_kind", part.__class__.__name__)` fallback at **43 and 68**.

**Assessment:** The breadth is **intrinsic and justified** — co owns client-side compaction (`feedback_context_management_self_contained`), which requires understanding message structure. Each module does a *genuinely different operation* (sanitize / serialize / boundary-detect / token-estimate / rewrite), so there is **no single visitor** that would unify them. v1's "5 silent breakage sites → 1" framing was overstated.

**Actual duplication (precise, verified):**
- `getattr(part, "part_kind", part.__class__.__name__)` — **2×, both in serialize.py** (43, 68). Within-file.
- `isinstance(msg, ModelRequest) and any(isinstance(p, UserPromptPart) …)` — **2×, both in _compaction_boundaries.py** (69–71 `group_by_turn`, 125 `_find_last_turn_start`). Within-file. (`compaction.py:414–416` and `prompt_text.py` touch the same notion but with different surrounding logic.)
- `isinstance(p, (TextPart, ThinkingPart))` — **2×, cross-module** (history_processors.py:589, _compaction_boundaries.py:110).

**Refactor (medium, optional):** a thin `co_cli/context/_parts.py` exposing the *type-set constants* and 2–3 predicates — `REQUEST_STRING_PARTS`, `RESPONSE_TEXT_PARTS`, `msg_starts_turn(msg)`, `part_kind_label(part)`. This puts the "closed set" knowledge in one place and makes it testable, but the immediate copy-paste payoff is small (≈4 sites). Do it **with** the part work, not as a standalone push. Keep `ModelMessagesTypeAdapter` (persistence.py) and `_rewrite_tool_returns` exactly as they are.

### 2.3 Toolset composition — ✅ justified

**Verified (toolset.py, mcp.py, core.py):**
- `FunctionToolset()` populated via `add_function(fn, requires_approval=…, sequential=not is_concurrent_safe, retries=…, prepare=_make_prepare(check_fn))` (toolset.py:119–135).
- `CombinedToolset([native, *mcp])` → `.filtered(_tool_visibility_filter)` → wrapped by `_CallSeamToolset` (core.py).
- **`_CallSeamToolset(WrapperToolset)`** (toolset.py:140–224) overrides `call_tool` for three co-located concerns: per-model-request tool-call cap (166–197, keyed on `ctx.run_step`), the `co.tool.*` OTEL span (178–223), MCP large-result spill (200–212). Delegates via `super().call_tool` (199).
- **`_SequentialMCPToolset(WrapperToolset)`** (mcp.py:43–63) overrides `get_tools` to patch `ToolDefinition.sequential` from `tool_catalog[name].is_concurrent_safe` via `replace(...)`.
- `_tool_visibility_filter` (toolset.py:62–85) is co's **own** deferral mechanism (keyed on `tool_catalog` visibility + `runtime.unlocked_tools`); the SDK's `defer_loading`/`search_tools` is deliberately **not** used (documented at toolset.py:104–109, mcp.py:118–121).

**Assessment:** Textbook `WrapperToolset` usage. The two subclasses are the minimum for two distinct boundaries (call-time vs list-time).

**🔁 v1 RETRACTION:** v1 suggested folding `_SequentialMCPToolset` into `_SanitizingMCPServer.list_tools()`. **This is architecturally unsound.** `_SanitizingMCPServer.list_tools()` (mcp.py:24–30) operates on **raw MCP-protocol `Tool` objects** (`t.inputSchema`); `sequential` lives on the **pydantic-ai `ToolDefinition`** produced *later* by the SDK's MCP toolset, a different object at a different layer. They cannot be merged. Leave both as-is.

### 2.4 Deferred-tool / approval protocol — ✅ justified; owned by `drop-capability-api`

**Verified flow:** orchestrator `output_type` includes `DeferredToolRequests`; when `result.output` is that type, co iterates `output.approvals` + `output.metadata`, builds a `DeferredToolResults`, and re-enters `run_stream_events(..., deferred_tool_results=…)`. Per-call decisions: `True` (approve), `ToolDenied("…")` (reject), `ToolApproved()` (approve-as-is, used by the clarify tool which reads answers from a side channel). `ApprovalRequired` is subclassed by co's `QuestionRequired` (approvals.py).

**Assessment:** Clean, bidirectional. These value types are *thin data carriers* — co already drives the entire approval loop in `orchestrate.py`; the SDK contributes the pause/resume plumbing and the dict-keyed result shape, not the policy. This is exactly the seam flagged as the open question in `project_drop_capability_api`. **Do not "simplify" these types** — whether co re-implements pause/resume around its own `call_tool` (it already owns it) is a design decision belonging to that plan, not a mechanical cleanup. `ToolApproved()` has exactly one call site (clarify) — preserve that case if the protocol is reworked.

### 2.5 Model wrapping + factories — ✅ justified; the JSON-repair seam is here, not in the capability API

**Verified (surrogate_recovery_model.py):** `SurrogateRecoveryModel(WrapperModel)` (193) overrides `request` (200–243) and `request_stream` (245–291) for three concerns, all at the model boundary co already owns: (1) `UnicodeEncodeError` sanitize-retry (219–225, 273–287); (2) `kind="model"` chat span push/pop (206–213, 232–242, `_close_model_span`); (3) **JSON arg repair** gated to Ollama via `repair_tool_args` — non-stream repairs the `ModelResponse` directly (229–230), streaming wraps it in `_RepairingStreamedResponse` (270, 285). `factory.py` builds `OpenAIChatModel(provider=OllamaProvider(...))` or `GoogleModel(provider=GoogleProvider(...))`, then wraps in `SurrogateRecoveryModel`. `config/llm.py` translates inference dicts → `ModelSettings` / `GoogleModelSettings`.

**Assessment:** The cleanest seam co has — the two ugliest Ollama realities (surrogates, malformed tool-arg JSON) are isolated in one `WrapperModel` instead of polluting the agent loop. **Decisive for the capability-API decision:** JSON repair lives entirely in a `WrapperModel`, independent of any capability SDK. Dropping the capability API does **not** disturb JSON repair. (Caveat → §3.3.)

---

## 3. Findings, ranked by refactor leverage

### 3.1 🔴 `bootstrap/schema_budget.py` — most SDK-internal-coupled module (verified, #1)

Two coupling defects in one ~100-line module:

**(a) Private `RunContext` construction (schema_budget.py:23, 82):**
```python
from pydantic_ai._run_context import RunContext        # private module
...
ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_name=name)  # type: ignore[arg-type]
tdef = await tool.prepare_tool_def(ctx)
```
**Why it exists (verified):** `measure_always_schema_budget` measures the ALWAYS-visibility prefill bucket = `len(name) + len(description) + len(minified parameters_json_schema)` per tool. **`ToolInfo` (deps.py:100–115) carries NO `parameters_json_schema` field** — the schema is generated by pydantic-ai from the function signature and is only obtainable via `tool.prepare_tool_def(ctx)`, which also respects per-turn `prepare` callbacks. So the synthetic `RunContext` is genuinely required.

> **v1 correction:** v1 proposed "measure from co's own metadata and delete the RunContext." **Not viable** — co has no schema metadata. Discard that option.

**Fix (low effort, high value):** change the import to the public re-export — `from pydantic_ai import RunContext` (every other module already does this; it is the same class). The `# type: ignore[arg-type]` for `model=None` stays, but the dependency on the private `_run_context` module is gone.

**(b) `_unwrap_function_toolset` topology heuristic (schema_budget.py:39–62) — v1 MISSED THIS:**
```python
for _ in range(12):
    if hasattr(inner, "tools") and isinstance(inner.tools, dict): return inner
    if hasattr(inner, "toolsets"):  # CombinedToolset
        for sub in inner.toolsets:
            cur = sub
            for _ in range(8):
                if hasattr(cur, "tools") ...: return cur
                cur = getattr(cur, "wrapped", None)   # WrapperToolset chain
    inner = getattr(inner, "wrapped", None)
```
A 12-deep × 8-deep heuristic that hard-codes the SDK's internal toolset-chain shape (`.tools` / `.toolsets` / `.wrapped` of FilteredToolset / CombinedToolset / WrapperToolset). If pydantic-ai changes its composition internals, this silently returns the wrong toolset or `None` (→ `RuntimeError` at 75). This is the single most version-fragile structure in the tree.

**Fix:** have `assemble_routing_toolset` (core.py) **return the inner `FunctionToolset` (or its `.tools` dict) alongside** the assembled toolset, and store it on `deps` (next to `deps.toolset`). Then `measure_always_schema_budget` reads the known reference directly and the heuristic walk is **deleted**. This removes co's only reach into the SDK's toolset-composition internals. Pairs naturally with (a) — both live in this one module.

**Also (b-adjacent):** `from pydantic_ai.result import RunUsage` here is the only non-canonical `RunUsage` import; normalize to `pydantic_ai.usage`.

### 3.2 🟠 Inconsistent import paths for the same symbol

- `RunContext`: `pydantic_ai` (most sites) vs `pydantic_ai._run_context` (schema_budget.py:23).
- `RunUsage`: `pydantic_ai.usage` (run.py, orchestrate.py) vs `pydantic_ai.result` (schema_budget.py:24).

**Fix:** canonicalize to `pydantic_ai` / `pydantic_ai.usage` everywhere. Trivial, zero-risk, folds into §3.1.

### 3.3 🟠 JSON-repair stream proxy depends on a private agent-graph internal

`_RepairingStreamedResponse.get()` (surrogate_recovery_model.py:159–162) repairs the assembled response because — per its docstring (146–148) — "the agent graph validates and dispatches tools from `StreamedResponse.get()` (`_agent_graph.py` `_streaming_handler`)." The repair's *correctness* is pinned to that **private** internal behavior. It is a comment-level assumption (no import), so it won't break the build, but a change to where the SDK validates streamed tool args would silently bypass repair on the streaming path.

**Action (document, don't refactor):** keep the seam — it is correct today and there is no public alternative. Add a regression eval that asserts a malformed-JSON tool call is repaired *on the streaming path* (not just non-stream), so a future SDK change surfaces as a test failure rather than silent Ollama breakage. Relevant to `drop-capability-api`: this assumption sits in the model wrapper and is **orthogonal** to the capability API.

### 3.4 🟡 Message-part type-set spread across context modules

Covered in §2.2. Breadth is justified (client-side compaction); copy-paste duplication is narrow (≈4 sites, mostly within-file). **Optional** thin `_parts.py` (type-set constants + `msg_starts_turn` + `part_kind_label`); bundle with other part work. Down-ranked from v1's "highest-leverage."

### 3.5 🟡 Orchestrator output type declared `Any`

build.py:40 `agent: Agent[CoDeps, Any]` with `output_type=[str, DeferredToolRequests]`, while `main.py` / `commands/types.py` hint `Agent[CoDeps, str | DeferredToolRequests]`. **Fix:** declare a shared alias `type SessionAgent = Agent[CoDeps, str | DeferredToolRequests]` (one already exists in orchestrate.py:90) and use it in `build_orchestrator`'s return type. Leave `build_task_agent` as `Any` (its `output_type` is genuinely variable per spec).

### 3.6 🟡 Ceremony / dead code

- `orchestrate.py:387` passes `UsageLimits(request_limit=None)` — no limit. Either drop the arg (if `None` is the SDK default) or comment *why* the orchestrator is intentionally unbounded. (`run.py:64` sets a real limit for task agents — keep.)
- ~~`observability/serialize.py:21` imports `SystemPromptPart` but never uses it~~ — **resolved (2026-06-08):** the import is gone; `serialize.py`'s part imports are now `ModelResponse, ModelResponsePart, TextPart, ThinkingPart, ToolCallPart, UserPromptPart` only, and the serializer falls back via `part_kind` for non-enumerated parts (43, 68).

---

## 4. Coding-practice assessment (cross-cutting)

What the SDK integration reveals about the surrounding code, good and bad — relevant because a refactor should preserve the former and fix the latter.

**Strong practices to preserve:**
- **Pure, non-mutating history processors.** Every processor returns a new list; `_rewrite_tool_returns` rebuilds a `ModelRequest` via `replace(...)` *only when a part changed* (history_processors.py:126), else preserves the original object. `_sanitize_structure` returns `(payload, False)` unchanged so downstream change-detection stays identity-cheap (539–567). This is disciplined and correct.
- **Single rewrite contract.** dedup / evict / spill / strip all funnel through `_rewrite_tool_returns` with a `replacement_for` callback — the "boundary-protected, non-mutating" invariant holds by construction, not by convention.
- **Docstrings carry *why* + peer citations** (e.g. `COMPACTABLE_KEEP_RECENT` cites `fork-claude-code/...timeBasedMCConfig.ts:33` and explicitly notes non-convergence across peers). Boundary-planner invariants (`_MIN_RETAINED_TURN_GROUPS`) are documented as correctness constraints, not magic numbers.
- **Frozen metadata.** `ToolInfo` / `AlwaysSchemaBudget` / `MCPToolsetEntry` are frozen dataclasses set once at registration.
- **Thin proxies over deep subclassing.** `_SanitizingMCPServer` and `_RepairingStreamedResponse` delegate everything via `__getattr__` and override exactly one method — minimal surface against SDK internals.

**Risk practices a refactor should address:**
- **Topology duck-typing** (`_unwrap_function_toolset`, §3.1b) — the worst instance; reaches blindly into SDK toolset internals. Replace with an explicit reference handed down from assembly.
- **String-based part fallbacks** — `getattr(msg, "kind", "request")` (serialize.py:34) and `getattr(part, "part_kind", part.__class__.__name__)` (43, 68) duck-type instead of `isinstance`. Defensive, but signals the part taxonomy isn't fully trusted; the optional `_parts.py` (§3.4) would make the fallback intentional and single-sourced.
- **`id(part)` identity tracking across processor passes** (history_processors.py:225, 311, 366, 494) — `_build_keep_ids` / spill key replacements by `id(part)`, assuming pydantic-ai passes the *same* part objects to each processor within a request. True today; if the SDK ever deep-copies messages between processors this breaks silently. Worth a one-line invariant comment at each `id(part)` use.
- **Private import + `# type: ignore[arg-type]` + `model=None`** (bootstrap/schema_budget.py:82) — three smells stacked on one line; §3.1 removes the private import and the construction can stay localized.
- **Minor import ordering** — history_processors.py places `log = logging.getLogger(__name__)` (30) *before* the `pydantic_ai` imports (32–44). `from __future__` is correctly first; the stray code-before-import is cosmetic but unusual for this codebase.

---

## 5. Recommended refactor sequence (priority order)

1. **De-couple `bootstrap/schema_budget.py` from SDK internals (§3.1).** (a) public `RunContext` import; (b) thread the inner `FunctionToolset` reference from `assemble_routing_toolset` onto `deps` and **delete `_unwrap_function_toolset`**; (c) normalize `RunUsage` to `pydantic_ai.usage`. *Single module, removes the only private-module dependency and the only topology heuristic — highest leverage.*
2. **Canonicalize import paths repo-wide (§3.2).** Trivial; folds into step 1.
3. **Add a streaming-path JSON-repair regression eval (§3.3).** Pins the private agent-graph assumption to a test.
4. **Declare the orchestrator output type once (§3.5)** and **clean ceremony (§3.6)** — `Any` → `SessionAgent`, comment/drop `UsageLimits(None)`. (The unused `SystemPromptPart` import is already removed.)
5. **Optional: thin `_parts.py` (§3.4)** — only alongside other part work; modest payoff.
6. **Do NOT touch:** the approval-protocol types (§2.4 — `drop-capability-api` plan owns them), the two `WrapperToolset` subclasses (§2.3 — retraction stands), `SurrogateRecoveryModel` (§2.5), `ModelMessagesTypeAdapter`, `_rewrite_tool_returns`.

Steps 1–2 are mechanical and high-value. Step 3 is a test. Step 4 is cosmetic. Step 5 is opportunistic. Steps in 6 are design-coordinated or deliberately on-design.
