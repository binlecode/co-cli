# read-view-emission-spill-cap

> **Extracted from `docs/exec-plans/active/2026-06-02-210659-context-stability-sizing-control.md` (ISSUE-5).
> Sibling: `2026-06-05-150129-l2-spill-tail-protection.md`.**
> Scope evolved during planning, then narrowed again at Gate 1. This plan is now purely the **read/view
> limit-constant cleanup**: dedup the three per-tool line-*count* caps into one pagination cap
> (`READ_MAX_LINES`), drop `session_view`'s byte cap + 200-char preview (verbatim turns), and **keep**
> `file_read`'s per-line char clip (`_READ_MAX_LINE_CHARS=2000`) — it bounds the degenerate single-line case
> and is peer-aligned (opencode + hermes both retain a 2000-char per-line clip).
>
> **Two things this plan no longer does** (both resolved at Gate 1):
> - The **visibility guarantee** ("the model must see what it just read") is an L2 force-spill problem, not
>   an emission-cap one — owned entirely by the **sibling plan** above.
> - The read/view tools **keep `spill_threshold_chars=math.inf`** as-is. No emission cap, no `READ_MAX_CHARS`
>   constant, no sentinel removal. See *Resolved Decisions*.

## Why the emission cap is no longer the fix (read this first)

The original ISSUE-5 framing was "the four read/view tools bypass per-emission spill (`∞`), so an oversized
read lands inline unbounded — give them a finite emission cap." Tracing the spill tiers in source showed
that framing is wrong:

- A read tool's purpose is to put content **in front of the model**. The `∞` emission bypass exists for
  exactly that.
- But the model's *first* sight of a tool result is the **next** request, and `spill_largest_tool_results`
  (L2) runs on that request *before* it's sent — collecting **every** tool return with no tail protection
  (`history_processors.py:312-321`) and force-spilling largest-first (`:341`). So a freshly-read large doc
  is stubbed **before the model ever sees it** (sibling plan documents this fully).
- Therefore an L1 **emission** cap doesn't deliver visibility — and a *low* one actively harms it: spilling
  a read at emission removes it before L2's tail-protection (the sibling fix) can preserve it. **Emission
  spill and visibility are in tension.**

So visibility is restored by the **L2 tail-protection sibling plan** (preserve the recent tail in
`spill_largest_tool_results`, mirroring L3). Keeping `math.inf` at emission is the *most* aligned choice:
the read lands inline (never spilled at emission), and the sibling's tail-protection keeps it visible. The
two compose with **zero new machinery**. This plan therefore keeps only the parts that are genuinely about
the read/view tools themselves — pagination and the duplicated limit constants.

## Context

Each of the four read/view tools (`spill_threshold_chars=math.inf`) grew its **own ad-hoc limit constants**,
with no shared bound:

| Tool | Decorator | Limit constants today |
|---|---|---|
| `file_read` | `read.py:397` | `_READ_DEFAULT_LIMIT_LINES = 500`, `_READ_MAX_LINES = 2000`, `_READ_MAX_LINE_CHARS = 2000`, `_READ_MAX_FILE_BYTES = 500_000` |
| `session_view` | `view.py:25` | `_SESSION_TURN_MAX_LINES = 200`, `_SESSION_TURN_MAX_BYTES = 16 KB`, + a per-turn 200-char preview (`content[:200]`) |
| `memory_view` | `memory/view.py:22` | none |
| `skill_view` | `system/skills.py:37` | none at read time (`_MAX_SKILL_CHARS` is write-side validation) |

The duplication is the problem this plan owns:

- **Three line-count caps** (`500`/`2000`/`200`) across two tools do the same job with different values.
- **`session_view`'s two byte/preview knobs** (`_SESSION_TURN_MAX_BYTES`, the 200-char preview)
  are **implicit proxies** for "don't let one read get too big" — confusing because their names describe a
  per-turn unit while their real job is output-size protection they only half-do. Worse,
  `session_view`'s 200-char preview **defeats the tool's stated purpose**: the docstring promises "the exact
  turn content … rather than the chunk-level snippet" (`view.py:36-38`) but it returns `content[:200]` per
  turn (`view.py:82`) — a second snippet view, not verbatim content.
- **`_READ_MAX_LINE_CHARS=2000` is NOT in this dedup.** It is a per-line clip (not a line-*count* cap and not
  a partial proxy): it bounds a single pathological line — a minified-JS file that is one 400 KB line, which
  `READ_MAX_LINES` pagination cannot bound (it's one line) and `_READ_MAX_FILE_BYTES` only guards on no-range
  reads. It is **kept**, peer-aligned (opencode `MAX_LINE_LENGTH=2000` `read.ts:164`; hermes
  `MAX_LINE_LENGTH=2000` `file_operations.py:566,735-736`).

**The `math.inf` bypass is left as-is.** It was originally flagged as a "sentinel-value smell" (a numeric
threshold encoding the binary "exempt from emission spill," widening the type to `int | float` and forcing a
`math.isinf` special-case). At Gate 1 the decision was to **keep it**: read tools must not emission-spill at
all (that is what makes them land inline for the sibling's tail-protection to preserve), and `math.inf`
already expresses exactly that. Replacing it with either a finite cap or a new boolean flag adds machinery
for no behavioral gain — a finite cap low enough to be useful re-breaks visibility, and one high enough to
be safe is nearly inert. Minimal change wins.

**The cleanup:** one pagination cap (`READ_MAX_LINES`) replaces the three line-count caps; `session_view`'s
byte cap + 200-char preview are removed (verbatim turns). `file_read`'s per-line clip is kept.

### Peer-survey alignment (`docs/reference/RESEARCH-context-management-peer-survey.md`)

Correcting the parent survey (verified at peer HEADs): **opencode** bounds live reads with `MAX_LINES =
2000` + a **per-line clip `MAX_LINE_LENGTH = 2000`** (`tool/read.ts:14-16,164`) + total `MAX_BYTES = 50 *
1024`, **recoverable** (`tool/truncate.ts:16-17`); its `TOOL_OUTPUT_MAX_CHARS = 2000` is a separate
compaction-path trim, not the live cap (the survey conflated them). **hermes** has the *same* live shape —
`MAX_LINES = 2000` + **per-line clip `MAX_LINE_LENGTH = 2000`** (`file_operations.py:565-566,735-736`) +
`MAX_FILE_SIZE = 50 KB` + a `file_read_max_chars` total cap (`file_tools.py`); the parent survey's "hermes
has no live cap" is **wrong**. **openclaw** (pi-mono coding-agent core) bounds by total bytes
(`DEFAULT_MAX_BYTES` 32 KB/page, adaptive→128 KB) with a dedicated `firstLineExceedsLimit` branch for the
single-line case (`core/tools/read.ts:110,219`, `truncate.ts`). **codex** trims schemas only. So all three
agent peers **do** bound live reads, and the two line-based ones (opencode, hermes) keep a 2000-char
per-line clip identical to co's `_READ_MAX_LINE_CHARS` — which is exactly why this plan **keeps** it. co's
shape — line-count pagination + the retained per-line clip, with `math.inf` at emission and recoverability
owned by L2/L3 + the HTTP-400 overflow path — keeps a tighter line *count* (`500`) than opencode so normal
reads stay inline.

## Problem & Outcome

**Problem.** The read/view tools carry duplicated line-count caps (three: `500`/`2000`/`200`) and
`session_view`'s two byte/preview knobs whose preview defeats its own purpose. (The *visibility* defect — a
fresh read spilled before the model sees it — is the sibling plan's; not solved here. The `math.inf`
emission bypass is kept, not changed. `file_read`'s per-line clip `_READ_MAX_LINE_CHARS` is kept, not part of
the dedup.)

**Outcome.**
- **`READ_MAX_LINES = 500`** (new shared global) — one pagination cap for `file_read` and `session_view`,
  with the existing continuation hint paging forward. Dedupes `_READ_DEFAULT_LIMIT_LINES`,
  `_READ_MAX_LINES`, `_SESSION_TURN_MAX_LINES`.
- **`session_view` returns verbatim** — the 200-char preview and `_SESSION_TURN_MAX_BYTES` are removed; turns
  are bounded only by `READ_MAX_LINES` (turn count) + L2/HTTP-400, with **no per-turn char clip** (the turn,
  unlike a line, is a semantic unit that is legitimately multi-thousand chars — see *Resolved Decisions*).
- **`file_read` keeps `_READ_MAX_LINE_CHARS=2000`** — the per-line clip is retained (peer-aligned), so a
  pathological single 400 KB line still clips. Not part of the line-count dedup.
- **`spill_threshold_chars=math.inf` is unchanged** on all four read/view decorators — no emission cap.

Normal reads — a default 500-line `file_read` (~26k chars), a typical `session_view` range, an ordinary
memory/skill body — stay fully inline and (via the sibling plan) visible. The safety net for a pathological
single read is pagination (`READ_MAX_LINES`) + the sibling's L2 tail-protection + the HTTP-400
`recover_overflow_history` path — not an emission cap (see *Resolved Decisions*).

## Scope

**In scope — read/view limit-constant cleanup:**
- Add `READ_MAX_LINES = 500` (pagination) to `tool_io.py`.
- Dedup the three line caps → `READ_MAX_LINES`: `_READ_DEFAULT_LIMIT_LINES`, `_READ_MAX_LINES` (`read.py`),
  `_SESSION_TURN_MAX_LINES` (`view.py`).
- `session_view` only: remove `_SESSION_TURN_MAX_BYTES` + the byte-accumulation loop and the 200-char
  preview, returning verbatim turns (`view.py`). No per-turn char clip is added.
- Scoped behavioral tests; update the parent plan's ISSUE-5 block.

**Retained — unchanged on purpose:**
- **`_READ_MAX_LINE_CHARS = 2000`** + its per-line truncation branch (`read.py:59-60`) — peer-aligned per-line
  clip (opencode/hermes both keep `MAX_LINE_LENGTH=2000`); bounds the single pathological line that
  `READ_MAX_LINES` pagination cannot. Kept (Gate-1 decision).
- **`spill_threshold_chars=math.inf`** on all four read/view decorators, and all the machinery it implies:
  the `math.isinf` branch (`tool_io.py:138`), the `int | float` type widths (`tool_io.py:134`,
  `agent_tool.py:34`, `deps.py:109`), and `import math` in `tool_io.py` + the four tool modules. **All kept**
  — read tools deliberately never emission-spill (Gate-1 decision).
- `_READ_MAX_FILE_BYTES = 500_000` — I/O-safety guard (`file_read` `read_text()`s the whole file before
  slicing, `read.py:456`; pagination doesn't prevent slurping a huge file). Kept.
- `_MAX_SKILL_CHARS = 50_000`, `_MAX_DESCRIPTION_CHARS = 1024` — write-side validation for
  `skill_create`/`edit`/`patch` (`system/skills.py:69-70,81-90`), not reading. Kept.
- `TOOL_RESULT_PREVIEW_CHARS = 1_500`, `SPILL_THRESHOLD_CHARS = 4_000` — spill-preview length / non-read-tool
  default. Untouched.

**Out of scope (owned elsewhere):**
- **The visibility guarantee** ("model sees what it read") — sibling `2026-06-05-150129-l2-spill-tail-protection.md`.
- The L2 `spill_largest_tool_results` path generally and the HTTP-400 recovery path.
- Any emission cap / `math.inf` removal / `READ_MAX_CHARS` — dropped at Gate 1 (see *Resolved Decisions*).
- Streaming `file_read` (read only the needed lines) — an I/O optimization.
- The loop-stability eval extension — parent plan.
- `docs/specs/` updates (`sync-doc` post-delivery), including the pagination model.

## Behavioral Constraints

- **One pagination cap** — one `READ_MAX_LINES` for line *count*; no second line-count ceiling. (The per-line
  char clip `_READ_MAX_LINE_CHARS` is a distinct, retained guard, not a duplicated count proxy.)
- **`session_view` returns verbatim** — the tool exists to give exact turn content.
- **Read tools keep `math.inf`** — do not introduce an emission cap, a finite threshold, or a boolean
  bypass flag for the read/view tools. Their landing-inline behavior is what the sibling's tail-protection
  relies on.
- **Surgical** (`CLAUDE.md`) — touch only the line-count constants, `session_view`'s byte cap + preview,
  scoped tests, and the parent-plan pointer. The `math.inf` machinery, `_READ_MAX_LINE_CHARS` + its per-line
  truncation, and the other retained guards are untouched.

## High-Level Design

Add to `co_cli/tools/tool_io.py`:

```python
# Pagination cap for the read/view tools that page over a source (file_read,
# session_view). One read returns <= READ_MAX_LINES lines/turns; the continuation
# hint pages forward. 500 of typical source (~52 chars/line incl. line-number
# prefix) is ~26k chars — inline and (via the L2 tail-protection sibling) visible.
READ_MAX_LINES = 500
```

**`file_read`** — `_compute_read_slice` uses `READ_MAX_LINES` for both the no-range default and the ranged
ceiling (removing `_READ_DEFAULT_LIMIT_LINES`, `_READ_MAX_LINES`). `_build_read_display` is **unchanged** —
`_READ_MAX_LINE_CHARS` and its per-line truncation (`read.py:59-60`) stay. Decorator
`spill_threshold_chars=math.inf` **unchanged**. `_READ_MAX_FILE_BYTES` untouched.

**`session_view`** — `_SESSION_TURN_MAX_LINES` → `READ_MAX_LINES`; remove `_SESSION_TURN_MAX_BYTES` + the
byte-accumulation break loop; return verbatim turns (`content_preview` field → full `content`, drop
`[:200]`). Decorator `spill_threshold_chars=math.inf` **unchanged**.

**`memory_view`, `skill_view`** — no change (they carry `math.inf` and no read-time line caps; nothing in
this plan touches them).

## Tasks

### ✓ DONE TASK-1 — Add `READ_MAX_LINES`
- **files:** `co_cli/tools/tool_io.py`
- **action:** Add `READ_MAX_LINES = 500` (with the comment above) to `tool_io.py`. No decorator changes, no
  `math.inf` removal, no type narrowing — read tools keep `spill_threshold_chars=math.inf`.
- **done_when:** `READ_MAX_LINES` exists in `tool_io.py`; `grep -rn "math.inf" co_cli/tools/` still shows the
  four read/view decorators unchanged.
- **success_signal:** N/A (constant introduction; behavior verified by TASK-4).
- **prerequisites:** none.

### ✓ DONE TASK-2 — `file_read`: dedup line-*count* caps → `READ_MAX_LINES`
- **files:** `co_cli/tools/files/read.py`
- **action:** In `_compute_read_slice`, use `READ_MAX_LINES` for both read modes (remove
  `_READ_DEFAULT_LIMIT_LINES`, `_READ_MAX_LINES`). **Leave `_build_read_display`, `_READ_MAX_LINE_CHARS` and
  the per-line truncation branch (`read.py:59-60`) untouched** — the per-line clip is retained. Leave the
  `math.inf` decorator and `_READ_MAX_FILE_BYTES` untouched.
- **done_when:** the two line-*count* constants no longer exist; both read modes return ≤ `READ_MAX_LINES`
  lines with the continuation hint; `_READ_MAX_LINE_CHARS` and its truncation branch still exist; the
  full-file I/O guard and the `math.inf` decorator are unchanged.
- **success_signal:** a no-range and a ranged read each return ≤500 lines with a working hint; a >2000-char
  line is still clipped to 2000 chars + `...[truncated]`.
- **prerequisites:** TASK-1.

### ✓ DONE TASK-3 — `session_view`: dedup line cap, return verbatim turns, drop byte cap
- **files:** `co_cli/tools/session/view.py`
- **action:** `_SESSION_TURN_MAX_LINES` → `READ_MAX_LINES`. Remove `_SESSION_TURN_MAX_BYTES` and the
  byte-accumulation break loop; return verbatim turn content (`content_preview` field → `content`, drop
  `[:200]` at `view.py:82`). **Update the docstring** (`view.py:40,42-43`): drop "Refuses ranges over 200
  lines or content over 16KB" (now ≤`READ_MAX_LINES` lines, no byte cap) and rename the documented
  `content_preview` field → `content`. The structured-return rename is breaking by design (zero-backward-compat,
  no alias) — its only consumers are the two assertions in TASK-4. Leave the `math.inf` decorator untouched.
- **done_when:** `_SESSION_TURN_MAX_LINES` / `_SESSION_TURN_MAX_BYTES` gone; range clamped by
  `READ_MAX_LINES`; a turn longer than 200 chars is returned in full; docstring no longer cites the 200-line /
  16KB refusal or the `content_preview` field.
- **success_signal:** `session_view` over a range with a long turn returns verbatim content, not a 200-char
  snippet.
- **prerequisites:** TASK-1.

### ✓ DONE TASK-4 — Behavioral tests
- **files:** `tests/test_flow_files_read.py`, the existing session-view test module
- **action:** Real-FS, no-LLM tests; construct deps with `tool_results_dir=tmp_path`.
  - `file_read` (a): a no-range read of a file longer than `READ_MAX_LINES` returns exactly `READ_MAX_LINES`
    lines plus a continuation hint; a ranged read is clamped likewise.
  - `file_read` (b): a normal read returns content inline (no `PERSISTED_OUTPUT_TAG`).
  - `file_read` (c): a line longer than 2000 chars (within a small read) is **clipped** — content past char
    2000 absent, `...[truncated]` present (the retained `_READ_MAX_LINE_CHARS` guard).
  - `session_view`: over a range with a turn longer than 200 chars, the returned content includes the turn
    text past char 200 (verbatim — no per-turn clip). **Update the two existing assertions in
    `test_flow_session_view.py` (lines ~155, ~187) from `content_preview` → `content`** to match the renamed
    field; without this they read a missing key and fail.
- **done_when:** `uv run pytest tests/test_flow_files_read.py <session-view-module> -x` passes (piped to a
  timestamped `.pytest-logs/` file per `CLAUDE.md`).
- **success_signal:** reads page at `READ_MAX_LINES`; `file_read` still clips a >2000-char line;
  `session_view` returns verbatim turns.
- **prerequisites:** TASK-1, TASK-2, TASK-3.

### ✓ DONE TASK-5 — Update the parent plan's ISSUE-5 block; cross-reference the sibling
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

## Resolved Decisions (Gate 1)

- **Emission cap / `READ_MAX_CHARS` — DROPPED; keep `math.inf` (resolved).** The read/view tools keep
  `spill_threshold_chars=math.inf` with no replacement (no finite cap, no boolean flag). Verified reasoning:
  with `num_ctx = 65_536` (64k), L2/L3 triggering at `0.50 × 64k = 32k`, and pagination capping a page at
  ~5–9k tokens, a single read cannot overflow the real window; the genuine pathological case is caught by the
  `recover_overflow_history` HTTP-400/413 path (strip-all-returns → summarize, recoverable). A finite
  emission cap is squeezed out: a value low enough to be useful (e.g. 32k chars) spills a dense 500-line
  markdown page (~36.5k chars) and re-breaks the sibling's visibility fix; a value high enough to be safe
  (≥~48k) is nearly inert. Keeping `math.inf` is the minimal expression of "read tools land inline," which is
  exactly what the sibling's tail-protection preserves. (Naming aside: a cap, if ever added, would *not* be a
  global `TOOL_RESULT_MAX_CHARS` — non-read tools already spill at `SPILL_THRESHOLD_CHARS = 4_000`, so any
  such bound is read-scoped by nature, not universal.)
- **`READ_MAX_LINES` value — 500 (resolved, kept).** Pairs with the small-model "keep reads inline" goal;
  tightens the ranged ceiling (2000→500) and session clamp (200→500). Diverges from opencode's
  `MAX_LINES = 2000` deliberately: at co's 32k operational budget, a 500-line page is ~5–9k tokens (inline
  with room), whereas a 2000-line page is ~21–37k tokens — up to the whole window in one read. Adjustable
  later if pagination round-trips prove costly, but 500 is the right default for the 32k/64k regime.
- **`_READ_MAX_LINE_CHARS=2000` — KEPT (resolved).** Originally slated for removal as a "partial byte proxy."
  Reversed at Gate 1 after verifying peers: opencode (`read.ts:164`) and hermes (`file_operations.py:735-736`)
  both keep an identical 2000-char per-line clip, and openclaw has a `firstLineExceedsLimit` branch for the
  same case. The clip is the *only* guard on the degenerate single-line file (a 400 KB minified-JS line):
  `READ_MAX_LINES` can't bound a 1-line file, and `_READ_MAX_FILE_BYTES` guards no-range reads only, so a
  ranged read of such a file would otherwise land the whole line inline. Removing it would diverge from all
  three line/byte-bounding peers and rest on a survey claim ("hermes has no live cap") that is factually
  wrong. The parent-survey correction above now reflects this.
- **`session_view` — no per-turn char clip (resolved).** `file_read` clips per *line*; `session_view` does
  **not** get an analogous per-turn clip. The units differ: a line is fine-grained and 2000 chars rarely hits
  real content, but a transcript turn is legitimately multi-thousand chars, so a 2000-char per-turn clip would
  shred normal turns and re-break the "exact turn content" purpose this plan restores. `session_view` is
  bounded by `READ_MAX_LINES` (turn count) + L2/HTTP-400 recovery instead.

---

> **Gate 1 — APPROVED (2026-06-05).** Right problem, correct scope. Revisions folded in during review:
> keep `_READ_MAX_LINE_CHARS=2000` (peer-aligned per-line clip; dedup is line-*count* only), corrected the
> hermes peer-survey claim, set `session_view` to verbatim turns with no per-turn clip, captured the
> `content_preview`→`content` field rename + docstring/test coupling, fixed the `system/skills.py` citations.
> Next: `/orchestrate-dev read-view-emission-spill-cap`

## Delivery Summary — 2026-06-05

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `READ_MAX_LINES` in `tool_io.py`; four `math.inf` decorators unchanged | ✓ pass |
| TASK-2 | `_READ_DEFAULT_LIMIT_LINES`/`_READ_MAX_LINES` gone; both read modes use `READ_MAX_LINES`; `_READ_MAX_LINE_CHARS` + truncation + `_READ_MAX_FILE_BYTES` + `math.inf` retained | ✓ pass |
| TASK-3 | `_SESSION_TURN_MAX_LINES`/`_SESSION_TURN_MAX_BYTES` gone; clamp by `READ_MAX_LINES`; verbatim `content` field; docstring no longer cites 200-line/16KB refusal or `content_preview` | ✓ pass |
| TASK-4 | `pytest tests/test_flow_files_read.py tests/test_flow_session_view.py -x` passes | ✓ pass |
| TASK-5 | ISSUE-5 in parent plan reads as extraction stub citing both siblings; eval prerequisite updated | ✓ pass |

**Tests:** scoped — 26 passed, 0 failed (`test_flow_files_read.py` + `test_flow_session_view.py`).
**Doc Sync:** fixed — `docs/specs/tools.md` Files table corrected (`session_search`/`session_view` were wrongly attributed to `co_cli/tools/memory/`; moved to their real homes under `co_cli/tools/session/`). All other specs clean; no spec referenced the dropped `content_preview` field, the 200-line/16KB refusal, or the old line-count constants.

**Implementation notes:**
- The new `file_read` tests required wiring the test `RunContext` with `tool_name="file_read"` + a populated `deps.tool_index` so the registered `spill_threshold_chars=math.inf` is actually applied. Without it, the tool-name lookup misses and `tool_output` falls back to the 4000-char default, which spills a 500-line read — masking the inline-read behavior the plan relies on. Added an optional `tool` param to the module's `_ctx` helper for this (existing tests unchanged: they pass `None` and keep the old fallback, which is fine for their small outputs).

**Out-of-scope files NOT staged** (present in working tree, untouched by this delivery): `.claude/skills/clean-tests/SKILL.md`, `tests/test_flow_skill_manifest.py`, `tests/test_session_usage.py` — pre-existing / coworker edits unrelated to this plan.

**Overall: DELIVERED**
All five tasks passed `done_when`, lint clean, scoped tests green (26 passed), doc sync fixed an adjacent stale path in `tools.md`.

## Implementation Review — 2026-06-06

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `READ_MAX_LINES` in `tool_io.py`; four `math.inf` decorators unchanged | ✓ pass | `tool_io.py:51-55` — constant=500 with comment above (not trailing); `math.inf` intact at `read.py:395`, `session/view.py:22`, `memory/view.py:22`, `system/skills.py:37`; `int \| float` widths + `math.isinf` branch preserved (`tool_io.py:140,144,254`) |
| TASK-2 | line-count caps gone; both modes ≤ `READ_MAX_LINES` w/ hint; per-line clip + file-byte guard + `math.inf` retained | ✓ pass | `read.py:25` imports `READ_MAX_LINES`; used both branches `read.py:40,43`; `_compute_read_slice` called at `read.py:462`; clip retained `read.py:27,57-58`; `_READ_MAX_FILE_BYTES` at `read.py:28,445`; docstring "up to 500 lines" accurate |
| TASK-3 | `_SESSION_TURN_MAX_*` gone; clamp by `READ_MAX_LINES`; verbatim turn; docstring drops 200-line/16KB + `content_preview` | ✓ pass | `view.py:13` import; clamp `view.py:68-70`; byte-loop removed (zero hits); field `content` verbatim `view.py:82`; display uses `entry['content']` `view.py:92`; docstring `view.py:37-41` clean; `math.inf` `view.py:22` |
| TASK-4 | `pytest tests/test_flow_files_read.py tests/test_flow_session_view.py -x` passes | ✓ pass | 4 behaviors covered (pagination both modes, inline, line-clip, verbatim>200); no mocks/patching; `content_preview`→`content` updated; `_ctx(deps, file_read)` wires real `math.inf`; all assertions functional |
| TASK-5 | ISSUE-5 → extraction stub citing both siblings; eval prerequisite updated | ✓ pass | parent plan diff = ISSUE-5 stub + eval prereq line only; no other section changed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale ref: eval Action names dropped "ISSUE-5 emission cap" mechanism | parent plan `:196` | minor | Updated to "drop-reported trigger + L2 tail-protection — read tools land inline, not emission-capped" |
| Scope-creep: `.claude/skills/clean-tests/SKILL.md` in working tree | — | n/a | Not this delivery (coworker edit) — flagged, not staged |
| Scope-creep: `tests/test_flow_skill_manifest.py`, `tests/test_session_usage.py` | — | n/a | Pre-existing edits — flagged, not staged |

Adversarial pass confirmed the breaking `content_preview`→`content` rename has **zero orphaned consumers** across `co_cli/`, `tests/`, `evals/` (only the renamed code + updated test assertions reference it); no circular import (`tool_io` imports neither `read.py` nor `view.py`); pagination assertion `"line 501" not in body` is sound (hint reads `start_line=501`, no substring collision).

### Tests
- Command: `uv run pytest`
- Result: **647 passed, 0 failed** (1 warning, unrelated)
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Behavioral Verification
- System boot: clean import of all three changed modules — `READ_MAX_LINES=500`.
- `file_read` real invocation (778-line `co_cli/main.py`): returns exactly 500 numbered lines + `[278 more lines — use start_line=501 to continue reading]`, lands **inline** (no `<persisted-output>`) — `success_signal` verified (no-range read ≤500 lines with working hint; `math.inf` keeps it inline).
- `file_read` long-line clip + `session_view` verbatim-past-200-chars: verified by real-FS functional tests in the green suite (`success_signal` for TASK-2 clip + TASK-3 verbatim).

### Overall: PASS
All five tasks confirmed against source with file:line evidence; full suite green; one minor doc-staleness fixed; the breaking field rename verified to have no orphaned consumers. Ready for Gate 2 → `/ship`.
