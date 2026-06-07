# REPORT — ALWAYS tool-schema audit (TASK A1)

Source: `uv run python tmp/a1_schema_report.py`, which calls
`co_cli.bootstrap.schema_budget.measure_always_schema_budget` with `stack=None` (native
`FunctionToolset` only, no MCP — matching `tests/test_orchestrator_schema_budget.py`). Per-tool
totals are the authoritative bucket numbers the schema-budget guard and the runtime
`deps.static_floor_tokens` both read. Run date: 2026-06-06.

## Headline

- **ALWAYS bucket: 19,862 chars (~4,965 tok)** across **22 ALWAYS-visibility tools** (measured
  `tool_count` = 35 native callables; 13 are DEFERRED).
- No empty descriptions.
- Per-tool max: `file_search` (2,111), `shell_exec` (1,966) — both already trimmed in prior passes.
- The current pins in `test_orchestrator_schema_budget.py` are `ALWAYS_BUCKET_CEILING = 20_200`,
  `PER_ALWAYS_TOOL_CEILING = 2_300` — i.e. ~340 chars of headroom over the live bucket.

(The plan's prior "24 ALWAYS across 14 modules" was stale — the live count is 22.)

**Recommendation for A2:** defer **4** tools — `session_search`, `session_view`, `skill_patch`,
`skill_edit` → bucket **16,505 chars (~4,126 tok)**, clearing the <19,000 target with +2,495 margin and no
docstring squeeze. `web_fetch`/`web_search` were considered but **revised to KEEP** after usage mining (see
grounding); the memory-write family also stays. Both are documented next levers, not A2 work.

## Char-cost list (ranked, ALWAYS bucket)

`Total = len(name) + len(description) + len(minified-params-JSON)`. Cumulative is running sum
down the ranking; `%bkt` is share of the 19,862-char bucket.

| Rank | Tool | Desc | Params | Total | Cumul | %bkt |
|---:|---|---:|---:|---:|---:|---:|
| 1 | file_search | 361 | 1,739 | 2,111 | 2,111 | 10.6% |
| 2 | shell_exec | 1,317 | 639 | 1,966 | 4,077 | 9.9% |
| 3 | clarify | 655 | 812 | 1,474 | 5,551 | 7.4% |
| 4 | todo_write | 651 | 790 | 1,451 | 7,002 | 7.3% |
| 5 | file_patch | 477 | 787 | 1,274 | 8,276 | 6.4% |
| 6 | file_read | 355 | 665 | 1,029 | 9,305 | 5.2% |
| 7 | memory_create | 27 | 989 | 1,029 | 10,334 | 5.2% |
| 8 | web_fetch | 444 | 560 | 1,013 | 11,347 | 5.1% |
| 9 | session_view | 560 | 366 | 938 | 12,285 | 4.7% |
| 10 | web_search | 345 | 559 | 914 | 13,199 | 4.6% |
| 11 | skill_patch | 391 | 511 | 913 | 14,112 | 4.6% |
| 12 | session_search | 448 | 427 | 889 | 15,001 | 4.5% |
| 13 | memory_search | 214 | 600 | 827 | 15,828 | 4.2% |
| 14 | file_write | 489 | 241 | 740 | 16,568 | 3.7% |
| 15 | skill_edit | 317 | 290 | 617 | 17,185 | 3.1% |
| 16 | memory_replace | 49 | 398 | 461 | 17,646 | 2.3% |
| 17 | capabilities_check | 377 | 62 | 457 | 18,103 | 2.3% |
| 18 | memory_view | 224 | 207 | 442 | 18,545 | 2.2% |
| 19 | memory_append | 64 | 302 | 379 | 18,924 | 1.9% |
| 20 | skill_view | 137 | 202 | 349 | 19,273 | 1.8% |
| 21 | todo_read | 258 | 62 | 329 | 19,602 | 1.7% |
| 22 | memory_delete | 47 | 200 | 260 | 19,862 | 1.3% |

DEFERRED (not in bucket, for context): `google_calendar_search` 1,444, `google_drive_search` 1,359,
`task_start` 1,295, `google_calendar_list` 1,220, `google_gmail_search` 1,028, `skill_create` 856,
`google_drive_read` 777, `task_status` 702, `google_gmail_draft` 673, `task_list` 591,
`google_gmail_list` 590, `task_cancel` 515, `skill_delete` 332.

## Functional tiering — first-principles framework

### The deferral cost model (co-specific — this is what makes tiering a real trade)

Deferring a tool in co is **not** "remove it." `co_cli/tools/deferred_prompt.py` still emits a per-turn
**stub** for every DEFERRED tool — `` - `name`: one-liner `` capped at ~110 chars — so the model stays
aware the tool exists and can `search_tools` to load its full schema before calling. So:

- **Per-turn saving from deferring = full schema − stub (~110 chars).** Deferral has a *floor cost*: a
  small tool whose whole schema is ~300 chars saves only ~190 by deferring, while still paying the costs
  below. Big schemas are where the prefill win actually lives.
- **One-time penalty: a `search_tools` round-trip** the first time the tool is needed in a context (an
  extra model-request cycle + latency), after which it is loaded.
- **Standing penalty: reduced plan-legibility** — the model plans from a one-line stub, not the full
  multi-arg schema, until it loads the tool. For a tool the model must reason *with* mid-task, that detour
  is disruptive.

This gives the two axes the tiering turns on:

- **Axis 1 — Schema size = deferral *payoff*** (chars saved per turn). Big = worth deferring on size alone.
- **Axis 2 — Functional criticality = deferral *penalty*** (harm from not having it loaded). Composed of:
  use-probability per session, plan-dependence (does the model need the full schema to reason?), and
  latency/interaction sensitivity. High criticality = keep ALWAYS regardless of size.

### The size × criticality matrix

|                       | **Critical** (high use / plan-dep / latency-sensitive) | **Not critical** (conditional / rare) |
|---|---|---|
| **Big schema** (≥~800 ch) | **ALWAYS** — payoff is real but the round-trip would recur on common paths and break mid-task planning | **DEFER** — the sweet spot: big saving, low penalty |
| **Small schema** (<~450 ch) | **ALWAYS** — obvious keep; saving is ~nil, penalty is high | **ALWAYS by default** — deferral saves almost nothing (stub floor), so a round-trip isn't worth it — *unless* it belongs to a family already being deferred, where cluster coherence (one grouped stub block) justifies it |

The non-obvious cell is **small + not-critical → keep**: the stub floor means deferring a small tool
buys you nothing while still risking a round-trip and a fragmented capability family. Size only pays off
when it's *big*; criticality vetoes deferral at any size. (Medium 450–800 ch tools follow the same logic:
defer only if not-critical *and* big-enough or family-aligned.)

### Per-tool placement (Tier 1 vs Tier 2 review)

| Tool | Total | Size | Critical? | Cell → decision | Justification |
|---|---:|---|---|---|---|
| file_search | 2,111 | Big | **Yes** | Big+Crit → **ALWAYS** | first move on most tasks; model plans the search args directly |
| shell_exec | 1,966 | Big | **Yes** | Big+Crit → **ALWAYS** | universal action primitive; reached every few turns |
| clarify | 1,474 | Big | **Yes** | Big+Crit → **ALWAYS** | interactive — a search detour before asking the user is bad UX |
| todo_write | 1,451 | Big | **Yes** | Big+Crit → **ALWAYS** | task tracking is continuous through the loop |
| file_patch | 1,274 | Big | **Yes** | Big+Crit → **ALWAYS** | dominant edit op; multi-arg schema must be visible to plan an edit |
| file_read | 1,029 | Big | **Yes** | Big+Crit → **ALWAYS** | read precedes nearly every edit |
| memory_search | 827 | Big | **Yes** | Big+Crit → **ALWAYS** | recall is checked routinely; co's memory model leans on it |
| file_write | 740 | Med | **Yes** | Med+Crit → **ALWAYS** | new-file writes pair with patch on the core write path |
| capabilities_check | 457 | Small | Med | Small → **ALWAYS** | tiny (params 62) — deferral saves nothing |
| memory_view | 442 | Small | **Yes** | Small+Crit → **ALWAYS** | full-body read after a memory hit; cheap to keep |
| skill_view | 349 | Small | Med-hi | Small → **ALWAYS** | reading a skill to follow it; tiny |
| todo_read | 329 | Small | **Yes** | Small+Crit → **ALWAYS** | reads the live task list; tiny (params 62) |
| web_fetch | 1,013 | Big | No→**revised** | Big+NotCrit → **KEEP** | prior was DEFER; usage mining + hermes-CORE flip it to keep (see grounding) |
| **session_view** | 938 | Big | No | Big+NotCrit → **DEFER** | verbatim past-turn read; follows a session_search hit, rarer still |
| web_search | 914 | Big | No→**revised** | Big+NotCrit → **KEEP** | prior was DEFER; usage mining + hermes-CORE flip it to keep (see grounding) |
| **skill_patch** | 913 | Big | No | Big+NotCrit → **DEFER** | skill *authoring*; rare; joins deferred skill_create/skill_delete family |
| **session_search** | 889 | Big | No | Big+NotCrit → **DEFER** | episodic recall; invoked when the user references prior work |
| **skill_edit** | 617 | Med | No | Med+NotCrit → **DEFER** | skill authoring (metadata); rare; family-aligned with skill writes |
| memory_create | 1,029 | Big | Med-low | Big+NotCrit → **KEEP** (see below) | big payoff, but splitting the memory-write family is incoherent |
| memory_replace | 461 | Med | Med-low | Med/Small+NotCrit → **KEEP** | small payoff; keep family coherent |
| memory_append | 379 | Small | Med-low | Small → **KEEP** | deferral saves ~nil |
| memory_delete | 260 | Small | Low | Small → **KEEP** | deferral saves ~nil |

**Outcome (revised after usage mining, below): defer 4** — `session_search`, `session_view`,
`skill_patch`, `skill_edit`. `web_fetch`/`web_search` are revised to **KEEP** (see grounding). All 12
small-or-critical tools, plus web and the memory-write family, stay ALWAYS.

### Usage-frequency grounding for criticality signal #1 (`tmp/mine_tool_frequency.py`)

Criticality's dominant sub-signal — use-probability per session — was a reasoned prior, not data. Mining
`~/.co-cli/sessions/*.jsonl` (tool-call parts, counted per tool and per distinct session) to ground it
returned **a corpus too thin to be authoritative**, with three limitations stated up front:

- **Tiny:** 9 real sessions, **42 tool calls total**.
- **Stale:** tool names are pre-rename (`run_shell_command`, `knowledge_manage`, `search_knowledge`,
  `check_capabilities`) — the corpus predates the current surface.
- **Skewed:** the sessions are mostly web-research tasks; coding ops (`file_patch`/`file_write`/`clarify`)
  barely appear — an artifact of *which* sessions exist, not a usage law.

Raw result (real sessions), calls / % of calls / sessions-present:

| Tool (as recorded) | Calls | %calls | Sessions present |
|---|---:|---:|---:|
| web_fetch | 16 | 38% | 5/9 (56%) |
| web_search | 9 | 21% | 4/9 (44%) |
| run_shell_command (→ shell_exec) | 5 | 12% | 1/9 |
| file_read | 4 | 10% | 4/9 (44%) |
| todo_write | 2 | 5% | 2/9 |
| knowledge_manage / search_knowledge (→ memory) | 3 | 7% | 1/9 |
| session_search | 1 | 2% | 1/9 (11%) |
| check_capabilities (→ capabilities_check) | 1 | 2% | 1/9 |

**What it does and doesn't license:**
- *Not authoritative.* 42 calls cannot validate or refute the prior in general.
- *Where it points, it confirms the low-use defers:* `session_search` 11%, and zero `session_view` /
  `skill_patch` / `skill_edit` / memory-writes — consistent with deferring those four.
- *It contradicts deferring web.* `web_fetch`/`web_search` are the **two most-present tools** here. The
  sample is web-biased, so this is confounded — but combined with hermes keeping both in CORE and the
  conservative "keep when uncertain" rule, two independent signals now point the same way. **Revise web to
  KEEP**; it becomes the first *re-defer* candidate if a larger, current corpus shows it idle on most
  sessions.

To make this authoritative later: collect a current, task-diverse corpus (coding + research + chat) and
re-run `tmp/mine_tool_frequency.py`; that replaces signal #1 with measured per-tool session-presence.

**Memory writes — resolved to KEEP (was "evaluate").** Only `memory_create` (1,029) has real payoff; the
other three are small (≤461) where deferral buys almost nothing. The four form one capability family, and
co's stub grouping renders a family as one block — so the choice is defer-all or keep-all, not split.
Keeping them ALWAYS costs ~2,129 chars but (a) preserves co's self-curation surface as a coherent,
directly-callable cluster — consistent with co's deliberate monomorphic memory-op split for small-model
legibility (`feedback_tool_split_small_model`) — and (b) the recommended 6-tool deferral already clears the
target with margin, so the memory family is not needed as ballast. *If* a later floor squeeze is required,
deferring the whole memory-write family (−2,129 → bucket ~12,449) is the next lever.

### Tier 3 — Already DEFERRED (no action)

Google suite (calendar/drive/gmail), task-control (`task_*`), skill write/manage
(`skill_create`/`skill_delete`). Listed in the DEFERRED block above.

## Peer grounding — and why their tier lists don't port

co is the **only** surveyed agent that defers *individual native tools* behind a per-tool stub + a
`search_tools` round-trip. The peers reach a small prompt surface by **different sizing designs**, so their
specific keep/defer choices are not directly transferable — only the shared principle is.

- **hermes-agent** (closest analog) — thin-ish, 63 tools, *does* defer behind bridge tools
  (`tool_search`/`tool_describe`/`tool_call`), but **defers by provenance, not per-tool criticality**: a
  hand-picked CORE set (`toolsets.py` `_HERMES_CORE_TOOLS`) is *never* deferred, and only **MCP/plugin**
  tools defer — and only when their aggregate schema exceeds ~10% of the context window
  (`tools/tool_search.py`). Notably hermes' CORE **includes `web_search` and `session_search`** — the
  inverse of co's *initial* call on web. hermes keeping `web_search` in CORE was one of the two signals
  (with usage mining) that **flipped co's web tools back to KEEP**. `session_search` co still defers
  (hermes' inclusion is outweighed by its near-zero use in co's mined corpus + co's sharper floor pressure
  — a ~10.8k floor on a 64k *small-model* window — and co's stub preserves awareness where hermes' hidden
  MCP tier would not). Skill-authoring is the firmest defer.
- **openclaw** — **FAT** tools (~20, action/mode dispatch) + availability gates (auth/config/env). It
  shrinks the surface by **consolidation**, not deferral, and enforces no schema token budget. Not portable
  to co, which deliberately keeps **thin monomorphic** tools for small-model legibility
  (`feedback_tool_split_small_model`) — co cannot recover budget by fattening tools without abandoning that
  stance.
- **opencode** — thin (16) + **per-agent permission subsets** (e.g. the `explore` agent is restricted to
  read/grep/glob/bash/web) + runtime flag gates; no deferral. Its lever is **per-agent surfaces**, an axis
  co does not use today — a possible future lever (a lean default surface per agent role) but out of scope
  for A2.

**Transferable principle (all four agree):** keep a small hand-picked **core** always-visible and push
peripheral capability out of the default surface. co's Tier 1 *is* everyone's "core"; co's Tier 2 is co's
analog of hermes' deferred MCP/plugin tier — the difference is only the *unit* of tiering (co: per-primitive
criticality; hermes: provenance; openclaw: consolidation; opencode: per-agent).

## Projected post-deferral bucket (for TASK A2)

TASK A2 target: ALWAYS bucket **below 19,000 chars**. From 19,862:

| Deferral set | Saved | New bucket | vs 19,000 target |
|---|---:|---:|---|
| **Recommended (4: session_search/view + skill_patch/edit)** | **3,357** | **16,505 (~4,126 tok)** | **met, +2,495 margin** |
| + web_fetch + web_search (if a current corpus shows web idle) | 5,284 | 14,578 (~3,644 tok) | met, +4,422 margin |
| + memory-write family (further future lever) | 7,413 | 12,449 (~3,112 tok) | met, +6,551 margin |

The recommended 4-tool set clears the target with margin and needs no docstring squeeze. Docstring
tightening (e.g. `shell_exec` desc 1,317, `file_search` params 1,739) becomes optional headroom once
deferral lands. Web (revised to keep) and the memory-write family are deliberate KEEPs documented as the
next levers if a future floor squeeze demands them — not part of the A2 recommendation.

## Notes for A2

- Defer the 4 recommended tools: `session_search` (`session/recall.py`), `session_view`
  (`session/view.py`), `skill_patch`/`skill_edit` (`system/skills.py`). The two skill tools also fix an
  inconsistency — their `skill_create`/`skill_delete` siblings are already DEFERRED, so this aligns the
  whole skill-write surface behind ToolSearch. `skill_patch`/`skill_edit` are the firmest defers.
- Do **not** defer `web_fetch`/`web_search` — revised to KEEP (usage mining + hermes-CORE). Re-defer only
  if a current, task-diverse corpus shows them idle on most sessions.
- After deferral, re-pin `ALWAYS_BUCKET_CEILING` and `PER_ALWAYS_TOOL_CEILING` to the new measured
  values; `deps.static_floor_tokens` is measured live at bootstrap from the same helper, so the
  runtime trigger floor auto-updates.
