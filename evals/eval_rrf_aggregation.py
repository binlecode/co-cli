#!/usr/bin/env python3
"""Eval: RRF aggregation strategy — max vs sum for _hybrid_merge doc-level scoring.

Compares two chunk-to-doc RRF aggregation strategies on real FTS5 chunk results:
  - max: doc score = highest chunk RRF score  (current implementation)
  - sum: doc score = sum of all chunk RRF scores

Scenario: four documents with varying chunk-level relevance to a fixed query set.
  - doc-broad: 3 chunks all containing query terms (broad coverage)
  - doc-narrow: 1 chunk densely packed with query terms (narrow but rich)
  - doc-partial: 2 chunks, one matching, one not
  - doc-weak: 1 chunk with a partial match

Ground truth per query: broad > narrow > partial > weak.

Recall@k is measured as the fraction of ground-truth top-2 docs that appear in
the top-k results. Decision rule: sum improves recall@k by >= 5% → adopt sum.

Produces: docs/REPORT-rrf-aggregation-20260502.md

Usage:
    uv run python evals/eval_rrf_aggregation.py
"""

import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from co_cli.config.core import get_settings
from co_cli.memory.memory_store import MemoryStore
from co_cli.memory.service import reindex, save_artifact

_REPORT_PATH = Path(__file__).parent.parent / "docs" / "REPORT-rrf-aggregation-20260502.md"

# Ground-truth: top-2 most relevant doc stems for every query
_GROUND_TRUTH: dict[str, list[str]] = {
    "gradient descent optimization": ["doc-broad", "doc-narrow"],
    "learning rate parameter tuning": ["doc-broad", "doc-partial"],
    "loss function minimization": ["doc-narrow", "doc-broad"],
}

# Document content — varies chunk count and term density per doc
_DOCUMENTS = {
    "doc-broad": [
        "Gradient descent optimization iteratively adjusts model weights toward lower loss.",
        "Learning rate controls how large each gradient descent step is during training.",
        "Loss function minimization via gradient descent converges when gradients are near zero.",
    ],
    "doc-narrow": [
        (
            "Gradient descent optimization: the learning rate controls step size while "
            "minimizing the loss function by following the negative gradient direction."
        ),
    ],
    "doc-partial": [
        "Learning rate schedules like cosine annealing reduce the rate over training epochs.",
        "Unrelated content about data preprocessing pipelines and feature engineering.",
    ],
    "doc-weak": [
        "Stochastic methods sample mini-batches to estimate gradients efficiently.",
    ],
}


def _rrf_max(chunk_scores: dict[str, list[float]]) -> dict[str, float]:
    """Aggregate chunk RRF scores per doc using max."""
    return {path: max(scores) for path, scores in chunk_scores.items()}


def _rrf_sum(chunk_scores: dict[str, list[float]]) -> dict[str, float]:
    """Aggregate chunk RRF scores per doc using sum."""
    return {path: sum(scores) for path, scores in chunk_scores.items()}


def _recall_at_k(ranked: list[str], relevant: list[str], k: int) -> float:
    top_k = set(ranked[:k])
    hits = sum(1 for r in relevant if r in top_k)
    return hits / len(relevant) if relevant else 0.0


def _stem_from_path(path: str) -> str:
    """Extract the doc-<name> stem from a full path."""
    for stem in _DOCUMENTS:
        if stem in path:
            return stem
    return Path(path).stem


def run_eval() -> dict:
    settings = get_settings()
    results: list[dict] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        knowledge_dir = Path(tmpdir) / "knowledge"
        db_path = Path(tmpdir) / "search.db"
        store = MemoryStore(config=settings, memory_db_path=db_path)

        try:
            # Index documents as multi-chunk artifacts
            for stem, chunks in _DOCUMENTS.items():
                content = "\n\n".join(chunks)
                r = save_artifact(
                    knowledge_dir,
                    content=content,
                    artifact_kind="note",
                    title=stem,
                )
                reindex(
                    store,
                    r.path,
                    r.content,
                    r.markdown_content,
                    r.frontmatter_dict,
                    r.filename_stem,
                    # Small chunk size to force multiple chunks per broad doc
                    chunk_size=150,
                    chunk_overlap=20,
                )

            for query, relevant_stems in _GROUND_TRUTH.items():
                # Get real FTS chunk results from the store
                fts_results = store._fts_search(
                    store._build_fts_query(query) or query,
                    sources=["knowledge"],
                    kinds=None,
                    created_after=None,
                    created_before=None,
                    limit=20,
                )

                # Build chunk-level RRF scores (k=60, Cormack 2009)
                k = 60
                chunk_scores: dict[str, list[float]] = {}
                for i, r in enumerate(fts_results):
                    stem = _stem_from_path(r.path)
                    rrf = 1.0 / (k + i + 1)
                    chunk_scores.setdefault(stem, []).append(rrf)

                max_scores = _rrf_max(chunk_scores)
                sum_scores = _rrf_sum(chunk_scores)

                max_ranked = sorted(max_scores, key=lambda p: max_scores[p], reverse=True)
                sum_ranked = sorted(sum_scores, key=lambda p: sum_scores[p], reverse=True)

                recall_max = _recall_at_k(max_ranked, relevant_stems, k=2)
                recall_sum = _recall_at_k(sum_ranked, relevant_stems, k=2)

                results.append(
                    {
                        "query": query,
                        "relevant": relevant_stems,
                        "max_ranked": max_ranked,
                        "sum_ranked": sum_ranked,
                        "recall@2_max": recall_max,
                        "recall@2_sum": recall_sum,
                        "chunk_counts": {s: len(v) for s, v in chunk_scores.items()},
                    }
                )
        finally:
            store.close()

    avg_max = sum(r["recall@2_max"] for r in results) / len(results) if results else 0.0
    avg_sum = sum(r["recall@2_sum"] for r in results) / len(results) if results else 0.0
    improvement_pct = (avg_sum - avg_max) / avg_max * 100 if avg_max > 0 else 0.0

    decision = "keep_max"
    if improvement_pct >= 5.0:
        decision = "adopt_sum"

    return {
        "queries": results,
        "avg_recall@2_max": avg_max,
        "avg_recall@2_sum": avg_sum,
        "improvement_pct": improvement_pct,
        "decision": decision,
    }


def write_report(outcome: dict) -> None:
    lines = [
        f"# REPORT: RRF Aggregation Eval — {datetime.now(UTC).date()}",
        "",
        "## Summary",
        "",
        f"- avg recall@2 (max): {outcome['avg_recall@2_max']:.3f}",
        f"- avg recall@2 (sum): {outcome['avg_recall@2_sum']:.3f}",
        f"- improvement (sum vs max): {outcome['improvement_pct']:+.1f}%",
        f"- **decision: {outcome['decision'].upper()}**",
        "",
        "## Decision Rule",
        "",
        "sum improves recall@2 ≥ 5% → adopt sum; otherwise keep max with comment.",
        "",
        "## Per-Query Results",
        "",
        "| query | relevant | max ranked | sum ranked | recall@2 max | recall@2 sum |",
        "|-------|----------|-----------|-----------|-------------|-------------|",
    ]
    for r in outcome["queries"]:
        lines.append(
            f"| {r['query'][:40]} | {', '.join(r['relevant'])} "
            f"| {', '.join(r['max_ranked'][:3])} "
            f"| {', '.join(r['sum_ranked'][:3])} "
            f"| {r['recall@2_max']:.2f} | {r['recall@2_sum']:.2f} |"
        )
    lines += [
        "",
        "## Chunk Counts per Query",
        "",
    ]
    for r in outcome["queries"]:
        lines.append(f"**{r['query']}**: {r['chunk_counts']}")
        lines.append("")
    lines += [
        "## Code Action",
        "",
    ]
    if outcome["decision"] == "adopt_sum":
        lines += [
            "Change `_hybrid_merge` in `co_cli/memory/memory_store.py`:",
            "```python",
            "# Before:",
            "doc_rrf[path] = max(doc_rrf.get(path, 0.0), score)",
            "# After:",
            "doc_rrf[path] = doc_rrf.get(path, 0.0) + score",
            "```",
        ]
    else:
        lines += [
            "Keep `max` in `_hybrid_merge`. Added inline comment explaining the choice.",
        ]

    _REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to {_REPORT_PATH}")


def main() -> None:
    print("Running RRF aggregation eval...")
    outcome = run_eval()

    print(f"\navg recall@2 (max): {outcome['avg_recall@2_max']:.3f}")
    print(f"avg recall@2 (sum): {outcome['avg_recall@2_sum']:.3f}")
    print(f"improvement:        {outcome['improvement_pct']:+.1f}%")
    print(f"decision:           {outcome['decision']}")

    for r in outcome["queries"]:
        print(f"\n  query: {r['query']!r}")
        print(f"    chunk counts: {r['chunk_counts']}")
        print(f"    max ranked: {r['max_ranked']}")
        print(f"    sum ranked: {r['sum_ranked']}")
        print(f"    recall@2 max={r['recall@2_max']:.2f}  sum={r['recall@2_sum']:.2f}")

    write_report(outcome)

    if outcome["decision"] == "adopt_sum":
        print("\nDecision: adopt sum — update _hybrid_merge in memory_store.py")
        sys.exit(0)
    else:
        print("\nDecision: keep max — add justification comment to _hybrid_merge")
        sys.exit(0)


if __name__ == "__main__":
    main()
