# Co CLI — Canon Memory

> Session transcripts: [memory-session.md](memory-session.md). Knowledge artifacts: [memory-knowledge.md](memory-knowledge.md). Personality system: [personality.md](personality.md).

## 1. Canon vs Static Personality

Static personality content (soul seed, mindsets, behavioral rules) is not a recall channel. `build_static_instructions()` assembles it once at agent construction into the cacheable static prompt and it does not change within a session.

The dynamic-instructions block (`current_time_prompt()`) is a small volatile suffix — today's date plus conditional safety warnings — appended per turn so the stable static prefix remains cache-stable.

Canon memories (`souls/{role}/memories/*.md`) are intentionally excluded from static injection. A scene either matches the moment or it doesn't — static injection pays full token cost whether it lands or not. Canon is therefore served on demand via `memory_search` and scored against the query.

See [personality.md](personality.md) for the full asset taxonomy and static assembly pipeline.

## 2. Canon Recall Channel

Canon files live at `souls/{role}/memories/*.md` (package-shipped, read-only). `search_canon()` scans them in-process:

- No FTS DB — in-process token-overlap scoring
- Title match weighted 2× relative to body
- Returns up to `character_recall_limit` hits per `memory_search` call

Character memory files support YAML frontmatter parsed by `parse_frontmatter()`.

Result shape: `{channel: "canon", role, title, body, score}`

## 3. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge.character_recall_limit` | `CO_CHARACTER_RECALL_LIMIT` | `3` | max canon hits per `memory_search` call |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/tools/memory/_canon_recall.py` | `search_canon()` — token-overlap scoring over `souls/{role}/memories/*.md` |
| `co_cli/memory/_stopwords.py` | `STOPWORDS` frozenset — shared by `similarity.py` and `_canon_recall.py` |
| `co_cli/context/assembly.py` | `build_static_instructions()` — static prompt assembly (soul + mindsets + rules + recency advisory) |
| `co_cli/agent/_instructions.py` | `current_time_prompt()` — per-turn date/time and safety warning injection |
| `co_cli/context/prompt_text.py` | `safety_prompt_text()` — doom-loop and shell-reflection warning text |
| `co_cli/personality/prompts/loader.py` | `load_soul_seed`, `load_soul_critique`, `load_soul_mindsets` — personality asset loaders |
