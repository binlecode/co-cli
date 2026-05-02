# FTS5 Sanitizer Trim Analysis — 2026-05-02

## Summary

- Total queries: 34
- 6-step variant: 32 PASS / 2 FAIL
- 3-step variant: 19 PASS / 15 FAIL
- Elapsed: 0.7ms
- **Decision: KEEP**

## Decision Rule

If the stripped 3-step variant produces zero FTS5 `OperationalError`s: trim the function to steps 1+2+6.
If any query causes an `OperationalError` with the stripped variant: keep the current 6-step implementation with inline comments explaining each step.

## Variants

**6-step (current):** protect quoted phrases (1), strip `+{}()"^` (2), collapse `*+` / remove leading `*` (3), remove dangling AND/OR/NOT (4), quote hyphenated/dotted/underscored terms (5), restore phrases (6).

**3-step (stripped):** protect quoted phrases (1), strip `+{}()"^` (2), restore phrases (6). Steps 3, 4, 5 dropped.

## Results

| Query | 6-step output | 3-step output | FTS5-6 | FTS5-3 |
|-------|--------------|--------------|--------|--------|
| `asyncio concurrency` | `asyncio concurrency` | `asyncio concurrency` | PASS | PASS |
| `pytest fixture` | `pytest fixture` | `pytest fixture` | PASS | PASS |
| `memory recall` | `memory recall` | `memory recall` | PASS | PASS |
| `"asyncio concurrency"` | `"asyncio concurrency"` | `"asyncio concurrency"` | PASS | PASS |
| `"sqlite fts5"` | `"sqlite fts5"` | `"sqlite fts5"` | PASS | PASS |
| `asyncio*` | `asyncio*` | `asyncio*` | PASS | PASS |
| `pydantic*` | `pydantic*` | `pydantic*` | PASS | PASS |
| `asyncio AND concurrency` | `asyncio AND concurrency` | `asyncio AND concurrency` | PASS | PASS |
| `asyncio OR concurrency` | `asyncio OR concurrency` | `asyncio OR concurrency` | PASS | PASS |
| `NOT asyncio` | `asyncio` | `NOT asyncio` | PASS | FAIL |
| `asyncio NOT threading` | `asyncio NOT threading` | `asyncio NOT threading` | PASS | PASS |
| `AND asyncio` | `asyncio` | `AND asyncio` | PASS | FAIL |
| `asyncio OR` | `asyncio` | `asyncio OR` | PASS | FAIL |
| `asyncio AND` | `asyncio` | `asyncio AND` | PASS | FAIL |
| `chat-send` | `"chat-send"` | `chat-send` | PASS | FAIL |
| `session_store.py` | `"session_store.py"` | `session_store.py` | PASS | FAIL |
| `pydantic-ai` | `"pydantic-ai"` | `pydantic-ai` | PASS | FAIL |
| `co-cli` | `"co-cli"` | `co-cli` | PASS | FAIL |
| `asyncio** concurrency` | `asyncio* concurrency` | `asyncio** concurrency` | PASS | FAIL |
| `* asyncio` | `asyncio` | `* asyncio` | PASS | FAIL |
| `asyncio+concurrency` | `asyncio concurrency` | `asyncio concurrency` | PASS | PASS |
| `asyncio {concurrency}` | `asyncio  concurrency` | `asyncio  concurrency` | PASS | PASS |
| `(asyncio concurrency)` | `asyncio concurrency` | `asyncio concurrency` | PASS | PASS |
| `asyncio^2` | `asyncio 2` | `asyncio 2` | PASS | PASS |
| `pydantic-ai AND asyncio` | `"pydantic-ai" AND asyncio` | `pydantic-ai AND asyncio` | PASS | FAIL |
| `chat-send OR session_store.py` | `"chat-send" OR "session_store.py"` | `chat-send OR session_store.py` | PASS | FAIL |
| `   ` | `(empty)` | `(empty)` | FAIL | FAIL |
| `café async` | `café async` | `café async` | PASS | PASS |
| `"asyncio concurrency` | `asyncio concurrency` | `asyncio concurrency` | PASS | PASS |
| `asyncio "concurrency search` | `asyncio  concurrency search` | `asyncio  concurrency search` | PASS | PASS |
| `how does asyncio work` | `how does asyncio work` | `how does asyncio work` | PASS | PASS |
| `find all files with .py extension` | `find all files with .py extension` | `find all files with .py extension` | FAIL | FAIL |
| `recall memory session history` | `recall memory session history` | `recall memory session history` | PASS | PASS |
| `co-cli bootstrap startup check` | `"co-cli" bootstrap startup check` | `co-cli bootstrap startup check` | PASS | FAIL |

## 6-step Failures

- `   ` → `` — empty query after sanitization
- `find all files with .py extension` → `find all files with .py extension` — fts5: syntax error near "."

## 3-step Failures

- `NOT asyncio` → `NOT asyncio` — fts5: syntax error near "NOT"
- `AND asyncio` → `AND asyncio` — fts5: syntax error near "AND"
- `asyncio OR` → `asyncio OR` — fts5: syntax error near ""
- `asyncio AND` → `asyncio AND` — fts5: syntax error near ""
- `chat-send` → `chat-send` — no such column: send
- `session_store.py` → `session_store.py` — fts5: syntax error near "."
- `pydantic-ai` → `pydantic-ai` — no such column: ai
- `co-cli` → `co-cli` — no such column: cli
- `asyncio** concurrency` → `asyncio** concurrency` — fts5: syntax error near "*"
- `* asyncio` → `* asyncio` — unknown special query: asyncio
- `pydantic-ai AND asyncio` → `pydantic-ai AND asyncio` — no such column: ai
- `chat-send OR session_store.py` → `chat-send OR session_store.py` — no such column: send
- `   ` → `` — empty query after sanitization
- `find all files with .py extension` → `find all files with .py extension` — fts5: syntax error near "."
- `co-cli bootstrap startup check` → `co-cli bootstrap startup check` — no such column: cli

## Conclusion

The 3-step variant produced 15 FTS5 error(s). The 6-step implementation has been retained with inline comments documenting why each step is necessary.
