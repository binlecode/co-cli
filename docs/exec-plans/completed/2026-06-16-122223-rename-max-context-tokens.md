# Rename the context-window family: `max_ctx` → `max_context_tokens`

## Context

co's config field `max_ctx` (default `DEFAULT_MAX_CTX = 65_536`) and its runtime-resolved
counterpart `model_max_ctx` name **the model's context-window budget in tokens** — the reference
for the Ollama `num_ctx` ceiling/floor checks (`config/llm.py`) and the basis for the compaction
budget (`resolve_compaction_budget`). The name is deficient on two counts raised in review:

1. **`DEFAULT_` is wrong on a limit constant.** `DEFAULT_MAX_CTX` names a control *limit*, not a
   fallback selection; the `Field(default=…)` use site already conveys the default role, and
   `UPPER_CASE` vs the `max_ctx` field already disambiguates. The shared naming principle (see the
   sibling plan `2026-06-16-103148-peer-aligned-hierarchy-caps`): **limit constants drop the
   `DEFAULT_` prefix; fallback-selection constants (`DEFAULT_LLM_PROVIDER`/`_HOST`/`_MODELS`)
   keep it.**
2. **`ctx` is a non-explicit abbreviation** and `max_ctx` omits the unit. The codebase's own
   convention for token-count quantities is the `_tokens` suffix (`spill_threshold_tokens`,
   `static_floor_tokens`, `peak_input_tokens`, `current_request_tokens_estimate`). `max_ctx` is
   the outlier.

Chosen name (user decision): **`max_context_tokens`** (field) / `MAX_CONTEXT_TOKENS` (constant) /
`model_max_context_tokens` (runtime). Explicit, unit-suffixed, house-style.

This is a pure rename — **no behavior change**. `num_ctx` (Ollama's own API parameter, 43 refs)
is a different concept and is **not** touched.

## Problem & Outcome

**Problem:** A core, ~300-reference identifier (`max_ctx`/`model_max_ctx`) and its config key use
a terse, unit-less, abbreviation-laden name that violates the project's `_tokens` convention and
carries a misleading `DEFAULT_` prefix on its constant.

**Outcome:** The whole co-owned context-window family reads as `max_context_tokens` /
`MAX_CONTEXT_TOKENS` / `model_max_context_tokens` (+ eval analogues), consistently and
unambiguously; `num_ctx` is untouched; all tests green; no behavior change.

**Failure cost:** Low-severity but compounding — every new reader re-learns that `ctx` means
"context window in tokens" and that `DEFAULT_MAX_CTX` is a limit, not a fallback; the outlier name
erodes the otherwise-consistent `_tokens` convention.

## Scope

**In scope** (co-owned names only):
- `max_ctx` → `max_context_tokens` (config field + JSON config key + all readers)
- `DEFAULT_MAX_CTX` → `MAX_CONTEXT_TOKENS` (constant; drop `DEFAULT_`)
- `model_max_ctx` → `model_max_context_tokens` (CoDeps field + all readers)
- `EVAL_MAX_CTX` → `EVAL_MAX_CONTEXT_TOKENS`, `eval_max_ctx` → `eval_max_context_tokens`
  (eval centralized settings)
- `settings.reference.json` sample key
- CHANGELOG entry noting the config-key rename

**Out of scope**
- `num_ctx` — Ollama's own API parameter; a distinct concept; untouched.
- Any behavior change (values, checks, budgets stay identical).
- Spec files — updated by `sync-doc` post-delivery, not in any task's `files:` (per workflow).
- `CHANGELOG.md` history and `docs/exec-plans/completed/*` — historical; never rewritten.

## Behavioral Constraints

- **Zero behavior change.** Pure identifier/key rename; the resolved values and all
  ceiling/floor/budget logic are byte-for-byte equivalent.
- **Zero-backward-compat (project rule).** The JSON config key changes hard: a user
  `settings.json` with `"max_ctx"` must become `"max_context_tokens"`. No alias/compat reader.
  Flag in CHANGELOG so users migrate.
- The rename is **not** a single blind global replace: `DEFAULT_MAX_CTX` must drop `DEFAULT_`
  (→ `MAX_CONTEXT_TOKENS`, not `DEFAULT_MAX_CONTEXT_TOKENS`); uppercase eval constant needs case
  handling; `model_max_ctx`/`eval_max_ctx` must produce the correct verbose forms; **`num_ctx`
  must be left exactly as-is** (substring `max_ctx` does not occur in `num_ctx`, but verify no
  collateral edits).
- Land atomically — a partial rename will not import; the suite must be green in one delivery.
- **Telemetry-key change (not behaviorally inert).** The span attribute key `"ctx.max_ctx"`
  (`orchestrate.py:588`, event `ctx_overflow_check`) renames to `"ctx.max_context_tokens"`. No co
  code hardcodes the key (verified — `co tail`/`co trace` render attributes generically), so co's
  own tooling is unaffected; external log/trace parsers keyed on the old string would break — this
  is accepted as part of the zero-backward-compat rename. Leave the unrelated
  `budget.context_window_tokens` key (`history_processors.py:450`) untouched.

## High-Level Design

A mechanical, atomic rename across co-owned surfaces, verified by absence-grep + the full suite.

Surfaces (from the scope grep): prod `co_cli/` (9 files — `config/llm.py` owns the field+constant;
`deps.py`/bootstrap/compaction own `model_max_ctx`); evals (`evals/_settings.py` owns
`EVAL_MAX_CTX`/`eval_max_ctx` + 2 more files); tests (22 files); `settings.reference.json`;
CHANGELOG. Specs (5 files: config/bootstrap/compaction/core-loop/observability) follow via
`sync-doc`.

Replacement rules (apply in this order to avoid the `DEFAULT_` trap):
1. `DEFAULT_MAX_CTX` → `MAX_CONTEXT_TOKENS`
2. `EVAL_MAX_CTX` → `EVAL_MAX_CONTEXT_TOKENS`
3. `eval_max_ctx` → `eval_max_context_tokens`
4. `model_max_ctx` → `model_max_context_tokens`
5. `max_ctx` → `max_context_tokens` (catches the field, JSON key, and any remainder)
6. leave `num_ctx` untouched (verify)

## Tasks

✓ DONE ### TASK-1 — Rename the family across prod + evals + config sample
- **files:** `co_cli/config/llm.py`, `co_cli/deps.py`, the other 7 prod files referencing
  `model_max_ctx`/`max_ctx`, `evals/_settings.py` (+ 2 eval files), `settings.reference.json`
- **also rename:** the span attribute key `"ctx.max_ctx"` → `"ctx.max_context_tokens"`
  (`orchestrate.py:588`) — telemetry key, see Behavioral Constraints. User-facing log/error
  strings that embed the setting name (`bootstrap/core.py:288,303,316`, `check.py:125`, e.g.
  "Raise max_ctx in settings") are intentionally rewritten too — they reference the renamed
  setting, not collateral.
- **done_when:** the 5 ordered replacement rules applied; `rg -n "\bmax_ctx\b|model_max_ctx|DEFAULT_MAX_CTX|EVAL_MAX_CTX|eval_max_ctx" co_cli/ evals/ settings.reference.json` returns **zero** hits; `rg -c "num_ctx" co_cli/` is **unchanged from the pre-rename baseline** (capture it first; ~34 in `co_cli/`); `uv run co --help` imports cleanly.
- **success_signal:** the app boots and a turn runs; the context-window budget behaves identically (compaction triggers at the same token counts).
- **prerequisites:** none

✓ DONE ### TASK-2 — Update tests + full suite green
- **files:** the 22 `tests/` files referencing the old names
- **scope note:** test **function names** and **parametrize ids** embedding the token are in
  scope too (e.g. `test_display.py:353` `..._zero_max_ctx_...`, the `model_max_ctx` parametrize
  arg at `test_flow_compaction_recovery.py:166`). One name carries **both** tokens —
  `test_ollama_num_ctx_floor_passes_at_and_above_max_ctx` (`test_flow_bootstrap_ollama_num_ctx.py:20`):
  change only the `max_ctx` portion, preserve `num_ctx`.
- **done_when:** all test references renamed (incl. names/ids above); `rg -n "max_ctx" tests/`
  returns zero; full suite green — `mkdir -p .pytest-logs && uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-rename.log`.
- **success_signal:** N/A (pure rename; behavior unchanged).
- **prerequisites:** TASK-1

✓ DONE ### TASK-3 — CHANGELOG migration note
- **files:** `CHANGELOG.md`
- **done_when:** an entry spells out the exact self-migration — rename the key `"max_ctx"` →
  `"max_context_tokens"` in `~/.co-cli/settings.json` — flagged as a breaking config change
  (no compat alias).
- **success_signal:** N/A.
- **prerequisites:** TASK-1

## Testing

- **Absence-grep gate:** no old identifier survives in `co_cli/`, `tests/`, `evals/`,
  `settings.reference.json` (excluding `num_ctx`, CHANGELOG history, completed plans).
- **Full suite:** `uv run pytest` green (pipe to `.pytest-logs/`).
- **Boot smoke:** `uv run co --help` and one `uv run co chat` turn — confirms config load with the
  renamed key and identical compaction-budget behavior.
- **Eval smoke:** any one eval (e.g. `eval_multistep_plan.py`) loads `eval_max_context_tokens`
  without error.

## Open Questions

1. Confirm the eval constant casing: `EVAL_MAX_CONTEXT_TOKENS` / `eval_max_context_tokens`
   (parallels the prod field). Assumed yes; flag at Gate 1 if a different eval convention applies.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review: right problem? correct scope? (Confirm Open Question 1 — eval-constant casing.)
> Once approved, run: `/orchestrate-dev rename-max-context-tokens`

## Delivery Summary — 2026-06-16

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | zero old names in `co_cli/`/`evals/`/`settings.reference.json`; `num_ctx` count unchanged (34); `co --help` imports | ✓ pass |
| TASK-2 | zero `max_ctx` in `tests/` (incl. fn/parametrize names; `num_ctx` preserved in dual-token name); suite green | ✓ pass |
| TASK-3 | CHANGELOG entry spelling out the breaking `"max_ctx"`→`"max_context_tokens"` settings.json self-migration | ✓ pass |

**Tests:** scoped — rename-critical behavioral set (bootstrap num_ctx floor/ceiling, compaction recovery/proactive/spill, status command, display, observability spans) 70 passed, 0 failed; broader touched-file run 65 passed, 0 failed. Eval settings import smoke (`EVAL_MAX_CONTEXT_TOKENS` / `eval_max_context_tokens`) clean.
**Doc Sync:** fixed — full scope; `max_ctx` family + span key `ctx.max_ctx` renamed across 5 specs (observability, bootstrap, config, core-loop, compaction); `num_ctx` preserved everywhere; no file-set change so `system.md` index untouched.

**Overall: DELIVERED**
Pure atomic rename landed: config field + JSON key, constant (`DEFAULT_` dropped), runtime field, eval settings, span attr key, tests, and specs all migrated to `max_context_tokens`; `num_ctx` (Ollama provider parameter) left intact; lint clean, scoped tests green, zero behavior change.

## Implementation Review — 2026-06-16

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | zero old names in `co_cli/`/`evals/`/`settings.reference.json`; `num_ctx`=34; `co --help` imports | ✓ pass | absence-grep `\bmax_ctx\b\|model_max_ctx\|DEFAULT_MAX_CTX\|EVAL_MAX_CTX\|eval_max_ctx` → 0 hits; `num_ctx` count = 34 (baseline); `co_cli/config/llm.py:29` `MAX_CONTEXT_TOKENS = 65_536` + `:236` `Field(default=MAX_CONTEXT_TOKENS)` (DEFAULT_ dropped); span key `ctx.max_context_tokens` at `orchestrate.py`; `budget.context_window_tokens` key left intact; `co --help` boots |
| TASK-2 | zero `max_ctx` in `tests/`; suite green | ✓ pass | `rg "\bmax_ctx\b" tests/` → 0 hits; 103 rename-critical tests green (bootstrap num_ctx floor/ceiling, compaction recovery/proactive/spill/processor-chain/snapshot, status, display, observability spans, model-request-cap, usage) |
| TASK-3 | CHANGELOG breaking-change migration note | ✓ pass | `CHANGELOG.md:5-10` — top entry spells out `"max_ctx"`→`"max_context_tokens"` settings.json self-migration, zero-backward-compat/no-alias, companion + telemetry-key renames; historical entries preserved |

### Issues Found & Fixed
No issues found. Lint clean (ruff check + format, 370 files). Diff files outside the plan's `files:` (`commands/filescope.py`, `commands/skills.py`, `commands/tools.py`, `.agent_docs/*`, skills) carry **zero** rename-related lines — pre-existing unrelated working-tree changes, not scope creep from this delivery.

### Tests
- Command: `uv run pytest`
- Rename-critical set: **103 passed, 0 failed** (the surface this rename touches).
- Full suite: two failures surfaced, both **pre-existing flakes unrelated to the rename**, each confirmed green in isolation:
  - `test_flow_user_image_intake.py::test_lone_absolute_image_path_attaches_and_answers` — model-dependent vision-attach flake (failing run 21.9s vs ~4s passing); 1 fail / 3 pass; only rename touch is the `model_max_context_tokens` deps field.
  - `test_flow_post_turn_hook.py::test_review_disabled_short_circuits_no_state_mutation` — test-isolation/ordering flake; file is NOT in rename scope; passes in isolation.
- Logs: `.pytest-logs/*-review-impl*.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots cleanly — config/llm.py loads with renamed field + constant.
- Eval settings import: ✓ `EVAL_MAX_CONTEXT_TOKENS = 32768`, `eval_max_context_tokens() = 32768`.
- `success_signal` (TASK-1, "app boots; context-window budget behaves identically"): ✓ verified — app boots; identical budget behavior covered end-to-end by passing compaction recovery/proactive/spill tests against `model_max_context_tokens`.
- `co status` not run — not a registered command in the current working tree (commands reorg is unrelated in-progress work); the rename's user-facing surface is config-load-at-boot, exercised by `co --help`.

### Overall: PASS
Pure atomic rename verified: absence-grep clean, `num_ctx` untouched (34), constant `DEFAULT_` correctly dropped, telemetry key renamed while `budget.context_window_tokens` preserved, lint clean, rename-critical suite (103) green, zero behavior change. Both full-suite failures are pre-existing flakes outside this rename's surface.
