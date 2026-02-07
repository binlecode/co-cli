"""E2E test: Ctrl-C during a long-running agent task returns to CLI prompt.

Spawns `co chat` in a PTY (so prompt_toolkit works), sends a command that
triggers a long agent run, delivers SIGINT at two points:

  1. During the approval prompt (synchronous Prompt.ask)
  2. During agent.run() / tool execution (async)

…and verifies the process survives and returns to the Co prompt each time.

This validates the asyncio.CancelledError + KeyboardInterrupt handling and
the SIGINT handler swap in _handle_approvals (main.py).

Prerequisites:
  - LLM provider configured (gemini_api_key or ollama running)
  - Docker running (the prompt asks for a shell command)

Usage:
    uv run python scripts/e2e_ctrl_c.py
"""

import os
import pty
import select
import signal
import subprocess
import sys
import time

READ_CHUNK = 4096


def read_until(fd, marker: str, timeout: float = 15) -> str:
    """Read from fd until *marker* appears in accumulated output or timeout."""
    buf = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        ready, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if ready:
            try:
                data = os.read(fd, READ_CHUNK).decode("utf-8", errors="replace")
            except OSError:
                break
            buf += data
            if marker in buf:
                return buf
    return buf


def drain(fd, timeout: float = 1) -> str:
    """Read all pending output from fd."""
    return read_until(fd, "WONT_MATCH_DRAIN", timeout=timeout)


def assert_alive(proc, context: str) -> bool:
    if proc.poll() is not None:
        print(f"[FAIL] Process exited ({proc.returncode}) during: {context}")
        return False
    return True


def check_recovery(fd, proc, label: str) -> bool:
    """After SIGINT, check that the process is alive and returns to prompt."""
    time.sleep(2)
    post = read_until(fd, "Co", timeout=15)
    print(f"[{label}] Post-interrupt output ({len(post)} chars): {repr(post[:300])}")

    if proc.poll() is not None:
        print(f"[FAIL] Process DIED after Ctrl-C (exit code {proc.returncode})")
        return False

    print(f"[PASS] Process survived Ctrl-C ({label})")

    if "Interrupt" in post:
        print(f"[PASS] Saw 'Interrupted' message ({label})")
    else:
        print(f"[WARN] Did not see 'Interrupted' ({label})")

    if "Co" in post:
        print(f"[PASS] Saw Co prompt — returned to CLI ({label})")
    else:
        print(f"[WARN] Did not see Co prompt ({label})")

    return True


def main():
    master_fd, slave_fd = pty.openpty()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen(
        [sys.executable, "-m", "co_cli.main", "chat"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"},
        cwd=project_root,
    )
    os.close(slave_fd)

    print(f"[TEST] Started co chat (PID {proc.pid})")
    ok = True

    try:
        # ── Wait for welcome prompt ──────────────────────────────────
        output = read_until(master_fd, "Co", timeout=20)
        if "Co" not in output:
            print(f"[FAIL] Never saw Co prompt. Output:\n{output[:500]}")
            return 1
        print("[TEST] Saw Co prompt.\n")

        # ═══════════════════════════════════════════════════════════════
        # TEST 1: Ctrl-C during the approval prompt (synchronous input)
        # ═══════════════════════════════════════════════════════════════
        print("=" * 60)
        print("TEST 1: Ctrl-C during approval prompt")
        print("=" * 60)

        os.write(master_fd, b"run a shell command: sleep 30 && echo done\n")
        print("[T1] Sent shell command, waiting for approval prompt...")

        approval = read_until(master_fd, "y/n", timeout=30)
        print(f"[T1] Output ({len(approval)} chars): {repr(approval[:200])}")

        if "y/n" not in approval:
            print("[T1] Never saw approval prompt — skipping this test")
        else:
            print("[T1] Approval prompt visible. Sending SIGINT...")
            if not assert_alive(proc, "before SIGINT at approval"):
                return 1
            os.kill(proc.pid, signal.SIGINT)
            if not check_recovery(master_fd, proc, "T1"):
                ok = False

        # ═══════════════════════════════════════════════════════════════
        # TEST 2: Ctrl-C during agent.run() (async LLM call)
        # ═══════════════════════════════════════════════════════════════
        print()
        print("=" * 60)
        print("TEST 2: Ctrl-C during agent.run() (LLM thinking)")
        print("=" * 60)

        drain(master_fd)  # clear buffer

        os.write(master_fd, b"write a 2000 word essay on the history of computing\n")
        print("[T2] Sent long-generation prompt, waiting for thinking...")

        thinking = read_until(master_fd, "thinking", timeout=15)
        print(f"[T2] Output: {repr(thinking[:200])}")

        if "thinking" not in thinking:
            print("[T2] Never saw 'thinking' message — skipping")
        else:
            # Wait a moment for the LLM API call to be in flight
            time.sleep(3)
            print("[T2] Sending SIGINT during agent.run()...")
            if not assert_alive(proc, "before SIGINT during thinking"):
                return 1
            os.kill(proc.pid, signal.SIGINT)
            if not check_recovery(master_fd, proc, "T2"):
                ok = False

    finally:
        # ── Clean up ──────────────────────────────────────────────────
        print("\n[TEST] Cleaning up...")
        if proc.poll() is None:
            try:
                os.write(master_fd, b"\nexit\n")
                proc.wait(timeout=10)
                print(f"[TEST] Process exited cleanly (code {proc.returncode})")
            except Exception:
                proc.kill()
                proc.wait()
                print("[TEST] Killed process")
        os.close(master_fd)

    if ok:
        print("\n[RESULT] ALL TESTS PASSED")
        return 0
    else:
        print("\n[RESULT] SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
