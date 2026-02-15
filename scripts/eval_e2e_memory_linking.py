#!/usr/bin/env python3
"""E2E: Memory linking — related field + one-hop traversal in recall.

Saves two memories with a related link, then recalls and verifies that
one-hop traversal surfaces the connected memory.

Usage:
    uv run python scripts/eval_e2e_memory_linking.py
"""

import asyncio
import os
import sys
from pathlib import Path

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "ollama",
    "OLLAMA_MODEL": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
}
for _k, _v in _ENV_DEFAULTS.items():
    if _k not in os.environ:
        os.environ[_k] = _v

import yaml  # noqa: E402
from pydantic_ai._run_context import RunContext  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from co_cli.agent import get_agent  # noqa: E402
from co_cli.config import get_settings  # noqa: E402
from co_cli.deps import CoDeps  # noqa: E402
from co_cli.shell_backend import ShellBackend  # noqa: E402
from co_cli.tools.memory import recall_memory, _load_all_memories  # noqa: E402


UNIQUE_TAG = "_e2e_linking_test"


def _create_memories(memory_dir: Path) -> tuple[Path, Path]:
    """Create two linked test memories. Returns (path_a, path_b)."""
    memory_dir.mkdir(parents=True, exist_ok=True)

    existing = _load_all_memories(memory_dir)
    max_id = max((m.id for m in existing), default=0)

    id_a = max_id + 1
    id_b = max_id + 2
    slug_a = f"{id_a:03d}-e2e-pytest-preference"
    slug_b = f"{id_b:03d}-e2e-asyncio-testing"

    # Memory A: about pytest
    fm_a = {
        "id": id_a,
        "created": "2026-02-14T00:00:00+00:00",
        "tags": ["preference", "python", "testing", UNIQUE_TAG],
        "source": "detected",
        "related": [slug_b],  # Links to B
    }
    content_a = "User strongly prefers pytest for all Python testing work"
    path_a = memory_dir / f"{slug_a}.md"
    path_a.write_text(
        f"---\n{yaml.dump(fm_a, default_flow_style=False)}---\n\n{content_a}\n",
        encoding="utf-8",
    )

    # Memory B: about asyncio testing (linked from A)
    fm_b = {
        "id": id_b,
        "created": "2026-02-14T00:00:01+00:00",
        "tags": ["preference", "python", "asyncio", UNIQUE_TAG],
        "source": "detected",
        "related": [slug_a],  # Links back to A
    }
    content_b = "User always uses the companion async runner for coroutine test functions"
    path_b = memory_dir / f"{slug_b}.md"
    path_b.write_text(
        f"---\n{yaml.dump(fm_b, default_flow_style=False)}---\n\n{content_b}\n",
        encoding="utf-8",
    )

    return path_a, path_b


def _cleanup(memory_dir: Path) -> None:
    """Remove test memories."""
    for p in memory_dir.glob("*e2e-*"):
        if UNIQUE_TAG in p.read_text(encoding="utf-8"):
            p.unlink(missing_ok=True)


async def main() -> int:
    settings = get_settings()
    memory_dir = Path.cwd() / ".co-cli" / "knowledge" / "memories"

    print("=" * 60)
    print("  E2E: Memory Linking (One-Hop Traversal)")
    print("=" * 60)

    # Step 1: Create linked memories
    print("\n[1] Creating linked test memories...")
    path_a, path_b = _create_memories(memory_dir)
    print(f"    Memory A: {path_a.name}")
    print(f"    Memory B: {path_b.name}")
    print(f"    A.related → [{path_b.stem}]")
    print(f"    B.related → [{path_a.stem}]")

    try:
        # Step 2: Recall with a query that matches only A
        print("\n[2] Recalling with query 'pytest' (should match A, traverse to B)...")

        agent, _, _ = get_agent()
        deps = CoDeps(
            shell=ShellBackend(),
            session_id="e2e-memory-linking",
            memory_dedup_threshold=settings.memory_dedup_threshold,
        )
        ctx = RunContext(deps=deps, model=agent.model, usage=RunUsage())

        result = await recall_memory(ctx, "pytest", max_results=5)

        print(f"    Count: {result['count']}")
        print(f"    Display:\n{result['display'][:500]}")

        # Step 3: Verify one-hop traversal
        print("\n[3] Verifying one-hop traversal...")
        result_ids = [r["id"] for r in result.get("results", [])]
        has_related_hop = any(
            r.get("related_hop") for r in result.get("results", [])
        )
        has_linked_content = "companion async runner" in result["display"]
        has_pytest_content = "prefers pytest" in result["display"]
        has_related_section = "Related memories" in result["display"]

        checks = {
            "Direct match found (pytest preference)": has_pytest_content,
            "One-hop traversal surfaced linked memory": has_linked_content,
            "Related memories section present": has_related_section or has_related_hop,
            "More than 1 result returned": result["count"] > 1,
        }

        all_pass = True
        for check, passed in checks.items():
            status = "PASS" if passed else "FAIL"
            print(f"    {status}: {check}")
            if not passed:
                all_pass = False

        verdict = "PASS" if all_pass else "FAIL"
        print(f"\n{'=' * 60}")
        print(f"  Verdict: {verdict}")
        print(f"{'=' * 60}")
        return 0 if all_pass else 1

    finally:
        # Step 4: Cleanup
        print("\n[4] Cleaning up test memories...")
        _cleanup(memory_dir)
        print("    Done.")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
