# Plan: Capability Self-Check Surface

Task type: code-feature

## Context

`co-cli` already exposes an always-visible tool named `capabilities_check`, but the
current implementation is much closer to a `/doctor` diagnostic helper than a true
agent self-check mechanism.

Code review findings that drive this plan:

1. The general agent prompt does not tell the model to use `capabilities_check` when
   asked what it can do or whether a capability is available. The current tool
   protocol only steers the model toward `search_tools` when a needed capability is
   not visible.
2. `capabilities_check` is explicitly coupled to the bundled `/doctor` skill and is
   described in code/specs as a "runtime doctor", not as the canonical self-check
   surface.
3. The model only sees `ToolReturn.return_value`; `ToolReturn.metadata` is app-side
   only (`co_cli/tools/tool_io.py`). The current tool puts most of the structured
   state into metadata, which limits its usefulness for in-turn self-assessment.
4. The current runtime snapshot misses the most important degraded-mode data:
   bootstrap-recorded `deps.degradations`. It also overstates MCP health and has at
   least one concrete correctness bug in source counting (`ToolSourceEnum` compared to
   string literals).

This creates a mismatch with the intended product goal:

- desired: Co can check its own runtime capability surface during a normal turn
- actual: Co has a doctor tool mainly used by `/doctor`

## Naming Decision

**Public tool name:** keep `capabilities_check`

Reasoning:

1. It already conforms to the shipped domain-prefix naming convention introduced by
   the tool-surface rename work (`capabilities_*` domain, `check` verb).
2. The name is broad enough for the intended final role: "check my current
   capabilities".
3. Renaming again would create churn without solving the underlying mismatch, which is
   semantic and prompt-placement driven, not naming driven.

**Constraint:** if implementation splits the current behavior into a lower-level
runtime helper plus the public agent-facing tool, only the internal helper may be
renamed. The public model-visible tool remains `capabilities_check`.

## Problem & Outcome

**Problem:** `capabilities_check` is not currently the canonical capability-awareness
mechanism. It is not reliably invoked for ordinary self-assessment prompts, its
display payload is not optimized for model consumption, and its health/fallback
reporting is incomplete enough to mislead the model.

**Outcome:** `capabilities_check` becomes the authoritative model-visible self-check
tool:

1. The agent can answer plain-language prompts such as "what can you do right now?",
   "can you access Google Drive?", or "check whether you have note access" by calling
   `capabilities_check` in a normal turn.
2. `/doctor` remains available as an optional user convenience alias, not as the only
   path to runtime introspection.
3. The tool returns a compact but model-usable display contract that includes the
   current tool surface, active degradations, configured-but-unavailable integrations,
   and approval-gated capabilities.
4. The app-side metadata mirrors the same facts for tracing, tests, and UI use.
5. The tool no longer overclaims MCP or reasoning readiness, and it surfaces
   bootstrap degradations as first-class fallbacks.

## Scope

**In:**

- `co_cli/bootstrap/check.py`
- `co_cli/tools/capabilities.py`
- `co_cli/prompts/rules/04_tool_protocol.md`
- `co_cli/skills/doctor.md`
- `tests/test_capabilities.py`
- `tests/test_tool_prompt_discovery.py`
- any small supporting test file updates needed for prompt/tool assertions
- optional focused eval under `evals/` if the team wants model-behavior verification

**Out:**

- no new slash command
- no removal of `/doctor`
- no MCP live-refresh work (`tools/list_changed`, dynamic re-discovery)
- no redesign of `search_tools`
- no spec-file edits as task inputs; `/sync-doc` owns spec updates after delivery

## Behavioral Constraints

1. `capabilities_check` stays `ALWAYS`, read-only, and concurrent-safe.
2. `capabilities_check` remains the public tool name; do not add an alias or second
   public self-check tool.
3. The model-visible `return_value` must contain every decision-critical fact needed
   for self-assessment. Metadata may mirror it, but metadata is not enough.
4. Report three distinct states where relevant:
   - registered/available to the agent
   - discoverable later via `search_tools`
   - unavailable or degraded, with reason
5. Never label an MCP server as "connected" unless the implementation is using a real
   connection/discovery fact. PATH-present or URL-configured is not "connected".
6. Active degradations from `deps.degradations` must appear in the self-check result.
   This includes knowledge fallback and MCP discovery failures.
7. `reasoning_ready` must mean "runtime usable", not merely "a model name string is
   configured".
8. `/doctor` remains a thin UX shortcut. Plain-language asks about capability must
   work without `/doctor`.
9. Keep the tool output compact enough to fit comfortably in context. The full
   37-tool surface may be listed, but the display should group tools by visibility or
   domain rather than dumping an unstructured blob.

## High-Level Design

### 1. Reframe `capabilities_check` as the canonical self-check tool

The public semantics change from:

- "runtime doctor for `/doctor`"

to:

- "show what Co can do right now, what is gated, and what is degraded"

This is not a second tool. It is a semantic tightening of the existing one.

### 2. Separate model-visible display from app-side metadata

Because `tool_output(...metadata=...)` is not sent to the LLM, the display text must
carry the usable contract.

Target display shape:

```text
Capability summary:
- Available now: <always-visible tools or domains>
- Discoverable on demand: <deferred tools or domains>
- Approval-gated: <tools that prompt or conditionally prompt>
- Unavailable or limited: <component -> reason>
- Active fallbacks: <degradation list, or none>
```

Required model-visible facts:

1. current always-visible tool set
2. current deferred/discoverable tool set
3. approval-gated tools
4. configured integration status:
   - provider
   - knowledge backend
   - Google
   - Obsidian
   - Brave/web search
   - MCP
5. unavailable/degraded reasons
6. active fallback list

Metadata should mirror the same information in structured form for tests/UI, e.g.:

- `always_visible_tools`
- `deferred_tools`
- `approval_required_tools`
- `conditional_approval_tools`
- `component_status`
- `degradations`
- `fallbacks`
- `source_counts`

### 3. Expand `check_runtime()` to produce runtime-truth, not just doctor prose

`check_runtime()` should become the single source of truth for the self-check tool.
It should assemble:

1. **Tool surface**
   - `always_visible_tools`
   - `deferred_tools`
   - `tool_approvals`
   - source counts using `ToolSourceEnum` correctly

2. **Component status**
   - provider/model availability
   - Google / Obsidian / Brave
   - knowledge backend runtime mode
   - skills loaded
   - MCP server states

3. **Degradations**
   - direct copy of `deps.degradations`
   - normalized user/model-facing fallback strings derived from it

4. **Session/runtime context**
   - active skill
   - session id suffix
   - runtime knowledge mode

Implementation rule: the helper may keep internal dataclasses, but the result shape
must clearly distinguish:

- "tool exists in current agent surface"
- "integration configured but unhealthy"
- "integration not configured"
- "fallback active"

### 4. Tighten MCP semantics

The current implementation conflates:

- config present
- command on PATH / URL configured
- server actually discovered successfully

The self-check surface must not blur these together.

Recommended MCP summary contract:

```text
MCP:
- configured servers: N
- discovered tools: M
- degraded servers: <name -> reason>   # from deps.degradations["mcp.*"]
- native-only fallback: yes/no
```

If the implementation cannot prove a server is live, use language like:

- `configured`
- `command found`
- `url configured`
- `tool discovery failed`

Do not say `connected` unless that fact exists at runtime.

### 5. Keep `/doctor` as an alias-like workflow, not a separate capability system

`/doctor` should remain user-invocable because it is a good explicit UX affordance for
troubleshooting. But its body should be treated as a convenience wrapper around the
same canonical self-check tool.

Desired behavior:

- plain-language ask: model chooses `capabilities_check`
- `/doctor`: skill explicitly instructs the model to call `capabilities_check` and
  summarize findings in the doctor format

No separate built-in slash command is needed.

### 6. Add explicit prompt steering for self-capability questions

The static tool protocol should tell the model:

- when asked what capabilities are available right now
- when asked whether it can use a specific integration/tool
- when asked why an expected capability is unavailable

call `capabilities_check` before answering.

This guidance belongs next to the existing `search_tools` discovery rule, because the
two behaviors complement each other:

- `capabilities_check` answers "what do I have right now?"
- `search_tools` answers "what additional deferred tools can I load?"

## Implementation Plan

### ✓ DONE — TASK-1 — Rebuild the runtime snapshot around self-check truth

**files:**
- `co_cli/bootstrap/check.py`
- `co_cli/tools/capabilities.py`

**done_when:**

1. `check_runtime()` returns structured data that distinguishes:
   - always-visible tools
   - deferred/discoverable tools
   - approval-gated tools
   - unavailable/degraded components
   - active fallbacks
2. `deps.degradations` is surfaced directly in the result and normalized into
   user/model-facing fallback strings. The existing hardcoded `"mcp: native-only ..."`
   entry at `check.py:545-547` is replaced by a `deps.degradations`-driven list; do not
   keep both sources.
3. `ToolSourceEnum` is used correctly for source counting and tool totals. Concretely,
   `capabilities.py:66-67` currently compares `tc.source` (enum) to string literals,
   which silently zeroes both counts and makes the MCP display read
   `"... · 0 tools"` regardless of how many MCP tools are discovered. Fix by using
   `tc.source.value` or the enum member, matching `check.py:516`.
4. `reasoning_ready` is tied to actual provider/model health. Concretely, set
   `reasoning_ready = provider_result.ok` (the probe already computed at
   `check.py:497-501`), not `bool(deps.config.llm.model)`. Do not introduce a new probe
   — reuse the existing one.
5. MCP wording is corrected so the tool never claims "connected" based only on PATH or
   URL checks. Preserve the existing per-probe loop (`capabilities.py:83-85`), which is
   useful detail; replace the summary-line wording "servers connected" with
   `configured` / `command found` / `url configured` per each probe's actual evidence,
   and add a `degraded servers: <name -> reason>` line sourced from
   `deps.degradations["mcp.*"]`.
6. `capabilities_check()` renders a compact model-visible summary with grouped sections:
   available now, discoverable on demand, approval-gated, unavailable/limited, active
   fallbacks.
7. Metadata mirrors the display contract in structured form.
8. The module docstring at `capabilities.py:1-5` is updated to reflect the reframed
   semantics. It currently reads "Capability introspection tool for the /doctor skill."
   — rewrite to frame the tool as the canonical self-check surface, with `/doctor` as
   one consumer among others.

**success_signal:** running `capabilities_check` on a degraded runtime yields a display
that explicitly names the degraded component and fallback reason in plain text, without
requiring metadata inspection.

### ✓ DONE — TASK-2 — Make self-check part of ordinary agent behavior

**files:**
- `co_cli/prompts/rules/04_tool_protocol.md`
- `tests/test_tool_prompt_discovery.py`

**done_when:**

1. The tool protocol contains explicit guidance to call `capabilities_check` when the
   user asks about:
   - available capabilities
   - whether a specific tool/integration is usable
   - why a capability is unavailable or degraded
2. Existing `search_tools` guidance remains intact and is not weakened.
3. Static prompt tests assert that both `search_tools` guidance and
   `capabilities_check` self-check guidance are present.

**success_signal:** `build_static_instructions(settings)` includes both the deferred
discovery rule and the self-check rule.

### ✓ DONE — TASK-3 — Recast `/doctor` as a convenience workflow

**files:**
- `co_cli/skills/doctor.md`

**done_when:**

1. The skill still tells the model to call `capabilities_check`.
2. The skill wording makes it clear that doctor is a troubleshooting workflow layered
   on top of the canonical self-check tool, not a distinct capability system.
3. The skill output contract remains concise and diagnosis-focused.

**success_signal:** `/doctor` still works as before from a user perspective, but the
skill text no longer implies that capability introspection only exists inside doctor.

### ✓ DONE — TASK-4 — Add focused tests for the corrected contract

**files:**
- `tests/test_capabilities.py`
- `tests/test_agent.py`

**done_when:**

Add or update tests covering:

1. `capabilities_check` display contains self-check sections in model-visible text.
2. `deps.degradations` appears in the tool result when present.
3. MCP reporting does not use false "connected" wording for mere PATH/URL probes.
4. source counts use `ToolSourceEnum` correctly.
5. `reasoning_ready` follows runtime provider health semantics.
6. `capabilities_check` remains always-visible in the tool registry.

**success_signal:** `uv run pytest tests/test_capabilities.py tests/test_agent.py -x`
passes with assertions covering the new contract.

### TASK-5 — Optional model-behavior eval

**files:**
- `evals/eval_capability_self_check.py` or an appropriate existing eval file

**done_when:**

If the team chooses to add an eval, it should exercise real orchestration behavior with
prompts such as:

1. "What tools do you have available right now?"
2. "Can you access my Obsidian vault?"
3. "Why can't you use Google Drive here?"
4. "Check your capabilities before deciding how to proceed."

Pass condition: the model calls `capabilities_check` before answering in the relevant
cases.

**success_signal:** at least one production-path eval demonstrates that a plain-language
self-capability question triggers the tool without `/doctor`.

## Testing

Required during implementation:

```bash
mkdir -p .pytest-logs
uv run pytest tests/test_capabilities.py tests/test_agent.py tests/test_tool_prompt_discovery.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-capability-self-check.log
```

Optional if TASK-5 is taken:

```bash
uv run python evals/eval_capability_self_check.py
```

## Migration / Compatibility Notes

1. No public rename is performed. The public tool name stays `capabilities_check`.
2. `/doctor` is retained.
3. The tool output text will change shape. That is acceptable because the only known
   consumer is the model itself plus tests/spec prose; there is no stable external API
   contract documented for the exact display formatting.
4. App-side metadata field names may change if needed, but tests must pin the final
   shape to prevent future drift.

## Open Questions

1. Should the model-visible display list every tool name explicitly, or summarize by
   domain and visibility with a smaller selected list?

Proposed answer:
list all tool names, but grouped compactly:
- always-visible tools
- deferred/discoverable tools
- approval-gated tools

The native surface is small enough that the full grouped list is still cheap, and it
removes ambiguity for self-assessment prompts.

2. Should `/doctor` remain a skill or become a built-in slash command?

Proposed answer:
keep it as a skill. The core need is the model-visible tool, not a local-only command.
The existing skill already provides the right UX shape as a convenience wrapper.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev capability-self-check-surface`

---

## Delivery Summary — 2026-04-23

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | structured runtime data, degradations→fallbacks, enum source counts, reasoning_ready tied to provider.ok, MCP wording evidence-based, grouped display, module docstring reframed | ✓ pass |
| TASK-2 | self-check guidance added to `04_tool_protocol.md`; static-prompt test asserts both `search_tools` and `capabilities_check` rules present | ✓ pass |
| TASK-3 | `/doctor` skill now frames itself as a triage workflow layered on `capabilities_check`; output contract preserved | ✓ pass |
| TASK-4 | new tests cover grouped display sections, degradations surfacing, evidence-based MCP wording, enum-correct source counts, reasoning_ready ↔ provider.ok invariant | ✓ pass |
| TASK-5 | optional eval | — skipped |

**Tests:** full suite — 680 passed (251s). One transient timeout on first run (`test_circuit_breaker_probes_at_cadence` — unrelated, KV-cache churn under load); passed cleanly on re-run and in isolation (7.19s).
**Doc Sync:** narrow scope on `tools.md`, `skills.md`, `prompt-assembly.md` — 1 fix in `tools.md:130` (stale `capabilities_check` description). `skills.md` and `prompt-assembly.md` clean.

**Team dynamics:** Two Dev subagents were dispatched for TASK-2 and TASK-3 in parallel; both were blocked by permission denials on their `Edit` calls. TL executed all four tasks directly. Noted for future runs — subagent edits may require the user to pre-approve Edit permission on the target paths.

**Coverage note for TASK-4 item 6** (`capabilities_check` remains ALWAYS-visible): already covered by `test_tool_index_visibility_policy_metadata` in `tests/test_agent.py:86-114`. No new duplicate test added.

**Overall: DELIVERED**
Canonical self-check surface is in place: `capabilities_check` produces a grouped, model-legible summary (available now / discoverable / approval-gated / unavailable / active fallbacks), steering prompt rule is live, `/doctor` is reframed as a wrapper workflow, and five new tests pin the corrected contract. Spec and source docstrings are in sync.

---

## Implementation Review — 2026-04-23

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | structured runtime data distinguishes always/deferred/approval-gated/unavailable/fallbacks; `deps.degradations` → fallbacks; `ToolSourceEnum` used correctly; `reasoning_ready` tied to `provider_result.ok`; MCP wording evidence-based; grouped display; module docstring reframed | ✓ pass | `check.py:542-546` (tool_groups), `check.py:587-594` (fallbacks from deps.degradations, no hardcoded `mcp: native-only` entry), `check.py:532` (`tc.source.value`), `capabilities.py:153-154` (`ToolSourceEnum.NATIVE/MCP`), `check.py:519` (`reasoning_ready=provider_result.ok`), `capabilities.py:34-45` + `:117-133` (MCP wording, grep confirms no `"connected"` in body), `capabilities.py:48-77` (grouped display sections), `capabilities.py:1-9` (docstring reframed to canonical self-check surface) |
| TASK-2 | self-check guidance in `04_tool_protocol.md`; `search_tools` guidance intact; static prompt tests assert both | ✓ pass | `04_tool_protocol.md:57-62` (Deferred discovery), `:64-69` (Capability self-check); `tests/test_tool_prompt_discovery.py:7-29` — both assertions pass |
| TASK-3 | `/doctor` skill still calls `capabilities_check`; wording positions doctor as workflow on top of canonical tool; output contract preserved | ✓ pass | `doctor.md:7-9` (framing), `:15-25` (preserved diagnosis format) |
| TASK-4 | tests cover grouped sections, degradations, evidence-based MCP wording, enum source counts, reasoning_ready semantics, ALWAYS visibility | ✓ pass | `tests/test_capabilities.py:132-148, 151-174, 177-190, 193-220, 223-237`; ALWAYS visibility already covered at `tests/test_agent.py:106-112` |

Cold re-read confirmed every passing claim. No stale imports or dead code introduced by the delivery. Lint clean on first pass.

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale `__pycache__/test_tool_calling_functional.cpython-312.pyc` contained old test code (pre-naming-rename) causing `test_tool_selection_and_arg_extraction[search_knowledge_db]` failure in first full-suite run | `tests/__pycache__/*.pyc` | blocking-for-gate | Cleared all `__pycache__` dirs under repo (excluding `.venv`); full suite re-run went 681/681 green. Not a production code defect — local staleness only. |
| Progress strings still say `"Doctor: ..."` in `capabilities_check` even though tool is now the canonical self-check (pinned by `test_capabilities_emits_doctor_progress_updates`) | `check.py:472-494`, `capabilities.py:145` | minor | Not fixed — outside task done_when, pinned by intentional test, cosmetic only |
| Test file terminology still calls workflow "Doctor tool" (section comment + one docstring) | `tests/test_capabilities.py:23, 36` | minor | Not fixed — cosmetic drift, no functional impact |

### Tests
- Command: `uv run pytest` (full suite)
- Result: **681 passed, 0 failed in 227.68s**
- Log: `.pytest-logs/20260329-133210-review-impl-full.log`
- Earlier run hit one false failure from stale `__pycache__`; diagnosed to cached pyc, cleared, re-ran clean.

### Doc Sync
- Scope: narrow — changes touch `co_cli/bootstrap/check.py`, `co_cli/tools/capabilities.py`, `co_cli/prompts/rules/04_tool_protocol.md`, `co_cli/skills/doctor.md` and a test file. No shared modules renamed, no public API or schema changes beyond what delivery already synced.
- Result: clean. Delivery already updated `docs/specs/tools.md:130` (new `capabilities_check` row); `skills.md` and `prompt-assembly.md` had no references to fix. Grep confirmed no other spec references `capabilities_check` or `/doctor`.

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM online, Shell active, Google/Brave configured, MCP `context7` ready.
- Smoke-rendered `capabilities_check` against a degraded runtime (`deps.degradations["knowledge"] = "…grep (embedder unavailable)"`):
  ```
  Capability summary:
    Available now: …
    Discoverable on demand: …
    Approval-gated: …
  Unavailable or limited:
    - knowledge — grep mode
  Active fallbacks:
    - knowledge: sqlite-fts → grep (embedder unavailable)
  …
  MCP:
    configured servers: 1 · discovered tools: 0
    - context7: command found: npx
  ```
  Confirms `success_signal` for TASK-1: degraded component and fallback reason are both named in plain text without metadata inspection. MCP wording uses `command found:` (evidence-based), never `"connected"`. `success_signal` for TASK-2 verified by `build_static_instructions` test asserting both `search_tools` and `capabilities_check` guidance.

### Overall: PASS
Four `✓ DONE` tasks verified end-to-end. Full test suite green (681/681), lint clean, behavioral verification confirms the grouped self-check display renders correctly with degraded state. No blocking findings survived adversarial self-review. Ready to ship.
