# .co-cli — Project-Local Data

Project-scoped runtime data for **co-cli**. Lives alongside the codebase, similar to `.vscode/` or `.claude/`.

## Directory Layout

| Path | Content | Git-tracked | Lifecycle |
|------|---------|-------------|-----------|
| `memory/` | Project-local memory markdown (kind: memory/article) | Yes | Persists across sessions; managed by memory tools |
| `skills/` | Skill definitions (SKILL.md + references) | Yes | Project config; edited by developers |
| `settings.json` | Project-level settings (overrides user config) | No | Per-project; may contain secrets |
| `sessions/` | Session JSON metadata + JSONL transcripts | No | Ephemeral per-user; created per chat session |
| `tool-results/` | Oversized tool result offloads | No | Transient runtime artifacts; safe to delete |
| `library/` | Project-local article store | Yes | Managed by article tools |
| `search.db` | Knowledge FTS5 index | No | Rebuilt from memory/library content |
| `co-cli.db` | Local trace/span storage | No | Runtime diagnostics |

## What to commit

Track `memory/`, `skills/`, `library/`, and this README. Everything else is ephemeral or user-specific and is covered by `.gitignore`.

## See also

- `docs/DESIGN-system.md` — system architecture and data path diagram
- `docs/DESIGN-context.md` — knowledge schema, memory lifecycle, session persistence
