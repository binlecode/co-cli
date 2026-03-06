#!/usr/bin/env python3
"""Coding toolchain eval: gate checks for file tools and coder delegation.

Metrics:
  edit_success_rate    >= 0.80  (write_file + edit_file cases that succeed)
  patch_apply_rate     >= 0.90  (edit_file cases where replacement is confirmed in file)
  tool_error_recovery  >= 0.70  (expected-failure cases that return error dict)

Exit codes:
  0 — all gates pass
  1 — one or more gates fail
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from co_cli.tools.files import find_in_files, edit_file, write_file, read_file


FIXTURE_PATH = Path(__file__).parent / "coding_toolchain.jsonl"

THRESHOLDS = {
    "edit_success_rate": 0.80,
    "patch_apply_rate": 0.90,
    "tool_error_recovery_rate": 0.70,
}


# ---------------------------------------------------------------------------
# Minimal fake context — file tools only use Path.cwd(), not ctx.deps
# ---------------------------------------------------------------------------


@dataclass
class _FakeDeps:
    pass


class _FakeCtx:
    deps = _FakeDeps()


_CTX = _FakeCtx()


# ---------------------------------------------------------------------------
# Fixture loader
# ---------------------------------------------------------------------------


def load_fixture() -> list[dict]:
    cases: list[dict] = []
    with open(FIXTURE_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


# ---------------------------------------------------------------------------
# Individual case runners — each gets its own isolated subdirectory
# ---------------------------------------------------------------------------


async def run_edit_existing(case_dir: Path) -> tuple[bool, bool]:
    """Returns (success, patch_applied).

    Writes the target file, chdirs into case_dir, then calls edit_file.
    Ordering matters: mkdir + write first, then chdir, then tool call.
    """
    case_dir.mkdir(parents=True, exist_ok=True)
    target = case_dir / "target.py"
    target.write_text("def hello():\n    return 'world'\n")

    os.chdir(case_dir)

    try:
        result = await edit_file(_CTX, path="target.py", search="'world'", replacement="'earth'")
    except (ValueError, Exception):
        # edit_file raises ValueError when search string is missing
        return False, False

    success = not result.get("error")
    patch_applied = success and "'earth'" in target.read_text()
    return success, patch_applied


async def run_write_new(case_dir: Path) -> bool:
    case_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(case_dir)

    result = await write_file(_CTX, path="new_output.txt", content="generated content\n")
    return not result.get("error")


async def run_find_matches(case_dir: Path) -> bool:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "src.py").write_text("def foo():\n    return 42\n")
    (case_dir / "other.py").write_text("x = foo()\n")

    os.chdir(case_dir)

    # Use a pattern that is valid regex and matches "foo(" literally
    result = await find_in_files(_CTX, pattern=r"foo\(")
    return not result.get("error") and result.get("count", 0) > 0


async def run_path_traversal(case_dir: Path) -> bool:
    """Expected to fail — should return error dict."""
    case_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(case_dir)

    result = await read_file(_CTX, path="../../etc/passwd")
    # Path escape should be caught and return an error dict
    return bool(result.get("error"))


async def run_delegate_summary() -> bool:
    """delegate_coder with no model configured should return a dict with 'display'.

    The tool returns an error dict (error=True) when unconfigured. We treat
    any well-formed dict with a 'display' key as a passing result — the tool
    ran and communicated its state correctly.
    """
    from co_cli.tools.delegation import delegate_coder

    @dataclass
    class _MinimalDeps:
        model_roles: dict = field(default_factory=dict)
        llm_provider: str = "ollama"
        ollama_host: str = "http://localhost:11434"

    class _MinimalCtx:
        deps = _MinimalDeps()

    result = await delegate_coder(_MinimalCtx(), "analyze the codebase")
    # Unconfigured → error dict with 'display' key — tool ran and returned a dict
    return isinstance(result, dict) and "display" in result


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_all_cases(fixture: list[dict], tmp_dir: Path) -> dict:
    """Run all cases in isolated subdirs and collect results."""
    results: dict[str, dict] = {}

    for case in fixture:
        cid = case["id"]
        try:
            if cid == "edit_existing":
                success, patch = await run_edit_existing(tmp_dir / "edit")
                results[cid] = {
                    "success": success,
                    "patch": patch,
                    "expected": case["expected_success"],
                }

            elif cid == "write_new":
                ok = await run_write_new(tmp_dir / "write")
                results[cid] = {"success": ok, "expected": case["expected_success"]}

            elif cid == "find_matches":
                ok = await run_find_matches(tmp_dir / "find")
                results[cid] = {"success": ok, "expected": case["expected_success"]}

            elif cid == "path_traversal":
                ok = await run_path_traversal(tmp_dir / "pt")
                results[cid] = {"success": ok, "expected": case["expected_success"]}

            elif cid == "delegate_summary":
                ok = await run_delegate_summary()
                results[cid] = {"success": ok, "expected": case["expected_success"]}

            else:
                results[cid] = {
                    "success": False,
                    "error": f"Unknown case id: {cid}",
                    "expected": case.get("expected_success", True),
                }

        except Exception as exc:
            results[cid] = {
                "success": False,
                "error": str(exc),
                "expected": case.get("expected_success", True),
            }

    return results


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_metrics(results: dict) -> dict:
    # edit_success_rate: write_file + edit_file cases that succeed
    write_cases = [r for k, r in results.items() if k in ("edit_existing", "write_new")]
    # patch_apply_rate: edit_file cases where replacement is confirmed in output
    edit_cases = [r for k, r in results.items() if k == "edit_existing"]
    # tool_error_recovery_rate: expected-failure cases that correctly return error dict
    failure_cases = [r for k, r in results.items() if not r.get("expected", True)]

    edit_success = (
        sum(1 for r in write_cases if r.get("success")) / max(len(write_cases), 1)
    )
    patch_apply = (
        sum(1 for r in edit_cases if r.get("patch")) / max(len(edit_cases), 1)
    )
    error_recovery = (
        sum(1 for r in failure_cases if r.get("success")) / max(len(failure_cases), 1)
    )

    return {
        "edit_success_rate": edit_success,
        "patch_apply_rate": patch_apply,
        "tool_error_recovery_rate": error_recovery,
    }


def all_fail_metrics() -> dict:
    """Return metrics that are all below threshold (for --fixture=all-fail test)."""
    return {
        "edit_success_rate": 0.0,
        "patch_apply_rate": 0.0,
        "tool_error_recovery_rate": 0.0,
    }


# ---------------------------------------------------------------------------
# Gate check + report
# ---------------------------------------------------------------------------


def check_gates(metrics: dict) -> list[str]:
    """Return list of failing gate descriptions."""
    failures: list[str] = []
    for name, threshold in THRESHOLDS.items():
        value = metrics.get(name, 0.0)
        if value < threshold:
            failures.append(f"{name}: {value:.2f} < {threshold:.2f}")
    return failures


def print_report(metrics: dict, failures: list[str]) -> None:
    print("\n=== Coding Toolchain Eval ===")
    for name, value in metrics.items():
        threshold = THRESHOLDS[name]
        status = "PASS" if value >= threshold else "FAIL"
        print(f"  [{status}] {name}: {value:.2f} (threshold: {threshold:.2f})")
    if failures:
        print(f"\nFailed gates: {len(failures)}")
        for f in failures:
            print(f"  x {f}")
    else:
        print("\nAll gates passed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description="Coding toolchain eval gates")
    parser.add_argument(
        "--fixture",
        default="",
        help="Use 'all-fail' to force all metrics below threshold",
    )
    args = parser.parse_args()

    if args.fixture == "all-fail":
        metrics = all_fail_metrics()
    else:
        fixture = load_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            results = await run_all_cases(fixture, tmp_dir)
            metrics = compute_metrics(results)

    failures = check_gates(metrics)
    print_report(metrics, failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
