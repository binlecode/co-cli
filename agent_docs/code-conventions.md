# Code Conventions

## Class Naming Suffixes

Names must reveal the class's role. Prefer these suffixes where they fit. Self-evident named concepts (e.g. `ShellBackend`, `AgentLoop`) do not need a suffix.

| Suffix | Meaning |
|--------|---------|
| `*State` | Mutable lifecycle data |
| `*Result` | Immutable pass/fail outcome |
| `*Output` | Agent/pipeline payload |
| `*Settings` | Persisted configuration — top-level `Settings` and any nested submodel; maps to `settings.json` |
| `*Config` | Runtime configuration — in-memory, not persisted; **confirm with the user before introducing a new `*Config` name** |
| `*Info` | Read-only descriptor |
| `*Registry` | Registration lookup table |
| `*Store` | Persistent storage layer |
| `*Context` | Input bag for a call |
| `*Event` | Async/streaming event |
| `*Error` | Exception class |
| `*Enum` | Enumeration |

## Variable and Function Naming

Use descriptive names that reveal intent — including loop variables (e.g. `idx`, `key`, `val` over `i`, `k`, `v`). Well-known conventions (`fd`, `db`) are fine as-is.

No domain-specific abbreviations. Names must be explicit and reflect functionality: `frontmatter_dict` not `fm_dict`, `source_type` not `src_type`. Standard Python/ecosystem shorthands (`id`, `dir`, `url`, `db`, `ctx`) are fine. Magic labels — opaque short strings used as keys or identifiers without a named constant — are not allowed; use an enum or a descriptive named constant instead.

When a name identifies a layer's job — function, field, or telemetry attribute — pick the verb for the layer's *behavior*, not for the underlying mechanic. `spill_threshold_chars` and `spill_if_oversized` (the spill layer) read better at every callsite than `persist_threshold_chars` and `persist_if_oversized` (the disk-write mechanic). Serialization payloads that bake a verb into their *value* (e.g. `PERSISTED_OUTPUT_TAG = "<persisted-output>"`) keep the verb in both name and value — changing the value breaks compat with stored history.

## Numeric Constant Unit Suffixes

Numeric constants name their unit when it isn't obvious from context. Bare `_SIZE` is forbidden when more than one unit could plausibly apply (chars vs tokens vs bytes vs items).

| Quantity | Suffix | Example |
|----------|--------|---------|
| Char counts | `_CHARS` | `TOOL_RESULT_PREVIEW_CHARS = 2_000` |
| Token counts | `_TOKENS` | `STUB_TOKENS = 575` |
| Byte counts | `_BYTES` | `MAX_UPLOAD_BYTES = 10 * 1024 * 1024` |
| Time | `_SECONDS` / `_MS` | `REQUEST_TIMEOUT_SECONDS = 30` |
| Conversion ratios | `_PER_X` | `CHARS_PER_TOKEN = 4` |

Same rule for function parameters and return values when the unit isn't named by the surrounding type. Drive-by renaming of bare-`_SIZE` constants during an otherwise-related edit is acceptable cleanup.

## Suffix Preservation

Preserve existing suffix conventions (e.g. `*Registry`, `*Info`) unless explicitly told otherwise. Before proposing a rename, verify the new name against peer codebases and existing conventions in this repo.

## Shared Primitives

For cross-cutting concerns, use the existing project primitive before adding another path. Config loading, console output, filesystem roots, tool outputs, approval flow, tracing, and test harnesses should each have one obvious implementation route.

Full-overwrite file mutation uses `co_cli.persistence.atomic.atomic_write_text` (or `atomic_write_bytes` for binary). Both primitives `mkdir(parents=True, exist_ok=True)` before writing — do not pre-create the parent at call sites. Local `tempfile.NamedTemporaryFile` + `os.replace` blocks in mutation paths are forbidden.

Multi-step writes to `MemoryStore` use `with store.transaction() as tx: tx.index(...); tx.index_chunks(...)`. The public `index() / index_chunks() / remove() / remove_chunks()` methods always commit; hidden transaction state on the store is forbidden.

## Display

Use the project's shared `console` object for all terminal output. Use semantic style names; never hardcode color names at callsites.
