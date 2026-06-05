# read-view-emission-spill-cap

> **Extracted from `docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md` (ISSUE-5).
> Sibling: `2026-06-05-150129-l2-spill-tail-protection.md`.**
> Scope evolved during planning. This plan is now the **read/view limit-constant cleanup**: dedup the three
> per-tool line caps into one pagination cap, collapse the per-unit byte knobs, return verbatim
> `session_view` content, and remove the `math.inf` spill-bypass sentinel. The **visibility guarantee**
> ("the model must see what it just read") turned out to be an L2 force-spill problem, not an emission-cap
> one — it is owned by the **sibling plan** above. Here `READ_MAX_CHARS` is **demoted** to a high backstop
> (or dropped — see Open Questions), not the headline fix.

## Why the emission cap is no longer the fix (read this first)

The original ISSUE-5 framing was "the four read/view tools bypass per-emission spill (`∞`), so an oversized
read lands inline unbounded — give them a finite emission cap." Tracing the spill tiers in source showed
that framing is wrong:

- A read tool's purpose is to put content **in front of the model**. The `∞` emission bypass exists for
  exactly that.
- But the model's *first* sight of a tool result is the **next** request, and `spill_largest_tool_results` (L2)
  runs on that request *before* it's sent — collecting **every** tool return with no tail protection
  (`history_processors.py:312-321`) and force-spilling largest-first (`:341`). So a freshly-read large doc
  is stubbed **before the model ever sees it** (sibling plan documents this fully).
- Therefore an L1 **emission** cap doesn't deliver visibility — and a *low* one actively harms it: spilling
  a read at emission removes it before L2's tail-protection (the sibling fix) can preserve it. **Emission
  spill and visibility are in tension.**

So visibility is restored by the **L2 tail-protection sibling plan** (preserve the recent tail in
`spill_largest_tool_results`, mirroring L3). This plan keeps only the parts that are genuinely about the read/view
tools themselves — pagination and the duplicated limit constants — and demotes the byte cap to a backstop.

## Context

Each of the four read/view tools (`spill_threshold_chars=math.inf`) grew its **own ad-hoc limit constants**,
with no shared bound and no clean total-output cap:

| Tool | Decorator | Limit constants today |
|---|---|---|
| `file_read` | `read.py:397` | `_READ_DEFAULT_LIMIT_LINES = 500`, `_READ_MAX_LINES = 2000`, `_READ_MAX_LINE_CHARS = 2000`, `_READ_MAX_FILE_BYTES = 500_000` |
| `session_view` | `view.py:25` | `_SESSION_TURN_MAX_LINES = 200`, `_SESSION_TURN_MAX_BYTES = 16 KB`, + a per-turn 200-char preview (`content[:200]`) |
| `memory_view` | `memory/view.py:22` | none |
| `skill_view` | `skills.py:37` | none at read time (`_MAX_SKILL_CHARS` is write-side validation) |

The duplication is the problem this plan owns:

- **Three line-count caps** (`500`/`2000`/`200`) across two tools do the same job with different values.
- **Three partial byte knobs** (`_READ_MAX_LINE_CHARS`, `_SESSION_TURN_MAX_BYTES`, the 200-char preview)
  are **implicit proxies** for "don't let one read get too big" — confusing because their names describe a
  per-line/per-turn unit while their real job is output-size protection they only half-do. Worse,
  `session_view`'s 200-char preview **defeats the tool's stated purpose**: the docstring promises "the exact
  turn content … rather than the chunk-level snippet" (`view.py:36-38`) but it returns `content[:200]` per
  turn (`view.py:82`) — a second snippet view, not verbatim content.
- The `math.inf` bypass is itself a **sentinel-value smell**: a numeric threshold field encoding the binary
  "exempt from emission spill," widening the type to `int | float` and forcing a `math.isinf` special-case.

**The cleanup:** one pagination cap (`READ_MAX_LINES`) replaces the three line caps; the per-unit byte knobs
are removed (the demoted `READ_MAX_CHARS` backstop subsumes what little they did); `session_view` returns
verbatim; the `math.inf` sentinel and its special-casing are removed.

### Peer-survey alignment (`docs/reference/RESEARCH-context-management-peer-survey.md`)

Correcting the parent survey (verified at peer HEADs): **opencode** bounds live reads with `MAX_LINES =
2000` + `MAX_BYTES = 50 * 1024`, **recoverable** (`tool/truncate.ts:16-17`); its `TOOL_OUTPUT_MAX_CHARS =
2000` is a separate compaction-path trim, not the live cap (the survey conflated them). **openclaw** caps
live at `TOOL_RESULT_MAX_CHARS = 8000`, lossy head-only. **hermes** has no live cap; **codex** trims schemas
only. So co was *not* "the lone outlier with an uncapped live path" — hermes/codex also defer bounding.
The shape co lands on — line-cap pagination + a recoverable byte backstop — is opencode's shape; co keeps a
tighter line cap (`500`) so normal reads stay inline rather than byte-truncating.

## Problem & Outcome

**Problem.** The read/view tools carry duplicated, partial limit constants (three line caps, three byte
knobs) and a `math.inf` sentinel; `session_view`'s preview defeats its own purpose. (The *visibility* defect
— a fresh read spilled before the model sees it — is the sibling plan's; not solved here.)

**Outcome.**
- **`READ_MAX_LINES = 500`** (new shared global) — one pagination cap for `file_read` and `session_view`,
  with the existing continuation hint paging forward. Dedupes `_READ_DEFAULT_LIMIT_LINES`,
  `_READ_MAX_LINES`, `_SESSION_TURN_MAX_LINES`.
- **`session_view` returns verbatim** — the 200-char preview and `_SESSION_TURN_MAX_BYTES` are removed.
- **The `math.inf` sentinel is removed** — replaced by a finite `READ_MAX_CHARS` on every read/view
  decorator, with the `math.isinf` special-case, the `int | float` type widths, and the now-orphan
  `import math` cleaned up.
- **`READ_MAX_CHARS` is a demoted backstop**, not an active per-read cap — see Open Questions for its value
  and the keep-vs-drop decision. It must be set **high enough not to spill normal/dense reads at emission**
  (or it re-breaks the sibling's visibility fix).

Normal reads — a default 500-line `file_read` (~26k chars), a typical `session_view` range, an ordinary
memory/skill body — stay fully inline and (via the sibling plan) visible.

## Scope

**In scope — read/view limit-constant cleanup:**
- Add `READ_MAX_LINES = 500` (pagination) and `READ_MAX_CHARS` (demoted backstop) to `tool_io.py`.
- Dedup the three line caps → `READ_MAX_LINES`: `_READ_DEFAULT_LIMIT_LINES`, `_READ_MAX_LINES` (`read.py`),
  `_SESSION_TURN_MAX_LINES` (`view.py`).
- Collapse the three byte knobs: remove `_READ_MAX_LINE_CHARS` + its per-line truncation (`read.py`); remove
  `_SESSION_TURN_MAX_BYTES` + the 200-char preview, returning verbatim turns (`view.py`).
- **Remove the `math.inf` sentinel + its special-casing.** Wire the four decorators
  `spill_threshold_chars=math.inf` → `=READ_MAX_CHARS`; drop the `math.isinf` branch (`tool_io.py:138`),
  narrow `threshold_chars: int | float` → `int` (`:134`) and `spill_threshold_chars: int | float | None` →
  `int | None` (`agent_tool.py:34`, `deps.py:109`); drop the orphan `import math` from `tool_io.py` (`:21`)
  and each of the four tool modules.
- Scoped behavioral tests; update the parent plan's ISSUE-5 block.

**Retained — distinct-purpose guards, not read-output caps:**
- `_READ_MAX_FILE_BYTES = 500_000` — I/O-safety guard (`file_read` `read_text()`s the whole file before
  slicing, `read.py:456`; pagination doesn't prevent slurping a huge file). Kept.
- `_MAX_SKILL_CHARS = 50_000`, `_MAX_DESCRIPTION_CHARS = 1024` — write-side validation for
  `skill_create`/`edit`/`patch` (`skills.py:79-87`), not reading. Kept.
- `TOOL_RESULT_PREVIEW_CHARS = 1_500`, `SPILL_THRESHOLD_CHARS = 4_000` — spill-preview length / non-read-tool
  default. Untouched.

**Out of scope (owned elsewhere):**
- **The visibility guarantee** ("model sees what it read") — sibling `2026-06-05-150129-l2-spill-tail-protection.md`.
- The L2 `spill_largest_tool_results` path generally and the HTTP-400 recovery path.
- Streaming `file_read` (read only the needed lines) — an I/O optimization.
- The loop-stability eval extension — parent plan.
- `docs/specs/` updates (`sync-doc` post-delivery), including the pagination model and the demoted-backstop
  role of `READ_MAX_CHARS`.

## Behavioral Constraints

- **One pagination cap, no duplicated proxies** — one `READ_MAX_LINES`; no second line ceiling, no per-unit
  byte proxy (`feedback_naming_no_abbreviations`).
- **`session_view` returns verbatim** — the tool exists to give exact turn content.
- **No sentinel** — a binary "exempt from emission spill" must not be encoded as `math.inf` in a numeric
  field; every tool carries a plain finite `int` threshold.
- **The backstop must not break visibility** — `READ_MAX_CHARS` is set high (or dropped); it must not spill
  normal/dense reads at emission before the sibling's L2 tail-protection can preserve them.
- **Surgical** (`CLAUDE.md`) — touch only the constants, the deduped/removed knobs, the four decorators, the
  sentinel machinery, scoped tests, and the parent-plan pointer. Retained guards untouched.

## High-Level Design

Add to `co_cli/tools/tool_io.py`:

```python
# Pagination cap for the read/view tools that page over a source (file_read,
# session_view). One read returns <= READ_MAX_LINES lines/turns; the continuation
# hint pages forward. 500 of typical source (~52 chars/line incl. line-number
# prefix) is ~26k chars — inline and (via the L2 tail-protection sibling) visible.
READ_MAX_LINES = 500

# Demoted backstop, NOT an active per-read cap. The read/view tools read from
# persistent, re-addressable sources, so visibility (model sees the read) is owned
# by L2 tail-protection, and per-read bounding by READ_MAX_LINES. This only catches
# a single page too large to show even once. Keep high enough that normal/dense
# reads do NOT spill at emission (a low cap re-breaks visibility). See Open Questions
# for the value / keep-vs-drop decision.
READ_MAX_CHARS = 32_000
```

**`file_read`** — `_compute_read_slice` uses `READ_MAX_LINES` for both the no-range default and the ranged
ceiling (removing `_READ_DEFAULT_LIMIT_LINES`, `_READ_MAX_LINES`); `_build_read_display` drops
`_READ_MAX_LINE_CHARS` and the per-line truncation (`read.py:59-60`) → verbatim lines. Decorator `math.inf`
→ `READ_MAX_CHARS`. `_READ_MAX_FILE_BYTES` untouched.

**`session_view`** — `_SESSION_TURN_MAX_LINES` → `READ_MAX_LINES`; remove `_SESSION_TURN_MAX_BYTES` + the
byte-accumulation break loop; return verbatim turns (`content_preview` field → full `content`, drop
`[:200]`). Decorator `math.inf` → `READ_MAX_CHARS`.

**`memory_view`, `skill_view`** — decorator wiring only.

**Sentinel removal** — with no caller passing `inf`, drop the `math.isinf` branch in `spill_with_span`
(`:138` → `span_threshold = int(threshold_chars)`), narrow the `int | float[ | None]` types to
`int[ | None]` (`tool_io.py:134`, `agent_tool.py:34`, `deps.py:109`), and drop `import math` from
`tool_io.py` (its only use was line 138) and the four tool modules.

## Tasks

### TASK-1 — Add `READ_MAX_LINES` + `READ_MAX_CHARS`; wire decorators; remove the `math.inf` sentinel
- **files:** `co_cli/tools/tool_io.py`, `co_cli/tools/files/read.py`, `co_cli/tools/memory/view.py`,
  `co_cli/tools/system/skills.py`, `co_cli/tools/session/view.py`, `co_cli/tools/agent_tool.py`,
  `co_cli/deps.py`
- **action:** Add the two globals to `tool_io.py` (with the comments above). Change
  `spill_threshold_chars=math.inf` → `=READ_MAX_CHARS` in the four tool modules and drop their orphan
  `import math`. Remove the sentinel machinery this orphans: drop the `math.isinf` branch (`tool_io.py:138`),
  narrow `threshold_chars: int | float` → `int` (`:134`), drop `import math` (`:21`); narrow
  `spill_threshold_chars: int | float | None` → `int | None` (`agent_tool.py:34`, `deps.py:109`).
- **done_when:** `grep -rn "math.inf\|isinf" co_cli/tools/ co_cli/deps.py` returns no hits; `READ_MAX_CHARS`
  is passed at all four decorator sites; `spill_threshold_chars`/`threshold_chars` are typed `int`(`| None`).
- **success_signal:** N/A (wiring + orphan removal; behavior verified by TASK-4).
- **prerequisites:** none.

### TASK-2 — `file_read`: dedup line caps → `READ_MAX_LINES`, remove per-line truncation
- **files:** `co_cli/tools/files/read.py`
- **action:** In `_compute_read_slice`, use `READ_MAX_LINES` for both read modes (remove
  `_READ_DEFAULT_LIMIT_LINES`, `_READ_MAX_LINES`). In `_build_read_display`, remove `_READ_MAX_LINE_CHARS`
  and the per-line truncation branch (`read.py:59-60`). Leave `_READ_MAX_FILE_BYTES` untouched.
- **done_when:** those three constants no longer exist; both read modes return ≤ `READ_MAX_LINES` lines with
  the continuation hint; lines emit verbatim; the full-file I/O guard is unchanged.
- **success_signal:** a no-range and a ranged read each return ≤500 lines with a working hint; a >2000-char
  line reads verbatim, not clipped.
- **prerequisites:** TASK-1.

### TASK-3 — `session_view`: dedup line cap, return verbatim turns, drop byte cap
- **files:** `co_cli/tools/session/view.py`
- **action:** `_SESSION_TURN_MAX_LINES` → `READ_MAX_LINES`. Remove `_SESSION_TURN_MAX_BYTES` and the
  byte-accumulation break loop; return verbatim turn content (`content_preview` field → `content`, drop
  `[:200]`).
- **done_when:** `_SESSION_TURN_MAX_LINES` / `_SESSION_TURN_MAX_BYTES` gone; range clamped by
  `READ_MAX_LINES`; a turn longer than 200 chars is returned in full.
- **success_signal:** `session_view` over a range with a long turn returns verbatim content, not a 200-char
  snippet.
- **prerequisites:** TASK-1.

### TASK-4 — Behavioral tests
- **files:** `tests/test_flow_files_read.py`, the existing session-view test module
- **action:** Real-FS, no-LLM tests; construct deps with `tool_results_dir=tmp_path`.
  - `file_read` (a): a no-range read of a file longer than `READ_MAX_LINES` returns exactly `READ_MAX_LINES`
    lines plus a continuation hint; a ranged read is clamped likewise.
  - `file_read` (b): a normal read returns content inline (no `PERSISTED_OUTPUT_TAG`).
  - `file_read` (c): a line longer than 2000 chars (within a small read) is returned **verbatim** — content
    past char 2000 present, `…[truncated]` absent.
  - `session_view`: over a range with a turn longer than 200 chars, the returned content includes the turn
    text past char 200 (verbatim).
  - *(A spill-at-`READ_MAX_CHARS` test is only meaningful if the backstop is kept low enough to fire — defer
    until the Open-Questions value/keep decision is resolved.)*
- **done_when:** `uv run pytest tests/test_flow_files_read.py <session-view-module> -x` passes (piped to a
  timestamped `.pytest-logs/` file per `CLAUDE.md`).
- **success_signal:** reads page at `READ_MAX_LINES` and return verbatim content with no per-unit truncation.
- **prerequisites:** TASK-1, TASK-2, TASK-3.

### TASK-5 — Update the parent plan's ISSUE-5 block; cross-reference the sibling
- **files:** `docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md`
- **action:** Replace the ISSUE-5 body with an extraction pointer to this plan and the sibling
  `2026-06-05-150129-l2-spill-tail-protection.md` (noting the dedup vs visibility split). Update the
  loop-stability eval's `prerequisites` line. Touch only the ISSUE-5 block and the eval prerequisite line.
- **done_when:** ISSUE-5 reads as an extraction stub citing both plans; no other parent section changes.
- **success_signal:** N/A (doc pointer).
- **prerequisites:** none.

## Testing

- Scoped: `tests/test_flow_files_read.py` and the session-view test module (real FS, no LLM).
- Loop-stability eval lives in the **parent** plan; visibility is proven by the **sibling** plan.
- `scripts/quality-gate.sh full` at ship.

## Open Questions

- **`READ_MAX_CHARS`: keep as a high backstop, or drop entirely?** This is the central open decision.
  - *Drop (recommended).* With pagination (`READ_MAX_LINES`) bounding each read and L2 tail-protection
    (sibling) making it visible, the emission cap's remaining job — catch a single page too big to show even
    once — is already covered by the HTTP-400 `recover_overflow_history` path. Dropping it avoids the
    visibility conflict entirely. But "no emission spill for read tools" then needs a *clean* expression
    (e.g., a boolean `emission_spill=False`), not the `math.inf` we're removing — so the sentinel-removal
    becomes "replace with a boolean," not "replace with a finite cap."
  - *Keep, demoted.* Retain a finite `READ_MAX_CHARS` but set it **high** (a "can't fit even once" ceiling),
    so normal and dense legit pages pass through to L2's protected tail and stay visible. A *low* value
    (e.g., 32k) re-breaks visibility by spilling dense >32k pages at emission before the tail can preserve
    them — so if kept, the value should rise toward / above the largest legitimate single page (the
    `32_000` from the earlier primary-cap design was chosen for a stacking-math that no longer applies).
- **`READ_MAX_LINES` value (500).** Pairs with the small-model "keep reads inline" goal; tightens the ranged
  ceiling (2000→500) and session clamp (200→500). Diverges from opencode's `MAX_LINES = 2000`. Adjustable.

---

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev read-view-emission-spill-cap`
