# User Image Intake — Path-Reference → image_view Routing

## Context

co-cli is a single-process Rich REPL. Its input layer is **text-only**: no clipboard
paste, no drag-drop handler, no `@`-file mention. Image intake today is purely
*model-initiated* — the agent decides to call `image_view(path)` ([view.py#L57](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L57)),
which reads bytes and returns `ToolReturn(content=[prompt, BinaryContent(...)])`
([view.py#L108](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L108)).
`image_view` is `DEFERRED` ([view.py#L53](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L53))
and gated on `deps.agent_vision_capable` via `check_fn=_vision_available`
([view.py#L55](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L55)) — hidden when the
model can't see.

The peer survey (`docs/reference/RESEARCH-tui-multimodal-peer-survey.md`) classes co's
*general* lack of a user-intake surface as off-axis/by-design — the agent pulls images by
choosing to call `image_view`, and that model-initiated philosophy is intentional, not a
defect. This plan does **not** build a general intake surface. It targets one concrete,
recurring user gesture the model-initiated path serves poorly: **dragging an image file
into the terminal**, which most terminals turn into the file's *path pasted as text*.
Today that path arrives as a bare string, `image_view` is hidden, and unless the agent
proactively `tool_view`s then `image_view`s, the pixels never load — so the user's
explicit "here, look at this" gesture silently no-ops. hermes handles the same gesture
deterministically (path detected → forwarded → spliced in before the call); co should too.

**hermes is the right parity model for co:** the TUI detects a path and forwards a
*reference*; the backend splices the image into context before the LLM call (the TUI
never holds bytes). co has no backend split — REPL and agent loop are one process — so
the co-equivalent of "backend splices it in" is: the turn layer detects the
user-referenced path and routes it through `image_view`'s byte-read mechanism to attach
`BinaryContent` to the user turn. This is opposite to opencode's client-side base64
plumbing, which co has no wire protocol to justify.

### Current-state check

Source verified accurate against this plan's scope:
- `run_turn` ([orchestrate.py#L737](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py#L737))
  takes `user_input: str` and seeds `_TurnState(current_input=user_input)` — the user
  prompt is a bare string; there is no content-part path today.
- `image_view`'s byte-read + validation (resolve under read boundary, exists/dir check,
  MIME by suffix, ≤20 MB, `read_bytes`) lives inline in the tool body
  ([view.py#L76-L112](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L76)) —
  not yet factored out for reuse.
- `_MEDIA_TYPES` (png/jpg/jpeg/webp/gif) and `_MAX_IMAGE_BYTES` (20 MB) are the single
  source of accepted suffixes and size cap ([view.py#L29-L40](/Users/binle/workspace_genai/co-cli/co_cli/tools/vision/view.py#L29)).
- The REPL turn is dispatched via `_run_foreground_turn(..., user_input=...)`
  ([main.py#L167](/Users/binle/workspace_genai/co-cli/co_cli/main.py#L167)).

## Problem & Outcome

**Problem:** When a user drags an image into the terminal, the file's path arrives as text
and the gesture no-ops — the agent would have to discover the hidden tool and call it on
its own to honor what was an explicit user request to look.

**Outcome:** When a user's submitted turn **is, in its entirety, a single supported image
path** (a "lone path" — the canonical drag-and-send form), the turn layer reads that image
through `image_view`'s shared read mechanism and splices the pixels into the user prompt as
`BinaryContent` — deterministically — so a vision-capable agent answers about it on the
same turn. Because the path is an explicit user gesture (not agent traversal), it is read
even when outside `file_search_roots` — see the user-gesture read allowance below. When the
agent model can't see, co emits one honest notice and proceeds text-only (no silent attach
to a blind model). Any input that is *not solely a path* — a path mentioned in a sentence
("fix the diff that broke logo.png"), or a path followed by a question — is NOT
auto-attached; that is talking about a file, not handing it over, and stays the agent's
call (it can still `image_view`).

**Failure cost:** Without this, the most common real-world multimodal gesture —
drag-an-image-into-the-terminal — silently no-ops on the pixels. The user believes co
looked at their image; it didn't.

## Scope

**In scope:**
- A turn-preprocessing step that detects a **lone image path** (the entire submitted input
  resolves to a supported image) and, for a vision-capable agent, attaches its bytes to the
  user turn via the shared `image_view` read core.
- Path normalization for drag-drop text forms: surrounding quotes, `\<space>` escapes,
  leading `file://` URIs, and `~` expansion.
- A **user-gesture read allowance**: a lone user-supplied path is read even if outside
  `file_search_roots`. The allowance is preprocessor-only; `image_view`'s agent path keeps
  the full read boundary.
- Factoring `image_view`'s byte-read + validation into a shared core used by both the
  tool and the preprocessor (no duplicated MIME/size logic; boundary stays caller-side).
- Threading a multimodal user prompt (text + `BinaryContent` part) through `run_turn`.
- Making the compaction token estimator `BinaryContent`-aware (a prerequisite — the same
  fix retro-fixes a latent `image_view` crash).
- Honest gate: vision-incapable agent → one notice, text-only turn.

**Out of scope:**
- Clipboard paste / OS clipboard reads (co's REPL has no clipboard surface; opencode-style
  intake is explicitly rejected — wrong parity for co).
- Any input that is not solely a path — mid-sentence mentions, or a path followed by a
  question (deliberately not auto-attached — see Problem & Outcome).
- Multiple images per turn (a lone path is exactly one).
- PDF/document intake (routes to the `documents` skill, unchanged).
- Terminal pixel rendering / graphics protocols (no peer does this).
- Voice/audio (no peer convergence; not a gap).
- Changing `image_view`'s agent-initiated behavior, its `DEFERRED`/gate config, or its read
  boundary.
- Stripping or rewriting the path out of the user's text.

## Behavioral Constraints

- **Honest vision gate preserved.** The preprocessor MUST consult
  `deps.agent_vision_capable` and attach bytes ONLY when True. A blind model is never
  handed pixels — same invariant `_vision_available` enforces for the tool.
- **Deterministic, not model-dependent.** Detection + attach happen in the turn layer
  before the LLM call (hermes parity: "spliced in before the call"). The feature does not
  rely on the model choosing to call a tool.
- **Reuse, not duplicate.** MIME suffix set, size cap, and byte-read come from one shared
  core; the preprocessor and `image_view` must not drift. Boundary resolution stays
  caller-side (see allowance below) — the core validates an already-resolved path.
- **Lone-path trigger only.** The turn qualifies only when its **entire** submitted text
  (after trimming, quote-stripping, `\<space>` unescaping, `file://` stripping, and `~`
  expansion) is a single path with a supported image suffix that resolves to an existing
  file. Anything more than a bare path — trailing text, a question, a mid-sentence mention
  — is ignored. This is the false-positive guard and yields at most one image per turn.
- **User-gesture read allowance.** A lone path the user supplied is read even when it
  resolves outside `file_search_roots`. Rationale: the boundary exists to stop the *agent*
  from wandering the filesystem, not to stop the *user* from referencing their own files —
  the user already has FS read access and named the exact path as a deliberate foreground
  gesture. The allowance is strictly scoped: preprocessor-only (never reachable by the
  model), read-only, one explicitly-named image file, still gated on
  `agent_vision_capable`. `image_view`'s agent-initiated path retains the full
  `enforce_read_boundary` check unchanged.
- **Bounded context.** The existing per-image ≤20 MB cap is the sole bound. Note: the
  triggering (tail) turn is **unelided** by design — `elide_old_multimodal_prompts` only
  strips base64 from non-tail user turns — so the ≤20 MB cap is load-bearing for the turn
  that attaches.
- **Token estimate must be `BinaryContent`-aware.** `estimate_message_tokens`
  (summarization.py#L59-60) `json.dumps`-es any list `content`; a `[text, BinaryContent]`
  user prompt would raise `TypeError` (raw `bytes` not serializable), crashing the spill
  processor (history_processors.py#L425, runs every request over the un-elided tail),
  `/compact`, and the summary-budget calc. The estimate MUST count a `BinaryContent` as a
  **bounded flat image-token constant**, never its base64 byte length — counting bytes
  (~1.7 M "tokens" for a 5 MB image) would spuriously trigger compaction or loop
  overflow-recovery (compacting *history* cannot shrink the *current tail* image). See
  TASK-0; this also retro-fixes the same latent crash on `image_view`'s native path.
- **No silent failure.** A detected-but-rejected image (oversize, unreadable, blind model)
  emits exactly one user-visible notice; the turn still runs with text.
- **Expected on-disk effect (accepted, not reclaimed).** The attached image's base64 lands
  in the session JSONL via `persist_session_history` — identical to what `image_view`'s
  native path already writes. Elision is outbound-only (it shrinks what is re-sent to the
  model, not what is on disk), so persisted pixels are reclaimed only when their turn ages
  out of the compaction window (summarized away) — never proactively stripped. An
  image-heavy session's JSONL therefore grows monotonically; this is **accepted** (matches
  `image_view`, preserves exact replay fidelity, and the ≤20 MB per-image cap bounds each
  attach). The transcript extractor skips non-text content, so FTS indexing is unaffected.

## High-Level Design

**1. Shared image-read core (refactor).** Extract the byte-read + validation from
`image_view` into a module-level helper in `co_cli/tools/vision/` that takes an
**already-resolved** `Path` and returns either a `BinaryContent` (with resolved name +
media_type) or a structured rejection (reason string). The core does exists/dir/MIME/size/
`read_bytes` only. **Boundary resolution stays at the caller:** `image_view` keeps
`enforce_read_boundary(Path(path), roots)` then calls the core; the preprocessor resolves
via the user-gesture path (below, no boundary) then calls the same core. This is what lets
the allowance apply to the user gesture without touching the agent's boundary.

**2. Lone-path detection + resolution (turn preprocessor).** A new function in the vision
package takes the submitted text and returns a resolved `Path` only if the **entire**
trimmed input is one image path, else `None`. Normalization order: strip a single pair of
surrounding quotes (`"` or `'`); strip a leading `file://` (URL-decoding `%20` etc.);
unescape `\<space>`; `expanduser()`; resolve a relative path against `workspace_dir`. The
result qualifies only if its suffix ∈ `_MEDIA_TYPES` and it `exists()`. No tokenization of
trailing text — if anything remains after the path (a question, more words), it is not a
lone path and returns `None`. (`shlex` is rejected — NL with unbalanced apostrophes
raises.)

**3. Gate + assembly (REPL turn path).** In `_handle_one_input`'s **plain-text branch**,
immediately before `_run_foreground_turn` (main.py#L433):
- If the input is not a lone image path: pass `user_input` through unchanged (today's
  behavior).
- If it is and `not deps.agent_vision_capable`: print one notice ("referenced an image but
  the current model can't see — proceeding text-only") and run with text only.
- If it is and vision-capable: read via the shared core. On success, build the multimodal
  prompt `[original_text, BinaryContent]` (text retained verbatim, including the path) and
  pass it to the turn. On rejection (oversize/unreadable), print one notice and run
  text-only.

The slash-command-delegated branch (`_run_foreground_turn(user_input=new_input)` at
main.py#L417) is **out of scope** — `new_input` is system-synthesized skill text, not a
user-typed path. Queued/mid-turn submissions re-enter through the plain-text branch
(`_drain_next` → `dispatch` → `_handle_one_input`), so they are covered automatically.

**4. Multimodal prompt threading.** Widen `run_turn`'s `user_input` to accept
`str | list[str | BinaryContent]` (pydantic-ai's `user_prompt` already accepts a sequence
of user-content parts; `BinaryContent ∈ UserContent`). `_TurnState.current_input` carries
the list; the span char-count at `orchestrate.py#L767` must change from
`len(user_input or "")` to sum `len(p)` over the `str` parts only (else a list miscounts
as part-count). Replay-elision is free: `elide_old_multimodal_prompts`
(history_processors.py#L570) already strips base64 from any non-tail `UserPromptPart` whose
content is a non-string sequence — no new elision work, do not reinvent it. The
length-retry path keeps the same prompt object (already does — it does not null
`current_input`). One downstream consumer is **not** free, however: the compaction token
estimator `json.dumps`-es list content and would crash on `BinaryContent` — fixed in
TASK-0, which is a prerequisite of this task.

This mirrors hermes exactly at the seam: **user references a path → system detects it →
bytes are spliced into the turn before the model call**, adapted to co's single process
(the "backend splice" is a turn-layer preprocessor, and the byte-read is `image_view`'s
own core rather than a separate gateway).

## Tasks

### ✓ DONE TASK-0: Make token estimation `BinaryContent`-aware (prereq)
- **files:** `co_cli/context/summarization.py`
- **done_when:** `estimate_message_tokens` returns a finite count (no `TypeError`) for a
  history containing a `UserPromptPart` whose content is a list `[text, BinaryContent]`,
  counting each `BinaryContent` as a bounded flat image-token constant rather than its
  serialized bytes — verified by a unit test asserting (1) no raise on such a history, and
  (2) the estimate for a multi-MB `BinaryContent` is within a small constant of the
  text-only estimate, not inflated by the byte length. Note in the test that this also
  covers `image_view`'s native list-content shape (latent crash, fixed here).
- **success_signal:** the compaction trigger, `/compact`, and summary-budget calc run
  without error on any turn that carries an attached image.
- **prerequisites:** none

### ✓ DONE TASK-1: Extract shared image-read core from image_view
- **files:** `co_cli/tools/vision/view.py` (+ a private helper module in
  `co_cli/tools/vision/` if the core warrants its own file)
- **done_when:** `image_view` produces identical `ToolReturn` output (same `return_value`
  string, same `BinaryContent`, same `tool_error` messages for missing/dir/unsupported/
  oversize) via a shared `(resolved_path: Path) -> BinaryContent | rejection` core (exists/
  dir/MIME/size/read_bytes), with `enforce_read_boundary` remaining in `image_view` ahead
  of the core call — verified by an assertion test that drives a real PNG through both the
  tool and the core and compares the resulting `BinaryContent.data`/`media_type`, plus a
  test that an out-of-roots path still raises the boundary error through `image_view`.
- **success_signal:** `image_view` behavior — including its read boundary — is unchanged.
- **prerequisites:** none

### ✓ DONE TASK-2: Lone image-path detector + resolution
- **files:** `co_cli/tools/vision/` (detector helper)
- **done_when:** given turn text and `workspace_dir`, the detector returns a single resolved
  `Path` **only when the entire trimmed input is one supported image path that exists**,
  else `None` — verified by a test over a temp dir with a real `.png` whose fixtures
  include: (a) lone absolute path → resolve; (b) lone path with `~` → resolve via
  expanduser; (c) lone `file://` URI (incl. `%20`) → resolve; (d) backslash-escaped-space
  lone path `/…/My\ Screens/shot.png` → resolve; (e) double-quoted lone path with spaces →
  resolve; (f) lone relative path against workspace_dir → resolve; (g) path **outside**
  `file_search_roots` (e.g. a temp Desktop) → resolve (user-gesture allowance); (h) path +
  trailing text `/…/shot.png what is this?` → `None`; (i) mid-sentence mention `fix the diff
  that broke logo.png` → `None`; (j) lone non-image path `main.py` → `None`; (k) lone
  image-suffixed path that does not exist → `None`.
- **success_signal:** drag-and-send of a path (quoted, escaped, `~`, or `file://`, in or out
  of read roots) yields one path; anything with trailing text or a mid-sentence mention
  yields none.
- **prerequisites:** TASK-1

### ✓ DONE TASK-3: Multimodal prompt threading through run_turn
- **files:** `co_cli/context/orchestrate.py`
- **done_when:** `run_turn` accepts `user_input: str | list[str | BinaryContent]`, seeds
  `_TurnState.current_input` with it, and the `co.user_prompt.chars` span attribute at
  `orchestrate.py#L767` sums `len(p)` over `str` parts only — verified by (1) an assertion
  that for a `[text, BinaryContent]` prompt the recorded char count equals `len(text)` (not
  the part count `2`), and (2) a live turn against the configured model (`llm.host`,
  `noreason_model_settings()`) with a small real `BinaryContent` that completes and whose
  reply references the image content.
- **success_signal:** a list user prompt with image bytes drives a normal turn and the span
  char-count is the text length.
- **prerequisites:** TASK-0, TASK-1 (the live turn flows the image through the compaction
  estimator, which must be `BinaryContent`-aware first)

### ✓ DONE TASK-4: REPL gate + assembly wiring
- **files:** `co_cli/main.py`
- **done_when:** the hook lives in the **plain-text branch** of `_handle_one_input`,
  immediately before `_run_foreground_turn` (main.py#L433), and the slash-delegated branch
  (#L417) is left untouched. Driving the plain-text turn path with a lone image-path input:
  (a) attaches `BinaryContent` and the model answers about the image when
  `agent_vision_capable=True`; (b) prints exactly one "can't see" notice and runs text-only
  when `agent_vision_capable=False`; (c) a slash-delegated `new_input` that is an image-like
  path does NOT trigger attach.
- **success_signal:** end-to-end, a user sending a lone image path on a vision-capable model
  gets a pixel-grounded answer in the same turn.
- **prerequisites:** TASK-2, TASK-3

## Testing

- **Token estimate (TASK-0):** a constructed history with a `[text, BinaryContent]` user
  prompt → `estimate_message_tokens` returns finite (no `TypeError`) and within a small
  constant of the text-only estimate (not byte-length-inflated). Pure unit test, no LLM.
- **Core parity (TASK-1):** real PNG through tool + core → identical `BinaryContent`;
  oversize/missing/dir/unsupported → identical rejection text; out-of-roots path still
  raises the boundary error through `image_view`. Real fixture image, no mocks.
- **Detector (TASK-2):** temp dir with a real `.png` (and a real `logo.png` for the
  mid-sentence decoy) plus a temp out-of-roots dir; fixtures (a)–(k) from the task's
  `done_when` — lone paths in every form (quoted/escaped/`~`/`file://`/relative/out-of-roots)
  resolve; path+trailing-text, mid-sentence mention, non-image, and non-existent do not.
- **Threading (TASK-3):** char-count assertion (`== len(text)`) + a live turn against the
  configured model (`llm.host`, `noreason_model_settings()`) with a small real image in a
  list prompt → completes, reply is image-grounded. Tail the log for call timing.
- **End-to-end (TASK-4):** plain-text foreground path with `agent_vision_capable` True
  (attach + grounded answer) and False (one notice + text-only); slash-delegated path with
  an image-like `new_input` does not attach. Real deps, real store.
- All pytest runs pipe to `.pytest-logs/$(date +%Y%m%d-%H%M%S)-*.log`; run with `-x`.

## Open Questions

All open questions resolved (C1 cycle + post-review owner decisions):
- **Trigger** — **resolved: lone-path only** (the entire input is the path). Narrowed from
  the C1 "leading-path" decision after an over-design review: "leads with a path + trailing
  text" forced trailing-text tokenization and admitted a residual false-positive ("shot.png
  is broken, fix it"). Lone-path deletes both, and the reach lost is narrow (only "path then
  question in one turn"). Whole-text token-sniff and explicit sigil both rejected.
- **Read boundary** — **resolved: user-gesture read allowance**. A lone user-supplied path
  is read even outside `file_search_roots`; without this the headline gesture (a screenshot
  dragged from `~/Desktop` into a project-rooted session) would be rejected, since
  `file_search_roots` defaults to `[workspace_dir]`. Allowance is preprocessor-only; the
  agent's `image_view` boundary is untouched. See the Behavioral Constraints rationale.
- **Path forms** — **resolved: normalize** quotes, `\<space>`, `file://`, and `~` in the
  detector. (`enforce_read_boundary` does not expanduser and the boundary would reject most
  drag sources, which is why resolution moved caller-side with the allowance.)
- **Per-turn count cap** — **resolved: dropped**. A lone path is exactly one image; the
  existing ≤20 MB per-image cap is the sole bound.
- **Literal tool-call vs shared-core** — **resolved: shared read core** (deterministic,
  gate-honoring). Literal model-emitted `image_view` call rejected — reintroduces
  model-dependence and the `tool_view` DEFERRED hop.
- **Detector placement** — **resolved: REPL plain-text branch** of `_handle_one_input`, so
  evals/non-REPL callers are unaffected and the notice is a console concern. The
  slash-delegated branch is out of scope.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev user-image-intake`

## Delivery Summary — 2026-06-14

| Task | done_when | Status |
|------|-----------|--------|
| TASK-0 | `estimate_message_tokens` finite (no `TypeError`) on `[text, BinaryContent]`, image counted as a bounded flat constant not byte length | ✓ pass |
| TASK-1 | `image_view` produces identical output via a shared `read_image` core; boundary stays in `image_view` | ✓ pass |
| TASK-2 | `detect_lone_image_path` resolves every lone drag form (abs/`~`/`file://`/escaped/quoted/relative/out-of-roots); rejects trailing-text/mid-sentence/non-image/missing | ✓ pass |
| TASK-3 | `run_turn` accepts `str \| list[str \| BinaryContent]`; span char-count sums str parts only; live list-prompt turn is image-grounded | ✓ pass |
| TASK-4 | lone image path attaches + model answers (vision); one notice + text-only (blind); slash-delegated path not auto-attached | ✓ pass |

**Files changed:**
- `co_cli/tools/vision/intake.py` (NEW) — shared `read_image` core + `detect_lone_image_path` (TASK-1/2)
- `co_cli/tools/vision/view.py` — `image_view` refactored onto the shared core (TASK-1)
- `co_cli/context/summarization.py` — `BinaryContent`-aware token estimate (TASK-0)
- `co_cli/context/orchestrate.py` — multimodal `user_input`, `_prompt_char_count` (TASK-3)
- `co_cli/main.py` — `_attach_user_image` + intake routing before slash dispatch (TASK-4)
- tests: `test_flow_vision.py`, `test_flow_compaction_summarization.py`, `test_flow_multimodal_prompt.py` (NEW), `test_flow_user_image_intake.py` (NEW)
- specs: `compaction.md` (estimator note), `tui.md` (intake routing)

**Tests:** scoped — 28 passed, 0 failed (across the 4 touched test files; 3 live vision turns + the rest model-free).
**Doc Sync:** fixed (narrow) — `compaction.md` §token-counting (`BinaryContent` = bounded flat constant) + `tui.md` REPL input routing (lone image path before slash dispatch).

**⚠ Placement deviation (owner-approved during dev):** The approved plan placed the detector in the **plain-text branch** of `_handle_one_input` ("Open Questions → Detector placement — resolved: REPL plain-text branch"). During dev this was found to silently miss the **headline gesture**: a bare absolute image path (`/Users/me/shot.png`) starts with `/`, so the slash-command check (`main.py`) intercepts it *before* the plain-text branch — it dies as "Unknown command." Per owner decision, the detector now runs **before slash dispatch**. Collision safety verified: the detector requires an image suffix and no slash command (builtin or skill) ends in one, so the command set and the image-path set are disjoint (test: `test_detect_returns_none_for_every_slash_command`). This supersedes the plan's "plain-text branch" Open-Question resolution.

**Overall: DELIVERED**
All five tasks pass `done_when`, lint clean, scoped tests green, docs synced. One owner-approved placement change (intake before slash dispatch) that the plan text should reflect when archived.

**Next step:** `/review-impl user-image-intake` — full suite + evidence scan + behavioral verification → verdict appended to plan.

## Implementation Review — 2026-06-14

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-0 | `estimate_message_tokens` finite (no `TypeError`) on `[text, BinaryContent]`; image = bounded flat constant, not byte length | ✓ pass | summarization.py:67-74 — list branch dispatches `BinaryContent`→`_IMAGE_TOKEN_ESTIMATE*CHARS_PER_TOKEN` (line 72, ≈1000 tok) before the `json.dumps` else (73-74); never serializes bytes. Test asserts no-raise + delta `<5_000` on a 5 MB fixture (test_flow_compaction_summarization.py:227-228) |
| TASK-1 | `image_view` identical output via shared `read_image` core; boundary stays in `image_view` ahead of core | ✓ pass | `read_image` (intake.py:45-71) does exists/dir/MIME/size/read_bytes, no boundary call. `image_view` calls `enforce_read_boundary` (view.py:62) then `read_image` (view.py:66) — order strict, early-return on boundary violation (63-64). Parity test compares `BinaryContent.data`/`media_type` tool-vs-core (test_flow_vision.py:228-243); out-of-roots still raises (105-111) |
| TASK-2 | `detect_lone_image_path` resolves every lone drag form; rejects trailing-text/mid-sentence/non-image/missing | ✓ pass | intake.py:74-113 — whole trimmed string is one candidate (no `.split`/`shlex`); normalization order quotes→`file://`+unquote→`\<space>`→expanduser→resolve-relative (91-110); suffix∈`_MEDIA_TYPES` + `exists()` gate (106-112). Trailing text poisons `.suffix`→None. Fixtures (a)-(k) real PNG, no mocks (test_flow_vision.py:268-307) |
| TASK-3 | `run_turn` accepts `str \| list[str \| BinaryContent]`; span char-count sums str parts only; live list-prompt turn image-grounded | ✓ pass | signature orchestrate.py:754; `_TurnState.current_input` (147); `_prompt_char_count` sums str parts only (739-747); span at 781. Length-retry keeps prompt object (803-814, explicit "do NOT null"). Live turn returned "Red" in 3.2s (test_flow_multimodal_prompt.py:55-86) |
| TASK-4 | lone image path attaches+answers (vision); one notice+text-only (blind); slash-delegated not attached | ✓ pass | `_attach_user_image` (main.py:329-350) returns `[user_input, image]` verbatim; intake at 410 before slash dispatch at 428; delegated `new_input`→`_run_foreground_turn` (448) bypasses detector. Tests: attach+"red", `count("can't see it")==1`, delegated stays `str`, every builtin command→None (test_flow_user_image_intake.py) |

### Issues Found & Fixed
No blocking issues found. Three minor, non-blocking notes (not fixed — none warrant a change):
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `_prompt_char_count` not internally None-safe | orchestrate.py:739-747 | minor | None is outside its type contract and unreachable from the sole call site (guarded by `if user_input is None` at main.py:377) — no change |
| Collision-safety proven empirically for builtin commands only, not skill names | test_flow_user_image_intake.py | minor | Skill slugs carry no dotted image suffix and would only collide if a same-named file existed; low risk — no change |
| Plan stated TASK-2 detector fixtures live in `test_flow_user_image_intake.py`; they actually live in `test_flow_vision.py` (alongside vision-core tests) | plan §Tasks/Testing | minor | Doc-only discrepancy, sensible home — no functional impact |

### Tests
- Command: `uv run pytest -x -q`
- Result: 717 passed, 0 failed (185s)
- Log: `.pytest-logs/20260509-173946-review-impl.log`
- Live vision LLM calls in range (3.2–4.8s warm); no stalls.

### Behavioral Verification
- Lint: `scripts/quality-gate.sh lint` ✓ clean (ruff check + format, 364 files), re-run after suite.
- CLI boot: `co --help` ✓ package imports cleanly; bootstrap exercised by the green suite. (`co status` is the skill's generic placeholder — not a co command; surface here is the `chat` REPL input path.)
- `success_signal` TASK-3 verified: list `[text, BinaryContent]` prompt → real model replies "Red" (pixel-grounded).
- `success_signal` TASK-4 verified: lone image path on vision-capable model → `BinaryContent` attached, reply contains "red"; blind model → exactly one notice + text-only; slash-delegated image-like path → not attached. All through real REPL-dispatch helpers + configured model.

### Overall: PASS
All five tasks pass `done_when` with file:line evidence; six load-bearing claims confirmed by an adversarial cold read (incl. overflow-loop terminal exit at orchestrate.py:714-716); full suite green; lint clean; success_signals verified against the real model. Owner-approved placement deviation (intake before slash dispatch) is sound and collision-safe. Three minor non-blocking notes recorded above. Ready to ship — fold the placement deviation into the plan body on archive.
