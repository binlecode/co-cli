# TODO: Startup banner — split knowledge status by source

Task type: ux

## Context

Current startup UI is backend-centric where it should be source-centric.

Inline current-state validation:
- [co_cli/bootstrap/banner.py](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/banner.py#L61) renders a single `Knowledge:` line derived only from the active backend (`hybrid`, `fts5`, or `grep`) plus degradation text.
- [co_cli/bootstrap/core.py](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/core.py#L166) bootstraps two local knowledge sources separately: `memory` and `library`.
- [co_cli/tools/articles.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/articles.py#L184) treats cross-source knowledge differently from memory: default `search_knowledge()` searches `library + obsidian + drive`, while memory is a separate tool path.

Artifact hygiene: no existing TODO owns banner-level source status for memory, library, and external knowledge.

## Problem & Outcome

**Problem:** The welcome banner collapses multiple user-visible knowledge domains into one infrastructure line:

```text
Knowledge: fts5
```

That hides the distinctions users actually care about:
- whether personal memory is available
- whether saved articles/library content is indexed or grep-only
- whether external knowledge surfaces are available at all

The result is misleading startup feedback. A user can have working memory tools, no indexed library, and no external knowledge connectors, yet the banner still reduces all of that to a single backend word.

**Failure cost:** users cannot tell, at startup, whether:
- memory is available but grep-only
- library indexing degraded
- external knowledge is effectively off

**Outcome:** replace the single backend-only `Knowledge:` summary with three source-oriented rows:
- `Memory`
- `Library`
- `External`

The banner should answer "what knowledge can I use right now?" rather than "what retrieval engine is under the hood?"

## Scope

This task is limited to the interactive startup banner and the minimum status assembly needed to support it.

In scope:
- `co_cli/bootstrap/banner.py`
- small helper logic needed to derive source-centric status from `deps` and existing config
- tests covering source status rendering

Out of scope:
- changing memory or library retrieval behavior
- redesigning `/status` output beyond shared helper reuse if needed
- adding new remote probes at startup
- generic MCP capability summaries

For this task, **external knowledge** means non-local knowledge surfaces available during turns:
- Obsidian
- Google Drive
- web search

MCP is out of scope unless an existing MCP-specific signal is already being surfaced without extra probing.

## Behavioral Constraints

- Do not add new startup network checks. Banner state must be derived from existing bootstrap state plus existing config gates.
- Do not scan files just to show counts. This is a status split, not an inventory panel.
- Keep the panel narrow enough to fit the current light-theme banner shape in standard terminal widths.
- Do not reintroduce a misleading single `Knowledge:` line that hides source boundaries.
- Memory status must reflect the actual runtime retrieval path, not the configured ideal. If runtime memory search is grep-only, show that.
- Library status must reflect the actual runtime retrieval path, including degradation from `hybrid` to `fts5` or `grep`.
- External status must report capability availability, not speculative remote liveness beyond the checks the app already performs today.

## High-Level Design

### Before

```text
Model: ollama-openai / qwen3.5:35b-a3b-think
Knowledge: fts5  (hybrid -> fts5 ...)
Tools: 30  Skills: 1  MCP: 1  Commands: 16
```

### After

```text
Model: ollama-openai / qwen3.5:35b-a3b-think
Memory: available (indexed)
Library: fts5  (hybrid -> fts5 ...)
External: obsidian off  drive off  web on
Tools: 30  Skills: 1  MCP: 1  Commands: 16
```

Suggested semantics:

- `Memory`
  - `available (indexed)` when runtime memory retrieval uses the knowledge store
  - `available (grep-only)` when runtime memory retrieval does not use the store
  - no item counts

- `Library`
  - `hybrid`, `fts5`, or `grep-only`
  - degradation text stays here because backend degradation primarily describes library/article retrieval

- `External`
  - compact capability summary for `obsidian`, `drive`, and `web`
  - use short `on` / `off` states to preserve width

Implementation note:
- prefer a small typed helper that returns banner-ready status strings from `deps`
- keep display formatting in `banner.py`; avoid spreading string assembly across bootstrap code

## Implementation Plan

### TASK-1 — Introduce source-centric banner status assembly

Create a small helper inside `co_cli/bootstrap/banner.py` or a nearby private bootstrap helper module that derives three strings from existing runtime state:
- memory status
- library status
- external status

The helper should use:
- `deps.knowledge_store`
- `deps.config`
- `deps.degradations`
- existing config presence gates for Obsidian, Google Drive, and web search

Recommended output contract:

```text
memory_status = "available (indexed)" | "available (grep-only)"
library_status = "hybrid" | "fts5" | "grep-only" + optional degradation suffix
external_status = "obsidian on/off  drive on/off  web on/off"
```

files:
- `co_cli/bootstrap/banner.py`
done_when: |
  banner status assembly is no longer a single backend-only line;
  source status strings are derived in one place from deps/config/degradations
success_signal: a single helper owns banner-ready status for Memory, Library, and External
prerequisites: []

### TASK-2 — Replace the single `Knowledge:` row in the welcome banner

Update the banner body to remove the single `Knowledge:` line and replace it with:
- `Memory: ...`
- `Library: ...`
- `External: ...`

Rules:
- keep model/version/tools/dir/ready rows unchanged unless needed for spacing
- keep degradation messaging attached to the library row
- keep the final panel visually compact

files:
- `co_cli/bootstrap/banner.py`
done_when: |
  grep -n 'Knowledge:' co_cli/bootstrap/banner.py returns no banner-row hit;
  grep -n 'Memory:' co_cli/bootstrap/banner.py returns the new memory row;
  grep -n 'Library:' co_cli/bootstrap/banner.py returns the new library row;
  grep -n 'External:' co_cli/bootstrap/banner.py returns the new external row
success_signal: startup banner shows source-level knowledge readiness instead of a single backend summary
prerequisites: [TASK-1]

### TASK-3 — Add banner rendering tests for the new source split

Add focused tests around banner status formatting. Prefer testing a pure helper or a list-of-lines builder rather than snapshotting Rich panel output.

Coverage must include:
- indexed memory + degraded library (`hybrid -> fts5`)
- grep-only fallback for both memory and library
- external row with mixed availability states
- width-safe short labels (`on` / `off`) rather than verbose prose

Use real settings/deps objects, not mocks.

files:
- `tests/test_bootstrap.py` or `tests/test_banner.py`
done_when: |
  banner tests assert Memory/Library/External output under indexed and degraded scenarios;
  uv run pytest <affected banner test file(s)> -x passes
success_signal: banner status semantics are locked by tests and do not regress back to a single coarse knowledge line
prerequisites: [TASK-2]

## Testing

During implementation, scope to the affected tests first:

- `mkdir -p .pytest-logs && uv run pytest tests/test_bootstrap.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-banner-knowledge-status.log`

If a new dedicated banner test file is added:

- `mkdir -p .pytest-logs && uv run pytest tests/test_banner.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-banner-knowledge-status.log`

Before shipping:

- `scripts/quality-gate.sh types`

## Open Questions

None. For this task, external knowledge is explicitly defined as `obsidian + drive + web`, and MCP remains out of scope.
