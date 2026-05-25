# remove-compactable-tools-whitelist

## Context

`COMPACTABLE_TOOLS` (`co_cli/tools/categories.py:32`) is a 7-entry frozenset that gates which tool returns are eligible for content-clearing by `evict_old_tool_results` and dedup by `dedup_tool_results`. Tools not on the list pass through history unchanged — writes, approvals, memory ops, todo writes, skill lookups, etc.

Cross-peer survey (`docs/reference/RESEARCH-context-management-comparison.md`) shows co is the outlier:

| Peer | Filter framing | Filter size |
|---|---|---|
| Hermes (`agent/context_compressor.py`) | None — uniform clearing | 0 |
| Openclaw (`compaction.ts`, `tool-result-truncation.ts`) | None — uniform | 0 |
| Opencode (`packages/opencode/src/session/compaction.ts:38`) | Blacklist (`PRUNE_PROTECTED_TOOLS = ["skill"]`) | 1 |
| **Co** | **Whitelist (`COMPACTABLE_TOOLS`)** | **7** |

The peer pattern is *default-clear, narrow-exception*; co does the inverse.

### Why the whitelist doesn't earn its weight

State preserved by exempting a tool return from clearing must actually live in the return content. For every non-compactable tool in co's surface, the load-bearing state lives elsewhere:

- `memory_manage` / `memory_view` — state in `MemoryStore`; return is a status confirmation or a body that's trivially re-fetched.
- `todo_write` / `todo_view` — state in todo store; compaction enrichment (`gather_compaction_context` in `_compaction_markers.py`) already injects a fresh todo snapshot into the marker.
- `file_write` / `file_patch` — file is on disk; return is a status line.
- `session_view` — content recoverable via re-call.
- `skill_view` — body recoverable via re-call (one wasted tool call, not capability loss).
- Approval-related returns — approval state in `session_approval_rules`, not in return content.

The overflow recovery path (`strip_all_tool_returns`) already validates this: it strips *every* tool return universally when overflow forces the issue, and co operates correctly afterward. The whitelist preserves signal the system has already proven it can lose.

### Maintenance cost

Every new tool requires a `COMPACTABLE_TOOLS` membership decision. The frozenset is silent on tools that drift in or out of the eligible set — there is no test forcing classification of new tools. Today's set: `file_read`, `shell_exec`, `file_search`, `file_find`, `web_search`, `web_fetch`, `obsidian_read`.

### Code accuracy verification (anchors for the plan)

Full reference sweep — all live call sites:
- `co_cli/tools/categories.py:32` — definition.
- `co_cli/context/history_processors.py:58` (import), `:160` (durable-tail-protected set), `:165` (durable pre-tail keep-recent gate), `:223` (`_build_keep_ids` gate), `:312` (`evict_old_tool_results` short-circuit). Recovery helper `strip_all_tool_returns` at `:480-499` already universal — no filter.
- `co_cli/context/_dedup_tool_results.py:22` (import), `:50` (`is_dedup_candidate` gate).
- `co_cli/context/_tool_result_markers.py:19` (import), `:45` (`is_cleared_marker` membership scan for idempotency).
- `co_cli/context/_tool_result_markers.py` has per-tool branches for all 7 compactable tools plus a generic fallback already.

Specs and docs:
- `docs/specs/compaction.md` §2.3 (worked example + tool-list prose), §2.7 (PATH 1 strip prose mentions "no `COMPACTABLE_TOOLS` filter"), §4 (table row for `evict_old_tool_results`), §5 (file table for `categories.py`).
- `docs/specs/core-loop.md:272` (processor table row).
- `docs/specs/prompt-assembly.md:81` (processor table row).

Tests:
- `tests/test_flow_compaction_history_processors.py` — protects-non-compactable assertions.
- `tests/test_flow_compaction_recovery.py:test_recover_strip_only_fits` (currently expects `[<tool_name>] ` prefix directly, NOT via `is_cleared_marker`, because the helper only recognizes compactable prefixes today — see plan-relevant note below).

Evals:
- No live `COMPACTABLE_TOOLS` imports in `evals/` after the 2026-04-23 history-split / context-modularization plans (verified via grep).

## Problem & Outcome

**Problem.** A 7-entry whitelist gates tool-return clearing eligibility, but the state preserved by the exemption already lives outside tool-return content for every non-compactable tool. The result is per-tool maintenance cost and asymmetric behavior between proactive (`evict_old_tool_results`, selective) and overflow recovery (`strip_all_tool_returns`, universal) — the same operation, two policies.

**Outcome.** Unified policy: every tool return is eligible for content-clearing past `COMPACTABLE_KEEP_RECENT = 5` per tool name, replaced by a semantic marker. `COMPACTABLE_TOOLS` is deleted; `evict_old_tool_results` and `strip_all_tool_returns` differ only in scope (recency-protected vs. universal), not in tool selectivity. `dedup_tool_results` gates on content length (≥ 200 chars, string) — same as today, minus the tool-name filter.

**Failure cost (of not doing this).** Compounding maintenance: every new tool requires a classification decision with no enforcement test. Asymmetric proactive/recovery policy is a recurring source of confusion in spec reviews and code reviews. The current whitelist is a deferred decision that costs us once per new tool.

## Scope

**In:**
- Delete `COMPACTABLE_TOOLS` from `co_cli/tools/categories.py`. `FILE_TOOLS` and `PATH_NORMALIZATION_TOOLS` stay.
- Remove the 5 call-site gates in `history_processors.py` and the 1 gate in `_dedup_tool_results.py`.
- Rewrite `is_cleared_marker` (`_tool_result_markers.py:29`) to recognize any tool-name prefix via a regex, no longer scanning a fixed set.
- Update spec sections in `compaction.md`, `core-loop.md`, `prompt-assembly.md`.
- Update tests in `test_flow_compaction_history_processors.py` and `test_flow_compaction_recovery.py`.

**Out (deferred):**
- Adopting opencode's blacklist pattern for `skill_view` or other tools. Analysis shows no co tool has load-bearing state that lives only in its return content. Revisit only if a multi-cycle eval shows fidelity loss.
- Hermes-style enhancement to the `semantic_marker` per-tool dispatch table. Out of scope — orthogonal cleanup.
- Test enforcing classification of new tools (becomes unnecessary once the filter is gone).
- `RECENCY_CLEARING_ADVISORY` rewording — current text is already tool-agnostic.

## Behavioral Constraints

C1. **Eligibility for clearing is content-shape only, not tool-name.** `dedup_tool_results` gates on `string content AND len ≥ 200`. `evict_old_tool_results` keeps the 5 most-recent returns per tool name; everything older is content-cleared. No tool-name filter at either site.

C2. **`semantic_marker()` must produce a sensible marker for every tool name.** The per-tool dispatch in `_tool_result_markers.py` already has a generic fallback for unknown tools — verify it returns a non-empty, recognizable `[{tool_name}] ({content_len} chars)`-shaped string and keep it as the path for any tool without an explicit branch.

C3. **`is_cleared_marker` recognizes any marker prefix.** Current implementation iterates `COMPACTABLE_TOOLS`; replacement uses regex `^\[[a-z_][a-z0-9_]*\] ` to match co's tool naming convention. Also continues to recognize the static `[tool result cleared` fallback prefix. Collision risk with tool *return content* that happens to start with `[name] ` is bounded by the regex's lowercase/underscore restriction and the operational fact that this predicate is only called on already-cleared returns inside `_build_cleared_part` (idempotency) and `strip_all_tool_returns` (idempotency).

C4. **Idempotency of `_build_cleared_part` is preserved.** Re-running `evict_old_tool_results` over a previously-cleared history must not double-mark or degrade markers. This is the load-bearing case for C3 — `_build_cleared_part` checks `is_cleared_marker(content)` and short-circuits when true. Today this works for compactable tools only; after this plan it works for all tools.

C5. **`strip_all_tool_returns` semantics unchanged.** Already universal, no `COMPACTABLE_TOOLS` filter, no recency cap. After this plan, the only difference between strip (recovery) and evict (proactive) is recency-window + boundary protection, not tool selectivity.

C6. **Active-todos enrichment unchanged.** `gather_compaction_context` continues to inject the active todo snapshot into the compaction marker. Removing `todo_write` from the implicit-preserved set does not regress todo fidelity because the snapshot is the canonical source.

C7. **Spec coherence: drop the whitelist framing.** Spec sections that currently say "non-compactable tools pass through untouched" must say "all tool returns past the 5-most-recent are content-cleared with a semantic marker." Worked examples need a re-render to drop the non-compactable category.

C8. **Zero backward-compat surface.** Per `feedback_zero_backward_compat` memory — no alias for `COMPACTABLE_TOOLS`, no compat shim, no deprecated re-export. Single-commit removal, single-commit revert if needed.

## Implementation Tasks

| # | File | Change |
|---|---|---|
| 1 | `co_cli/tools/categories.py` | Delete `COMPACTABLE_TOOLS` frozenset (line 32–42). Keep `FILE_TOOLS`, `PATH_NORMALIZATION_TOOLS`. |
| 2 | `co_cli/context/history_processors.py` | Drop `COMPACTABLE_TOOLS` import (line 58). Remove the 4 filter guards at lines 160, 165, 223, 312. Update module docstring (line 18) — drop the "no `COMPACTABLE_TOOLS` filter" framing for `strip_all_tool_returns` (now trivially true). |
| 3 | `co_cli/context/_dedup_tool_results.py` | Drop `COMPACTABLE_TOOLS` import (line 22). Update `is_dedup_candidate` (line 50): remove the `tool_name in COMPACTABLE_TOOLS` clause; eligibility becomes "string content, ≥ 200 chars". Update docstring at line 46 — drop "Gates on `COMPACTABLE_TOOLS` membership". |
| 4 | `co_cli/context/_tool_result_markers.py` | Drop `COMPACTABLE_TOOLS` import (line 19). Rewrite `is_cleared_marker` (line 29) to use a compiled regex `_MARKER_PREFIX_RE = re.compile(r"^\[[a-z_][a-z0-9_]*\] ")`. Update its docstring to explain the regex-based recognition. Update module docstring (line 8) — drop "every member of `COMPACTABLE_TOOLS`" framing. |
| 5 | `co_cli/context/compaction.py` | Update comment at line 354 — drop the "no `COMPACTABLE_TOOLS` filter" line in the strip docstring; reword to "no recency cap, no boundary." |
| 6 | `tests/test_flow_compaction_history_processors.py` | Delete any test asserting "non-compactable tools pass through." Add: an unknown tool name with > 5 returns gets cleared to a semantic marker via the generic fallback. Existing recency-protection and last-turn-protection tests stay. |
| 7 | `tests/test_flow_compaction_recovery.py` | `test_recover_strip_only_fits` (currently asserts `[memory_create] ` via direct prefix check) — re-check: now `is_cleared_marker` would return True for `[memory_create] ...`, so any tests using `is_cleared_marker` against stripped non-compactable returns become symmetric. No assertion changes likely needed; verify both styles still pass. |
| 8 | `docs/specs/compaction.md` | §2.3: drop the `COMPACTABLE_TOOLS` tool list and "non-compactable tools pass through untouched" prose; rewrite the worked example to drop the "non-compactable preserved" pathway. §2.7 PATH 1 strip prose: drop "No `COMPACTABLE_TOOLS` filter" (the asymmetry is gone). §4 table row for `evict_old_tool_results`: rewrite to "Clears returns older than `COMPACTABLE_KEEP_RECENT` per tool." §5 file table: drop `COMPACTABLE_TOOLS` from the `categories.py` purpose. |
| 9 | `docs/specs/core-loop.md` | Line 272 processor table row: drop the `COMPACTABLE_TOOLS` qualifier. |
| 10 | `docs/specs/prompt-assembly.md` | Line 81 processor table row: drop the `COMPACTABLE_TOOLS` qualifier. |

## Validation

| Gate | Command / Source |
|---|---|
| Lint + types | `scripts/quality-gate.sh lint` |
| Full pytest | `scripts/quality-gate.sh full` |
| Proactive compaction eval | `uv run python evals/eval_compaction_proactive.py` |
| Multi-cycle fidelity eval | `uv run python evals/eval_compaction_multi_cycle.py` |
| Manual REPL smoke | One session exercising memory writes, todo writes, file writes, and skill views across enough turns to trigger compaction; verify model coherence post-compaction. |

**Expected eval outcomes:**
- `eval_compaction_proactive`: no change.
- `eval_compaction_multi_cycle`: fidelity score unchanged or marginally lower. If it drops more than ~5 percentage points, investigate before merging — the drop signals a tool whose return content was actually load-bearing and the plan's premise needs revising. Likely root cause if so: a tool whose body content is NOT recoverable via re-call.

## Risk

**Primary risk:** A tool whose load-bearing state lives in its return content gets cleared mid-session and the model degrades. Analysis shows no co tool fits this pattern, but the multi-cycle eval is the ultimate check.

**Mitigation:** If the eval flags a regression, add a 1-entry blacklist (opencode-style `PRUNE_PROTECTED_TOOLS`-equivalent) rather than restoring the whitelist. That preserves the simpler default and confines the exception to the one tool that genuinely needs it.

**Reversibility:** Single-commit revert. No data migrations, no persistent format changes. Frozenset re-add is mechanical.

## Effort

~1 hour: code (15 min) + tests (15 min) + spec (15 min) + eval verification (15 min).
