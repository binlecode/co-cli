# Code Conventions

## Class Naming Suffixes

Names must reveal the class's role. Prefer these suffixes where they fit. Self-evident named concepts (e.g. `ShellBackend`, `AgentLoop`) do not need a suffix.

| Suffix | Meaning |
|--------|---------|
| `*State` | Mutable lifecycle data |
| `*Result` | Immutable pass/fail outcome |
| `*Output` | Agent/pipeline payload |
| `*Settings` | Persisted configuration — top-level `Settings` and any nested submodel; maps to `settings.json` |
| `*Config` | Runtime configuration — in-memory, not persisted (e.g. `SkillConfig` loaded from `.md`); **confirm with the user before introducing a new `*Config` name** |
| `*Info` | Read-only descriptor |
| `*Registry` | Registration lookup table |
| `*Store` | Persistent storage layer |
| `*Context` | Input bag for a call |
| `*Event` | Async/streaming event |
| `*Error` | Exception class |
| `*Enum` | Enumeration |

## Variable and Function Naming

Use descriptive names that reveal intent — including loop variables (e.g. `idx`, `key`, `val` over `i`, `k`, `v`). Well-known conventions (`fd`, `db`) are fine as-is.

## Suffix Preservation

Preserve existing suffix conventions (e.g. `*Registry`, `*Info`) unless explicitly told otherwise. Before proposing a rename, verify the new name against peer codebases and existing conventions in this repo.

## Shared Primitives

For cross-cutting concerns, use the existing project primitive before adding another path. Config loading, console output, filesystem roots, tool outputs, approval flow, tracing, and test harnesses should each have one obvious implementation route.

## Display

Use the project's shared `console` object for all terminal output. Use semantic style names; never hardcode color names at callsites.
