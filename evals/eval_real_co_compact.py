#!/usr/bin/env python3
"""Standalone eval: launch a real `co` CLI session and validate `/compact`.

This eval does not use pytest. It spawns the actual REPL in a PTY, drives a
small conversation, invokes `/compact`, and inspects new trace rows written to
the shared OTel SQLite DB.

Success criteria:
1. The real CLI reaches the `Co ❯ ` prompt.
2. `/compact` succeeds and reports a compaction result.
3. New summarizer spans are written after the eval starts.
4. Those summarizer spans use the configured think model over the OpenAI-
   compatible Ollama path.
"""

from __future__ import annotations

import os
import re
import select
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from co_cli.config import LOGS_DB
from evals._timeouts import EVAL_API_TIMEOUT_SECS, EVAL_PROBE_TIMEOUT_SECS

PROMPT = "Co ❯"
SESSION_TIMEOUT_S = 180
READ_CHUNK = 4096
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CRUFT_RE = re.compile(r"[\r\b]")


def _db_high_water_mark() -> int:
    if not LOGS_DB.exists():
        return 0
    with sqlite3.connect(LOGS_DB) as conn:
        row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM spans").fetchone()
    return int(row[0]) if row else 0


def _query_new_summarizer_spans(after_rowid: int) -> list[tuple[str, str]]:
    if not LOGS_DB.exists():
        return []
    with sqlite3.connect(LOGS_DB) as conn:
        rows = conn.execute(
            """
            SELECT name, attributes
            FROM spans
            WHERE rowid > ?
              AND (name LIKE 'chat %' OR name LIKE 'invoke_agent %')
              AND attributes LIKE '%summaris%'
            ORDER BY rowid ASC
            """,
            (after_rowid,),
        ).fetchall()
    return [(str(name), str(attributes or "")) for name, attributes in rows]


def _normalize_terminal_output(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = _CRUFT_RE.sub("", text)
    return text


def _read_until(fd: int, pattern: str, *, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    chunks: list[str] = []
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue
        data = os.read(fd, READ_CHUNK).decode("utf-8", errors="replace")
        if not data:
            break
        chunks.append(data)
        joined = "".join(chunks)
        if pattern in _normalize_terminal_output(joined):
            return joined
    raise TimeoutError(f"Timed out waiting for pattern {pattern!r}")


def _read_turn(fd: int, *, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    chunks: list[str] = []
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            continue
        data = os.read(fd, READ_CHUNK).decode("utf-8", errors="replace")
        if not data:
            break
        chunks.append(data)
        visible = _normalize_terminal_output("".join(chunks))
        if visible.count(PROMPT) >= 2:
            return "".join(chunks)
    raise TimeoutError(f"Timed out waiting for full turn ending in prompt {PROMPT!r}")


def _send(fd: int, text: str) -> None:
    os.write(fd, text.encode("utf-8"))


def main() -> int:
    print("=" * 60)
    print("  Eval: Real Co CLI /compact")
    print("=" * 60)

    before_rowid = _db_high_water_mark()
    master_fd, slave_fd = os.openpty()
    proc = subprocess.Popen(
        ["uv", "run", "co"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(Path.cwd()),
        text=False,
        close_fds=True,
    )
    os.close(slave_fd)

    transcript = ""
    try:
        print("\n[1/4] Waiting for REPL prompt...")
        transcript += _read_until(master_fd, PROMPT, timeout_s=SESSION_TIMEOUT_S)
        print("PASS: prompt reached")

        print("[2/4] Driving short conversation...")
        _send(master_fd, "Docker packages software and dependencies into portable containers.\n")
        transcript += _read_turn(master_fd, timeout_s=SESSION_TIMEOUT_S)
        _send(master_fd, "Summarize the key point in one short sentence.\n")
        transcript += _read_turn(master_fd, timeout_s=SESSION_TIMEOUT_S)
        print("PASS: conversation turns completed")

        print("[3/4] Running /compact...")
        _send(master_fd, "/compact\n")
        compact_output = _read_turn(master_fd, timeout_s=SESSION_TIMEOUT_S)
        transcript += compact_output
        if "Compacted:" not in compact_output:
            print("FAIL: /compact did not report compaction")
            print("\n--- Transcript tail ---")
            print(transcript[-4000:])
            return 1
        print("PASS: /compact reported compaction")

        print("[4/4] Inspecting summarizer spans...")
        _send(master_fd, "exit\n")
        try:
            proc.wait(timeout=EVAL_API_TIMEOUT_SECS)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=EVAL_PROBE_TIMEOUT_SECS)

        spans = _query_new_summarizer_spans(before_rowid)
        if not spans:
            print("FAIL: no new summarizer spans found")
            print("\n--- Transcript tail ---")
            print(transcript[-4000:])
            return 1

        found_model = False
        found_openai = False
        for name, attrs in spans:
            if "qwen3.5:35b-a3b-think" in name or "qwen3.5:35b-a3b-think" in attrs:
                found_model = True
            if '"gen_ai.provider.name": "openai"' in attrs and '"server.address": "localhost"' in attrs:
                found_openai = True

        if not found_model or not found_openai:
            print("FAIL: summarizer spans do not show expected model/provider")
            print("\n--- Summarizer spans ---")
            for name, attrs in spans:
                print(name)
                print(attrs[:800])
                print("---")
            return 1

        print("PASS: summarizer spans show qwen3.5:35b-a3b-think over ollama-openai")
        print("\nVERDICT: PASS")
        return 0
    except Exception as exc:
        print(f"\nVERDICT: FAIL ({type(exc).__name__}: {exc})")
        print("\n--- Transcript tail ---")
        print(transcript[-4000:])
        return 1
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=EVAL_PROBE_TIMEOUT_SECS)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
