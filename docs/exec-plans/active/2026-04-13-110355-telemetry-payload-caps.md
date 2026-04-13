# TODO: Telemetry Payload Caps and Spill References

**Slug:** `telemetry-payload-caps`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research: [RESEARCH-otel-compare-fork-cc-and-co.md](reference/RESEARCH-otel-compare-fork-cc-and-co.md), [DESIGN-observability.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-observability.md), [DESIGN-context.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-context.md)

Sequencing:
- This task follows shipment of [TODO-tail-detail-tree-modes.md](/Users/binle/workspace_genai/co-cli/docs/TODO-tail-detail-tree-modes.md).
- Reason: ROI 1 should land first so terminal inspection semantics stabilize before changing what payloads get written to telemetry.

Current-state validation against the latest code:
- `SQLiteSpanExporter` currently serializes span `attributes`, `events`, and `resource` directly with `json.dumps(...)` and writes them to SQLite without export-side truncation, spill, or redaction in [co_cli/observability/_telemetry.py](/Users/binle/workspace_genai/co-cli/co_cli/observability/_telemetry.py).
- The highest-volume user-content payloads currently stored in spans are:
  - `gen_ai.input.messages`
  - `gen_ai.output.messages`
  - `gen_ai.tool.call.arguments`
  - `gen_ai.tool.call.result`
- `co tail` and `co traces` already read and render those attributes directly from SQLite in [co_cli/observability/_tail.py](/Users/binle/workspace_genai/co-cli/co_cli/observability/_tail.py) and [co_cli/observability/_viewer.py](/Users/binle/workspace_genai/co-cli/co_cli/observability/_viewer.py).
- `co` already has a proven oversized-content pattern for tool results:
  - per-tool `max_result_size` metadata in [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
  - `tool_output()` spill enforcement in [co_cli/tools/tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)
  - content-addressed persistence in [co_cli/tools/tool_result_storage.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_result_storage.py)
- There is currently no telemetry-specific test file covering export-time payload limiting.

Artifact hygiene:
- No current TODO owns exporter-side telemetry payload caps.
- This task is intentionally separate from terminal tail rendering and from telemetry retention/pruning.

---

## Problem & Outcome

Problem: telemetry is local-first, but not bounded. The exporter currently stores the full payload for large model messages and tool payloads, which creates three concrete costs:
- SQLite growth from a small set of oversized spans
- noisy tail/detail/trace views when a single attribute dominates the record
- a mismatch with the rest of `co`, which already spills oversized tool output by reference instead of keeping everything inline

Failure cost:
- one long model exchange can flood the telemetry DB with repeated full message JSON
- `co tail --detail` and `co traces` become less useful because payload volume overwhelms structure
- later retention work becomes harder because the store is already inflated by avoidable duplicate bulk content

Outcome: add export-boundary payload caps for a narrow set of high-volume attributes, with spill-to-disk references and short previews instead of full inline storage. The result should preserve local debugging value without silently deleting content or introducing a remote telemetry architecture.

---

## Scope

In scope:
- add export-time payload limiting for selected high-volume telemetry attributes
- adapt the existing content-addressed spill-by-reference pattern for telemetry payloads
- keep the SQLite schema unchanged if possible
- add tests for exporter behavior and viewer compatibility with spilled placeholders

Out of scope:
- telemetry retention or pruning
- remote telemetry export or analytics fanout
- a full redaction-policy matrix
- changing the meaning of tail/detail/tree modes
- generic limiting for every span attribute, event, and resource field

---

## Behavioral Constraints

- This task must preserve `co`'s local-debugging value. Do not replace large payloads with opaque `[TRUNCATED]` markers when a preview + file reference can be shown instead.
- Start with a narrow allowlist of capped attributes:
  - `gen_ai.input.messages`
  - `gen_ai.output.messages`
  - `gen_ai.tool.call.arguments`
  - `gen_ai.tool.call.result`
- Do not silently change trace structure, span names, or the SQLite table schema in v1.
- Keep the spill representation deterministic and content-addressed, following the same spirit as oversized tool-result storage.
- Prefer code constants over a new user-facing settings surface in v1. This is a bounded safety feature, not a new product configuration area.
- Under-threshold payloads must remain unchanged.
- Over-threshold payloads must remain inspectable:
  - include original size
  - include a stable file path reference
  - include a bounded preview
- The new placeholder format must remain readable in `co tail --detail` and `co traces` without requiring a full viewer redesign.
- Do not fold telemetry retention into this TODO. That is a separate follow-on task.

---

## High-Level Design

### 1. Add a telemetry-specific spill helper

`co` already has a good pattern for oversized tool output. Reuse the design, not necessarily the exact helper boundary.

Recommended direction:
- add a telemetry-focused helper under `co_cli/observability/`
- keep content-addressed persistence and preview generation
- use a dedicated telemetry payload directory under the user-global state root

This keeps telemetry semantics separate from tool-return semantics while still borrowing the proven persistence model.

### 2. Apply a targeted export policy, not blanket mutation

The exporter should sanitize only the known high-volume attributes before `json.dumps(...)`.

Recommended v1 policy:
- leave all other attributes untouched
- cap only the four target attributes listed above
- keep `events` and `resource` unchanged unless a concrete oversized case appears later

That keeps the blast radius small and makes regressions easier to reason about.

### 3. Preserve inspectability with placeholders, not deletion

When a target attribute exceeds its threshold:
- persist the full payload to disk
- replace the inline attribute value with a compact placeholder string
- include enough preview text for quick tail/trace inspection

This should mirror the existing “preview + file path” operator experience used for oversized tool results.

### 4. Keep viewer changes minimal and compatibility-focused

The primary change belongs in the exporter. Viewer work should only ensure that the new placeholder remains readable.

Recommended v1:
- no new viewer mode
- no schema migration
- only minimal formatting adjustments if the placeholder renders poorly in detail/tree/HTML views

---

## Implementation Plan

## TASK-1: Define telemetry payload limit policy and persistence helper

files: `co_cli/observability/_telemetry.py`, `co_cli/observability/_payload_limits.py`

Implementation:
- Introduce a small telemetry payload-limits helper module.
- Define the target attribute allowlist and per-attribute thresholds as code constants.
- Add content-addressed persistence for oversized telemetry payloads with preview + file-reference placeholders.
- Use a dedicated telemetry payload directory under user-global state rather than mixing with project-local tool results.

done_when: |
  telemetry payload limiting is defined in one explicit helper module rather than being scattered across the exporter;
  the target keys and thresholds are obvious from code
success_signal: there is one canonical spill/cap policy for oversized telemetry payloads
prerequisites: []

## TASK-2: Apply export-time sanitization in SQLiteSpanExporter

files: `co_cli/observability/_telemetry.py`, `tests/test_telemetry_payload_limits.py`

Implementation:
- Sanitize targeted span attributes before writing rows to SQLite.
- Leave non-target attributes unchanged.
- Keep the `spans` table schema and query shape unchanged.
- Ensure under-threshold values are written verbatim and over-threshold values are replaced with stable spill placeholders.

Coverage targets:
- under-threshold model message payloads stay inline
- oversized `gen_ai.input.messages` spills to disk and stores a readable placeholder
- oversized `gen_ai.output.messages` spills to disk and stores a readable placeholder
- oversized tool arg/result payloads spill to disk and store readable placeholders
- exporter still writes valid rows to SQLite with the same schema

done_when: |
  SQLiteSpanExporter limits only the intended high-volume attributes and leaves the rest of the span intact;
  DB rows remain readable and queryable after the change
success_signal: large telemetry payloads no longer inflate the SQLite DB inline by default
prerequisites: [TASK-1]

## TASK-3: Verify tail/trace compatibility with spilled placeholders

files: `co_cli/observability/_tail.py`, `co_cli/observability/_viewer.py`, `tests/test_telemetry_payload_limits.py`

Implementation:
- Check how the new placeholder strings render in summary/detail/tree and HTML trace views.
- Make only minimal compatibility tweaks needed to keep placeholders readable.
- Do not redesign viewer modes or add new viewer-specific concepts in this task.

Coverage targets:
- `co tail --detail` remains readable when a model message payload was spilled
- `co tail --tree` remains readable when context nodes contain spilled payload placeholders
- `co traces` attribute rendering shows placeholder metadata cleanly

done_when: |
  exporter-side spill placeholders are legible in the existing viewers without requiring a second feature wave;
  ROI 1 output modes remain useful after payload caps land
success_signal: payload limiting improves storage behavior without degrading observability UX
prerequisites: [TASK-2]

## TASK-4: Add focused automated coverage for telemetry payload limits

files: `tests/test_telemetry_payload_limits.py`

Implementation:
- Add a dedicated test file for payload-limiting behavior.
- Prefer real SQLite and real exporter paths over fake serialization stubs.
- Use realistic payloads shaped like current GenAI message and tool attributes.

Coverage must include:
- content-addressed persistence behavior
- placeholder stability for identical content
- target-key-only limiting
- viewer compatibility with spilled placeholders
- no schema change regression in written span rows

done_when: |
  telemetry payload limiting has direct automated coverage instead of relying on manual DB inspection;
  the targeted-key policy is locked by tests
success_signal: future observability work can change limits deliberately without accidental broadening or regressions
prerequisites: [TASK-1, TASK-2, TASK-3]

---

## Testing

During implementation, scope to the affected tests first:

- `mkdir -p .pytest-logs && uv run pytest tests/test_telemetry_payload_limits.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-telemetry-payload-caps.log`

Before shipping:

- `scripts/quality-gate.sh full`

Manual validation after implementation:
- run `co chat` with a prompt that produces a large model response
- inspect the latest rows via `co logs` or direct SQLite query
- verify that targeted oversized payloads are replaced by readable placeholders with file references
- verify that `co tail --detail`, `co tail --tree`, and `co traces` remain usable on those spans

---

## Open Questions

- Whether the telemetry spill directory should be fully separate from tool-result storage or share a common content-addressed persistence utility under a more general module. Recommended v1: keep the directory separate, even if some implementation logic is shared.
- Whether exporter-side limiting should later expand to `events` payloads. Recommended v1: no; keep the first rollout limited to the known high-volume span attributes.
