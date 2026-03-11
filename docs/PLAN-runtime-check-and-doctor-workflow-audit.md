# Plan Audit Log: Runtime Check And Doctor Workflow
_Slug: runtime-check-and-doctor-workflow | Date: 2026-03-10_

---

## Cycle C1 — Team Lead
Submitting for Core Dev review.

## Cycle C1 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2, CD-M-3
**Summary:** Three migration gaps require explicit task steps: `_status.py` imports `_check_llm_provider` and `_check_model_availability` directly from `_model_check.py` and is not covered by the migration plan; `test_model_check.py` tests `run_model_check` directly and will break when it is deleted in TASK-4; `reasoning_models` field mapping from `RuntimeCheck.capabilities` to `check_capabilities` return dict is underspecified.

**Major issues:**
- **CD-M-1** [TASK-1 / TASK-4]: `_status.py` directly imports `_check_llm_provider` and `_check_model_availability` from `co_cli._model_check`. TASK-1 moves provider probes to `_probes.py`; TASK-4 deletes `run_model_check`. Neither task lists `_status.py` as a file to modify. After TASK-4, `_status.py` will have a broken import. Recommendation: add `co_cli/_status.py` to TASK-4's files list and update its import to use `_probes.py` equivalents.
- **CD-M-2** [TASK-4 / TASK-8]: `tests/test_model_check.py` tests `run_model_check` directly. TASK-4 deletes `run_model_check` but TASK-8 does not mention updating or removing this test file. Recommendation: add `tests/test_model_check.py` to TASK-4's files list with instruction to migrate `run_model_check` tests to `tests/test_startup_check.py` (exercising `check_startup`) and remove deleted-function tests.
- **CD-M-3** [TASK-3 / TASK-5]: `check_capabilities` returns `reasoning_models` (a `list[ModelEntry]`), currently read from `ctx.deps.config.role_models.get("reasoning", [])`. `RuntimeCheck.capabilities` stores `role_models` as a nested dict `{reasoning, optional_roles}`. TASK-5 says "preserve all existing return dict fields" but does not show how `reasoning_models` is extracted from `RuntimeCheck`. Recommendation: explicitly add a `reasoning_chain: list[ModelEntry]` key to `RuntimeCheck.capabilities` in TASK-3, and state in TASK-5 that it maps to `reasoning_models` in the return dict.

**Minor issues:**
- **CD-m-1** [TASK-1 / TASK-3]: `probe_google` will need `GOOGLE_TOKEN_PATH` and `ADC_PATH` constants currently imported from `co_cli.tools._google_auth` inside `run_doctor`. Plan is silent on where `check_runtime` sources these. Recommendation: add a note that `_probes.py` imports these constants from `co_cli.tools._google_auth` and `check_runtime` passes them through.
- **CD-m-2** [TASK-8]: `make_minimal_co_deps`, `make_co_deps_with_grep_fallback`, and `make_co_deps_with_tool_names` are named as importable helpers, but no such helpers exist. Per CLAUDE.md, `conftest.py` is forbidden. Recommendation: clarify these are local factory functions defined at the top of `tests/test_runtime_check.py`, following the inline-construction pattern used in existing test files.
- **CD-m-3** [TASK-2]: `done_when` asserts `"check_capabilities" in deps.session.tool_names` which is not a standalone machine-verifiable test without running `chat_loop()`. Recommendation: reword to "`agent, _, tool_names, _ = get_agent(); assert 'check_capabilities' in tool_names` passes in a test that calls `get_agent()` directly."

## Cycle C1 — PO

**Assessment:** revise
**Blocking:** PO-M-1, PO-M-2
**Summary:** The plan is sound in structure and correctly scoped, but two issues block: the user-visible payoff (TASK-6 doctor rewrite) is buried last without explicit delivery priority, and context pressure / compaction signal is silently dropped from the MVP scope table without a documented decision.

**Major issues:**
- **PO-M-1** [TASK-6 sequencing]: The only user-visible outcome — the `/doctor` structured troubleshooting rewrite — is gated behind 5 internal refactor tasks. If scope must be cut, there is no documented priority order. The doctor skill rewrite depends only on TASK-5, not the full chain. Recommendation: add an explicit delivery priority note stating TASK-6 is the primary user-visible gate; TASK-7 (bootstrap migration) is secondary and may be deferred if needed.
- **PO-M-2** [Scope]: The original design doc's MVP scope table includes "Context pressure / compaction signal: Y (lightweight only if it explains degraded behavior)." The plan's Scope section does not include it in either "In" or "Out" — it was silently dropped. Recommendation: add it explicitly to the "Out" list with a one-line justification, or add a minimal note to TASK-3 if still intended.

**Minor issues:**
- **PO-m-1** [TASK-2 done_when]: Asserts `"check_capabilities" in deps.session.tool_names` — asserts on a specific tool name string, which is an implementation detail. Recommendation: change to structural assertion such as `len(tool_names) > 0` or `len(tool_names) == expected_count`.
- **PO-m-2** [TASK-4 justification]: `_startup_check.py` is pure internal restructuring with no user-visible effect. Recommendation: add one-line rationale in TASK-4 explaining why separation of startup policy from probing matters for the problem being solved.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1 | adopt | Add `_status.py` to TASK-4 files; update its imports to use `_probes.py` |
| CD-M-2 | adopt | Add `tests/test_model_check.py` to TASK-4 files; migrate tests to `test_startup_check.py` |
| CD-M-3 | adopt | Add `reasoning_chain` key explicitly to TASK-3 capabilities dict; clarify mapping in TASK-5 |
| CD-m-1 | adopt | Add note in TASK-1 that `_probes.py` imports constants from `co_cli.tools._google_auth` |
| CD-m-2 | adopt | Clarify test helpers are local factory functions in `tests/test_runtime_check.py` |
| CD-m-3 | adopt | Reword TASK-2 done_when to use `get_agent()` directly in test |
| PO-M-1 | adopt | Add delivery priority note — TASK-6 is primary user-visible gate; TASK-7 is secondary |
| PO-M-2 | adopt | Add "context pressure" explicitly to Scope "Out" with justification |
| PO-m-1 | adopt | Change TASK-2 done_when to structural assertion on tool count |
| PO-m-2 | adopt | Add one-line rationale to TASK-4 explaining separation value |

## Cycle C2 — Team Lead
Submitting for Core Dev review.

## Cycle C2 — Core Dev

**Assessment:** approve
**Blocking:** none
**Summary:** All three C1 blocking items are resolved with correct specificity. All minor items from C1 are also addressed. One new minor advisory noted.

**Verified resolved from C1:**
- CD-M-1: resolved — `_status.py` added to TASK-4 files list; work section explicitly states replacing `_check_llm_provider`/`_check_model_availability` imports with `probe_provider`/`probe_role_models` from `_probes.py` and updating call sites.
- CD-M-2: resolved — `tests/test_model_check.py` (modify) and `tests/test_startup_check.py` (create) both added to TASK-4 files list with migration instructions in the work section.
- CD-M-3: resolved — `reasoning_chain: list[ModelEntry]` is now an explicit key in `RuntimeCheck.capabilities` in TASK-3; TASK-5 explicitly maps `result.capabilities["reasoning_chain"]` to the `reasoning_models` return field.

**Minor issues:**
- **CD-m-4** [TASK-4 / `_status.py`]: `_status.py` currently accesses `provider_check.message` (old check result attribute); `ProbeResult` uses `.detail`, not `.message`. The plan says "update call sites accordingly" — sufficient intent but worth noting explicitly so implementers don't miss the attribute rename. Recommendation: add one-line guard note in TASK-4 work: "Note: old check results used `.message`; `ProbeResult` uses `.detail` — update all attribute access."

## Cycle C2 — PO

**Assessment:** approve
**Blocking:** none
**Summary:** All C1 blocking items are resolved with precise, well-placed fixes. The plan is coherent end-to-end and ready to proceed to implementation.

**Verified resolved from C1:**
- PO-M-1: resolved — explicit "Delivery priority" note added to Scope section; TASK-6 named primary gate, TASK-7 named secondary/deferrable.
- PO-M-2: resolved — "Context pressure / compaction signal" added to Scope "Out" list with one-line justification.
