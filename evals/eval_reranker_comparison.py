"""Benchmark eval: FTS5 baseline vs LLM listwise vs GGUF cross-encoder reranker.

Synthetic corpus: 30 documents, 5 topics × 6 docs each.
Each topic has 2 relevant docs (contain unique anchor term + query keywords)
and 4 off-topic docs. Relevance is known by construction — no external data.

Usage:
    uv run python evals/eval_reranker_comparison.py
    uv run python evals/eval_reranker_comparison.py \\
        --model-path ~/models/bge-reranker-v2-m3-Q5_K_M.gguf \\
        --ollama-model qwen2.5:3b
"""

import argparse
import math
import os
import tempfile
import time
from pathlib import Path

from co_cli.knowledge_index import KnowledgeIndex, SearchResult


# ---------------------------------------------------------------------------
# Synthetic corpus — 5 topics × 6 docs (2 relevant + 4 off-topic)
# ---------------------------------------------------------------------------

TOPICS = [
    {"name": "A", "query": "asyncio concurrency patterns", "anchor": "vexaprime"},
    {"name": "B", "query": "pytest fixture design", "anchor": "quorbital"},
    {"name": "C", "query": "sqlite fts5 ranking", "anchor": "zymorphlex"},
    {"name": "D", "query": "kubernetes pod scheduling", "anchor": "thraxinode"},
    {"name": "E", "query": "llm prompt engineering", "anchor": "flexorant"},
]

CORPUS: list[dict] = []
for _i, _topic in enumerate(TOPICS):
    _anchor = _topic["anchor"]
    _words = _topic["query"].split()
    # 2 relevant docs
    CORPUS.append({
        "path": f"/docs/topic{_i + 1}_rel1.md",
        "title": f"{_anchor} {_words[0]} guide",
        "content": (
            f"This document discusses {_anchor} and {_topic['query']}. "
            f"Key concepts include {_words[0]} and {_words[-1]}. "
            f"The {_anchor} system enables efficient {_topic['query']}."
        ),
        "relevant_to": _topic["name"],
    })
    CORPUS.append({
        "path": f"/docs/topic{_i + 1}_rel2.md",
        "title": f"Advanced {_anchor} techniques",
        "content": (
            f"Advanced techniques for {_anchor} in {_topic['query']} scenarios. "
            f"When working with {_words[0]}, {_anchor} provides optimal results."
        ),
        "relevant_to": _topic["name"],
    })
    # 4 off-topic docs (using other topics' content)
    _others = [t for t in TOPICS if t["name"] != _topic["name"]]
    for _j, _other in enumerate(_others[:4]):
        CORPUS.append({
            "path": f"/docs/topic{_i + 1}_off{_j + 1}.md",
            "title": f"Guide to {_other['anchor']} {_other['query'].split()[0]}",
            "content": (
                f"This covers {_other['anchor']} and {_other['query']}. "
                f"Not related to the primary topic query."
            ),
            "relevant_to": None,
        })

# Relevance map: query → set of relevant paths
RELEVANCE: dict[str, set[str]] = {}
for _topic in TOPICS:
    RELEVANCE[_topic["query"]] = {
        doc["path"] for doc in CORPUS if doc["relevant_to"] == _topic["name"]
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def ndcg_at_k(results: list[SearchResult], relevant_paths: set[str], k: int) -> float:
    """Binary NDCG@k."""
    dcg = sum(
        (1.0 if r.path in relevant_paths else 0.0) / math.log2(i + 2)
        for i, r in enumerate(results[:k])
    )
    n_relevant = min(len(relevant_paths), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(n_relevant))
    return dcg / idcg if idcg > 0 else 0.0


def precision_at_k(results: list[SearchResult], relevant_paths: set[str], k: int) -> float:
    """Precision@k."""
    if k == 0:
        return 0.0
    return sum(1 for r in results[:k] if r.path in relevant_paths) / k


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def build_index(db_path: Path, **kwargs) -> KnowledgeIndex:
    """Create a fresh KnowledgeIndex with the full synthetic corpus loaded."""
    idx = KnowledgeIndex(db_path, **kwargs)
    for doc in CORPUS:
        idx.index(
            source="memory",
            kind="article",
            path=doc["path"],
            title=doc["title"],
            content=doc["content"],
            hash=doc["path"],
            mtime=0.0,
        )
    return idx


def run_benchmark(idx: KnowledgeIndex, label: str, notes: str = "") -> dict:
    """Run all 5 queries and collect NDCG@5, Prec@3, cold/warm latency."""
    ndcg_scores: list[float] = []
    prec_scores: list[float] = []
    latencies: list[float] = []

    for topic in TOPICS:
        query = topic["query"]
        relevant = RELEVANCE[query]

        start = time.perf_counter()
        results = idx.search(query, limit=5)
        elapsed_ms = (time.perf_counter() - start) * 1000

        latencies.append(elapsed_ms)
        ndcg_scores.append(ndcg_at_k(results, relevant, k=5))
        prec_scores.append(precision_at_k(results, relevant, k=3))

    return {
        "label": label,
        "notes": notes,
        "ndcg5": sum(ndcg_scores) / len(ndcg_scores),
        "prec3": sum(prec_scores) / len(prec_scores),
        "cold_ms": latencies[0],
        "warm_ms": sum(latencies[1:]) / len(latencies[1:]) if len(latencies) > 1 else latencies[0],
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_table(rows: list[dict]) -> None:
    header = (
        f"{'Reranker':<25} | {'NDCG@5':>6} | {'Prec@3':>6} | "
        f"{'Cold (ms)':>9} | {'Warm (ms)':>9} | Notes"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        if "skip_reason" in row:
            print(
                f"{'  SKIP: ' + row['label']:<25} | {'':>6} | {'':>6} | "
                f"{'':>9} | {'':>9} | {row['skip_reason']}"
            )
        else:
            print(
                f"{row['label']:<25} | {row['ndcg5']:>6.2f} | {row['prec3']:>6.2f} | "
                f"{row['cold_ms']:>9.1f} | {row['warm_ms']:>9.1f} | {row.get('notes', '')}"
            )
    print(sep)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Reranker benchmark eval")
    parser.add_argument(
        "--model-path",
        default=os.getenv("CO_KNOWLEDGE_RERANKER_MODEL_PATH", ""),
        help="Path to GGUF cross-encoder model (e.g. bge-reranker-v2-m3-Q5_K_M.gguf)",
    )
    parser.add_argument(
        "--ollama-host",
        default=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        help="Ollama host URL",
    )
    parser.add_argument(
        "--ollama-model",
        default="qwen2.5:3b",
        help="Ollama model for listwise reranking",
    )
    args = parser.parse_args()

    rows: list[dict] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Config 1: FTS5 baseline — always runs
        print("Running FTS5 baseline...")
        idx = build_index(tmp / "baseline" / "search.db", backend="fts5")
        rows.append(run_benchmark(idx, "FTS5 baseline"))
        idx.close()

        # Config 2: LLM listwise via Ollama
        import httpx
        ollama_ok = False
        try:
            httpx.get(args.ollama_host, timeout=2.0)
            ollama_ok = True
        except Exception:
            pass

        if ollama_ok:
            print(f"Running LLM listwise reranker ({args.ollama_model} via Ollama)...")
            idx = build_index(
                tmp / "ollama" / "search.db",
                backend="fts5",
                reranker_provider="ollama",
                reranker_model=args.ollama_model,
                ollama_host=args.ollama_host,
            )
            rows.append(run_benchmark(idx, "LLM listwise", notes=f"{args.ollama_model} via Ollama"))
            idx.close()
        else:
            rows.append({
                "label": "LLM listwise",
                "skip_reason": f"Ollama not reachable at {args.ollama_host}",
            })

        # Config 3: GGUF cross-encoder
        local_ok = False
        if args.model_path:
            try:
                from llama_cpp import Llama  # noqa: F401
                local_ok = Path(args.model_path).exists()
            except ImportError:
                pass

        if local_ok:
            model_name = Path(args.model_path).name
            print(f"Running GGUF cross-encoder ({model_name})...")
            idx = build_index(
                tmp / "local" / "search.db",
                backend="fts5",
                reranker_provider="local",
                reranker_model_path=args.model_path,
            )
            rows.append(run_benchmark(idx, "Cross-encoder", notes=f"{model_name}"))
            idx.close()
        else:
            if not args.model_path:
                reason = "No --model-path provided (set CO_KNOWLEDGE_RERANKER_MODEL_PATH to skip)"
            elif not Path(args.model_path).exists():
                reason = f"Model file not found: {args.model_path}"
            else:
                reason = "llama-cpp-python not installed (uv sync --group reranker)"
            rows.append({"label": "Cross-encoder", "skip_reason": reason})

    print()
    print_table(rows)
    print()
    print("Model download (bge-reranker-v2-m3, ~600MB):")
    print("  huggingface-cli download BAAI/bge-reranker-v2-m3-GGUF \\")
    print("    bge-reranker-v2-m3-Q5_K_M.gguf --local-dir ~/models/")


if __name__ == "__main__":
    main()
