#!/usr/bin/env python3
"""Eval: trim analysis for sanitize_fts5_query.

Compares the current 6-step sanitizer against a stripped 3-step variant
(keep steps 1, 2, 6 — drop steps 3 collapse-*, 4 dangling-boolean, 5 quote-compounds).

For each query in a fixed set:
  - Run both variants
  - Print outputs side by side
  - Validate each output against a real in-memory FTS5 table (OperationalError = FAIL)

Decision rule (applied to search_util.py after running this eval):
  - Zero FTS5 errors with stripped variant -> trim to 3 steps
  - Any OperationalError with stripped variant -> keep 6 steps, add inline comments

Usage:
    uv run python evals/eval_fts_sanitize.py
"""

import re
import sqlite3
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Stripped 3-step variant (steps 1, 2, 6 only)
# ---------------------------------------------------------------------------


def _sanitize_3step(query: str) -> str:
    """Stripped sanitizer: protect quoted phrases, strip special chars, restore phrases."""
    _quoted: list[str] = []

    def _keep_quoted(m: re.Match) -> str:
        _quoted.append(m.group(0))
        return f"\x00Q{len(_quoted) - 1}\x00"

    # Step 1: protect balanced quoted phrases
    sanitized = re.sub(r'"[^"]*"', _keep_quoted, query)

    # Step 2: strip FTS5-special chars that cause parse errors
    sanitized = re.sub(r"[+{}()\"^]", " ", sanitized)

    # Step 6: restore protected quoted phrases
    for i, phrase in enumerate(_quoted):
        sanitized = sanitized.replace(f"\x00Q{i}\x00", phrase)

    return sanitized.strip()


# ---------------------------------------------------------------------------
# FTS5 validation helpers
# ---------------------------------------------------------------------------


def _make_fts5_db() -> sqlite3.Connection:
    """Create an in-memory FTS5 table with a few seed rows."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE docs USING fts5(title, body)")
    conn.executemany(
        "INSERT INTO docs VALUES (?, ?)",
        [
            ("asyncio concurrency patterns", "asyncio event loop task scheduling"),
            ("pytest fixture design", "conftest session scope function fixture"),
            ("sqlite fts5 ranking", "bm25 inverted index tokenizer porter"),
            ("memory recall search", "artifact session canon BM25 FTS5"),
            ("chat-send session_store.py", "hyphenated dotted compound terms"),
            ("pydantic-ai agent loop", "streaming tool calls approval"),
            ("file_read tool usage", "underscore path separator"),
            ("co-cli bootstrap", "startup check dependency injection"),
        ],
    )
    conn.commit()
    return conn


def _fts5_ok(conn: sqlite3.Connection, sanitized: str) -> tuple[bool, str | None]:
    """Return (ok, error_message). ok=True if MATCH executes without OperationalError."""
    if not sanitized.strip():
        # empty query — FTS5 raises OperationalError on empty string
        return False, "empty query after sanitization"
    try:
        conn.execute("SELECT * FROM docs WHERE docs MATCH ? LIMIT 1", (sanitized,)).fetchone()
        return True, None
    except sqlite3.OperationalError as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Query set
# ---------------------------------------------------------------------------

QUERIES: list[str] = [
    # plain terms
    "asyncio concurrency",
    "pytest fixture",
    "memory recall",
    # quoted phrase (intentional FTS5 syntax)
    '"asyncio concurrency"',
    '"sqlite fts5"',
    # prefix wildcard
    "asyncio*",
    "pydantic*",
    # boolean operators
    "asyncio AND concurrency",
    "asyncio OR concurrency",
    "NOT asyncio",
    "asyncio NOT threading",
    # dangling booleans
    "AND asyncio",
    "asyncio OR",
    "asyncio AND",
    # hyphenated / dotted / underscored compound terms
    "chat-send",
    "session_store.py",
    "pydantic-ai",
    "co-cli",
    # repeated wildcards
    "asyncio** concurrency",
    "* asyncio",
    # stray special chars
    "asyncio+concurrency",
    "asyncio {concurrency}",
    "(asyncio concurrency)",
    "asyncio^2",
    # mixed: compound + boolean
    "pydantic-ai AND asyncio",
    "chat-send OR session_store.py",
    # empty / whitespace-only
    "   ",
    # unicode term
    "café async",
    # nested quotes (unbalanced)
    '"asyncio concurrency',
    'asyncio "concurrency search',
    # real-world-style user queries
    "how does asyncio work",
    "find all files with .py extension",
    "recall memory session history",
    "co-cli bootstrap startup check",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    from co_cli.memory.search_util import sanitize_fts5_query

    conn = _make_fts5_db()

    results: list[dict] = []
    t0 = time.monotonic()

    print("=" * 76)
    print("  Eval: sanitize_fts5_query — 6-step vs 3-step trim analysis")
    print("=" * 76)
    fmt_hdr = f"  {'Query':<35} | {'6-step result':<22} | {'3-step result':<22} | {'FTS5-6':>6} | {'FTS5-3':>6}"
    print(fmt_hdr)
    print("-" * 76)

    six_pass = 0
    six_fail = 0
    three_pass = 0
    three_fail = 0

    for query in QUERIES:
        out_6 = sanitize_fts5_query(query)
        out_3 = _sanitize_3step(query)

        ok_6, err_6 = _fts5_ok(conn, out_6)
        ok_3, err_3 = _fts5_ok(conn, out_3)

        if ok_6:
            six_pass += 1
        else:
            six_fail += 1
        if ok_3:
            three_pass += 1
        else:
            three_fail += 1

        v6 = "PASS" if ok_6 else "FAIL"
        v3 = "PASS" if ok_3 else "FAIL"

        q_display = query[:33] + ".." if len(query) > 35 else query
        o6_display = out_6[:20] + ".." if len(out_6) > 22 else out_6
        o3_display = out_3[:20] + ".." if len(out_3) > 22 else out_3
        print(f"  {q_display:<35} | {o6_display:<22} | {o3_display:<22} | {v6:>6} | {v3:>6}")
        if not ok_6:
            print(f"    6-step ERROR: {err_6}")
        if not ok_3:
            print(f"    3-step ERROR: {err_3}")

        results.append(
            {
                "query": query,
                "out_6": out_6,
                "out_3": out_3,
                "ok_6": ok_6,
                "err_6": err_6,
                "ok_3": ok_3,
                "err_3": err_3,
            }
        )

    elapsed_ms = (time.monotonic() - t0) * 1000

    print("-" * 76)
    print(f"  6-step: {six_pass} PASS / {six_fail} FAIL")
    print(f"  3-step: {three_pass} PASS / {three_fail} FAIL")
    print(f"  Elapsed: {elapsed_ms:.1f}ms")
    print("=" * 76)

    # Decision
    if three_fail == 0:
        decision = "TRIM"
        print("  Decision: TRIM — all queries pass with 3-step variant; trim to steps 1+2+6.")
    else:
        decision = "KEEP"
        print(
            f"  Decision: KEEP — {three_fail} query(ies) fail FTS5 with stripped variant; "
            "keep 6-step with inline comments."
        )
    print("=" * 76)

    # Write report
    _write_report(results, six_pass, six_fail, three_pass, three_fail, elapsed_ms, decision)

    return 0 if six_fail == 0 else 1


def _write_report(
    results: list[dict],
    six_pass: int,
    six_fail: int,
    three_pass: int,
    three_fail: int,
    elapsed_ms: float,
    decision: str,
) -> None:
    """Write docs/REPORT-fts-sanitize-20260502.md."""
    repo_root = Path(__file__).parent.parent
    report_path = repo_root / "docs" / "REPORT-fts-sanitize-20260502.md"

    fail_rows_3 = [r for r in results if not r["ok_3"]]
    fail_rows_6 = [r for r in results if not r["ok_6"]]

    lines: list[str] = [
        "# FTS5 Sanitizer Trim Analysis — 2026-05-02",
        "",
        "## Summary",
        "",
        f"- Total queries: {len(results)}",
        f"- 6-step variant: {six_pass} PASS / {six_fail} FAIL",
        f"- 3-step variant: {three_pass} PASS / {three_fail} FAIL",
        f"- Elapsed: {elapsed_ms:.1f}ms",
        f"- **Decision: {decision}**",
        "",
        "## Decision Rule",
        "",
        "If the stripped 3-step variant produces zero FTS5 `OperationalError`s: trim "
        "the function to steps 1+2+6.",
        "If any query causes an `OperationalError` with the stripped variant: keep the "
        "current 6-step implementation with inline comments explaining each step.",
        "",
        "## Variants",
        "",
        '**6-step (current):** protect quoted phrases (1), strip `+{}()"^` (2), '
        "collapse `*+` / remove leading `*` (3), remove dangling AND/OR/NOT (4), "
        "quote hyphenated/dotted/underscored terms (5), restore phrases (6).",
        "",
        '**3-step (stripped):** protect quoted phrases (1), strip `+{}()"^` (2), '
        "restore phrases (6). Steps 3, 4, 5 dropped.",
        "",
        "## Results",
        "",
        "| Query | 6-step output | 3-step output | FTS5-6 | FTS5-3 |",
        "|-------|--------------|--------------|--------|--------|",
    ]

    for r in results:
        q = r["query"].replace("|", "\\|")
        o6 = r["out_6"].replace("|", "\\|") or "(empty)"
        o3 = r["out_3"].replace("|", "\\|") or "(empty)"
        v6 = "PASS" if r["ok_6"] else "FAIL"
        v3 = "PASS" if r["ok_3"] else "FAIL"
        lines.append(f"| `{q}` | `{o6}` | `{o3}` | {v6} | {v3} |")

    if fail_rows_6:
        lines += [
            "",
            "## 6-step Failures",
            "",
        ]
        for r in fail_rows_6:
            lines.append(f"- `{r['query']}` → `{r['out_6']}` — {r['err_6']}")

    if fail_rows_3:
        lines += [
            "",
            "## 3-step Failures",
            "",
        ]
        for r in fail_rows_3:
            lines.append(f"- `{r['query']}` → `{r['out_3']}` — {r['err_3']}")

    lines += [
        "",
        "## Conclusion",
        "",
    ]

    if decision == "TRIM":
        lines.append(
            "The 3-step variant passes all FTS5 validation checks. "
            "`sanitize_fts5_query` has been trimmed to steps 1, 2, and 6 in "
            "`co_cli/memory/search_util.py`."
        )
    else:
        lines.append(
            f"The 3-step variant produced {three_fail} FTS5 error(s). "
            "The 6-step implementation has been retained with inline comments "
            "documenting why each step is necessary."
        )

    report_path.write_text("\n".join(lines) + "\n")
    print(f"  Report written: {report_path}")


if __name__ == "__main__":
    sys.exit(main())
