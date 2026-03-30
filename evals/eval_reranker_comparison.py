"""Benchmark eval: FTS5 baseline vs LLM listwise vs fastembed cross-encoder reranker.

Harder corpus: 30 documents, 5 topics × 6 docs each.
Each topic has:
  - 2 rel_para docs: all query keywords present once each (AND-join FTS5 retrieves them),
    but surrounded by rich semantic paraphrase content → low BM25, low FTS5 rank
  - 2 trap docs: query keywords repeated 3-4× and in title → high BM25, high FTS5 rank,
    but the content discusses a peripheral/wrong angle (debugging, scope docs, etc.)
  - 2 noise docs: completely off-topic, zero keyword overlap → FTS5 never retrieves

NOTE: _build_fts_query uses AND-join, so all query tokens must be present for retrieval.
rel_para docs have all tokens once; traps have them many times. BM25 naturally ranks
traps above rel_para → FTS5 NDCG is reduced. A semantic reranker that understands the
traps are peripheral should demote traps and promote the paraphrase-rich rel_para docs.

Usage:
    uv run python evals/eval_reranker_comparison.py
    uv run python evals/eval_reranker_comparison.py --ollama-model qwen2.5:3b
    uv run python evals/eval_reranker_comparison.py --ollama-model qwen2.5:3b --runs 5
"""

import argparse
import math
import os
import statistics
import tempfile
import time
from pathlib import Path

from co_cli.knowledge._index_store import KnowledgeIndex, SearchResult


# ---------------------------------------------------------------------------
# Harder synthetic corpus — 5 topics × 6 docs
# rel_para: relevant, each query keyword present 1× (sparse BM25, low rank)
# trap: query keywords repeated 4-6× + in title (high BM25, wrong angle)
# noise: completely off-topic, zero keyword overlap (FTS5 never retrieves)
# ---------------------------------------------------------------------------

CORPUS: list[dict] = [
    # -------------------------------------------------------------------------
    # Topic A — query: "asyncio concurrency patterns"
    # rel_para: all 3 query keywords once each (AND-join retrieves them, low BM25)
    # traps: all 3 keywords 3-4x + in title (high BM25, wrong semantic content)
    # -------------------------------------------------------------------------
    {
        "path": "/docs/topicA_rel1.md",
        "title": "coroutine-based task management",
        "content": (
            "asyncio enables cooperative multitasking via await expressions. Concurrency in "
            "this model means tasks yield control voluntarily without preemption. Common "
            "patterns include task groups that scope child lifetimes to a parent and "
            "structured nurseries that enforce cleanup on exit. Awaitable objects suspend "
            "execution without blocking the thread, enabling single-thread I/O multiplexing."
        ),
        "relevant_to": "A",
    },
    {
        "path": "/docs/topicA_rel2.md",
        "title": "non-blocking I/O with cooperative scheduling",
        "content": (
            "asyncio tasks use concurrency patterns where await yields control to the event "
            "loop scheduler. Task groups cancel outstanding children when a scope exits. "
            "Non-blocking sockets and streams integrate via protocol callbacks. Structured "
            "lifetime management propagates exceptions from children to the parent task, "
            "preventing orphaned background work from leaking across scope boundaries."
        ),
        "relevant_to": "A",
    },
    {
        "path": "/docs/topicA_trap1.md",
        "title": "asyncio concurrency debugging guide",
        "content": (
            "Debugging asyncio concurrency patterns requires enabling PYTHONASYNCIODEBUG=1 to "
            "detect unawaited coroutines. Common asyncio concurrency pitfalls include deadlocks "
            "from blocking calls inside async functions. The asyncio patterns for timeout "
            "configuration use asyncio.wait_for() with a timeout parameter. Deadlock detection "
            "in asyncio concurrency scenarios often requires logging task states."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicA_trap2.md",
        "title": "thread-based concurrency patterns",
        "content": (
            "Thread-based concurrency patterns use the Python threading module to run tasks in "
            "parallel OS threads. Thread pools via concurrent.futures manage worker lifecycle. "
            "The GIL limits CPU-bound concurrency patterns in CPython — use multiprocessing "
            "instead. Thread-safe concurrency patterns require locks, semaphores, or queues to "
            "coordinate shared state across threads."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicA_noise1.md",
        "title": "CSS grid layout",
        "content": (
            "CSS Grid provides two-dimensional layout control using grid-template-columns and "
            "grid-template-rows. Grid items are placed using line numbers or named areas. "
            "The fr unit distributes remaining space proportionally. Auto-placement fills "
            "empty cells automatically following the grid flow algorithm."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicA_noise2.md",
        "title": "SQL window functions",
        "content": (
            "Window functions compute aggregates over a sliding row range without collapsing "
            "rows. PARTITION BY divides the result set into groups. ORDER BY within the OVER "
            "clause defines the frame ordering. ROW_NUMBER, RANK, and DENSE_RANK assign "
            "positional labels to rows within each partition."
        ),
        "relevant_to": None,
    },

    # -------------------------------------------------------------------------
    # Topic B — query: "pytest fixture design"
    # rel_para: all 3 query keywords once each; traps: full phrase repeated 3-4x + in title
    # -------------------------------------------------------------------------
    {
        "path": "/docs/topicB_rel1.md",
        "title": "dependency injection for test infrastructure",
        "content": (
            "pytest uses fixture design based on parameter injection from conftest.py factory "
            "functions. Yield-based teardown runs after the test body, ensuring resources are "
            "released. Test scaffolding declared once in conftest is shared across the entire "
            "suite without repetition. Factory functions return configured objects rather than "
            "constructing them inline, enabling flexible test infrastructure composition."
        ),
        "relevant_to": "B",
    },
    {
        "path": "/docs/topicB_rel2.md",
        "title": "reusable test scaffolding with scope control",
        "content": (
            "fixture scope in pytest controls the design of setup objects: once per module or "
            "session for expensive resources, per function for isolation. Parametrize "
            "decorators multiply a single test across input cases. Factories-as-fixtures "
            "return callables rather than single instances, allowing per-test customisation. "
            "Module-level sharing amortises costly setup like database connections."
        ),
        "relevant_to": "B",
    },
    {
        "path": "/docs/topicB_trap1.md",
        "title": "pytest fixture scope and lifecycle",
        "content": (
            "pytest fixture design supports four scope levels: function, class, module, and "
            "session. The pytest fixture lifecycle begins when first requested and ends based "
            "on scope. pytest fixture design best practice recommends preferring function scope "
            "for isolation. Autouse fixtures in pytest fixture design run automatically without "
            "explicit declaration in test parameters."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicB_trap2.md",
        "title": "Django fixture design for databases",
        "content": (
            "Django fixture design uses JSON or YAML serialized data loaded with loaddata. "
            "factory_boy is a fixture design library for generating model instances with "
            "randomized fields. Django fixture design for integration tests populates the test "
            "database before each test class. Fixture design in Django differs from pytest "
            "fixture design because fixtures are data files, not functions."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicB_noise1.md",
        "title": "Kubernetes resource limits",
        "content": (
            "Container resource limits in Kubernetes specify the maximum CPU and memory a pod "
            "may consume. Requests define the guaranteed minimum; limits define the ceiling. "
            "The kubelet enforces memory limits by OOM-killing containers that exceed them. "
            "CPU throttling occurs when a container exceeds its CPU limit but is not evicted."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicB_noise2.md",
        "title": "OAuth2 token refresh",
        "content": (
            "OAuth2 access tokens expire after a configured TTL. Refresh tokens are long-lived "
            "credentials used to obtain new access tokens without re-prompting the user. "
            "The token refresh flow sends a POST request to the authorization server token "
            "endpoint with grant_type=refresh_token. Revoked refresh tokens return an "
            "invalid_grant error."
        ),
        "relevant_to": None,
    },

    # -------------------------------------------------------------------------
    # Topic C — query: "sqlite fts5 ranking"
    # rel_para: all 3 query keywords once each; traps: full phrase repeated 3-4x + in title
    # -------------------------------------------------------------------------
    {
        "path": "/docs/topicC_rel1.md",
        "title": "BM25 scoring for full-text search",
        "content": (
            "sqlite fts5 ranking uses Okapi BM25 to combine term frequency with inverse "
            "document frequency. IDF penalises common terms that appear in many documents, "
            "reducing their discriminative power. Term frequency saturation means extra "
            "occurrences contribute diminishing returns. Probabilistic models estimate "
            "relevance from statistical co-occurrence patterns across the corpus."
        ),
        "relevant_to": "C",
    },
    {
        "path": "/docs/topicC_rel2.md",
        "title": "inverted index query execution",
        "content": (
            "sqlite uses fts5 to build inverted indexes; ranking is determined by posting "
            "lists that map tokens to sorted document identifiers. Multi-term queries "
            "intersect those lists to find candidates. Field boosting assigns higher weight "
            "to title matches versus body matches. The scoring formula combines per-field "
            "BM25 scores with boost factors to produce a final ordering."
        ),
        "relevant_to": "C",
    },
    {
        "path": "/docs/topicC_trap1.md",
        "title": "sqlite fts5 tokenizer configuration",
        "content": (
            "sqlite fts5 ranking uses bm25() as the default scoring function. The sqlite fts5 "
            "tokenizer options include porter, unicode61, and trigram for different ranking "
            "needs. sqlite fts5 ranking can be customised by passing weight parameters to the "
            "bm25() function. The fts5 ranking auxiliary function rank column controls sort "
            "order in sqlite fts5 queries."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicC_trap2.md",
        "title": "PostgreSQL full-text ranking with tsvector",
        "content": (
            "PostgreSQL full-text ranking uses ts_rank() and ts_rank_cd() to score tsvector "
            "documents. GIN indexes accelerate fts5-equivalent ranking queries in Postgres. "
            "The ranking function considers term frequency and document length normalisation. "
            "tsvector weights (A-D) enable field-level ranking boosts similar to sqlite fts5 "
            "ranking weight parameters."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicC_noise1.md",
        "title": "React component lifecycle",
        "content": (
            "React components go through mount, update, and unmount phases. useEffect runs "
            "after render and can return a cleanup function. getDerivedStateFromProps is called "
            "before every render in class components. The reconciler diffs the virtual DOM tree "
            "to determine the minimal set of DOM mutations required."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicC_noise2.md",
        "title": "JWT authentication claims",
        "content": (
            "JSON Web Tokens carry signed payload claims verified by the server without a "
            "database lookup. The header specifies the signing algorithm (HS256, RS256). "
            "Standard claims include iss, sub, aud, exp, and iat. Token verification checks "
            "the signature and expiry before trusting the payload claims."
        ),
        "relevant_to": None,
    },

    # -------------------------------------------------------------------------
    # Topic D — query: "kubernetes pod scheduling"
    # rel_para: all 3 query keywords once each; traps: full phrase repeated 3-4x + in title
    # -------------------------------------------------------------------------
    {
        "path": "/docs/topicD_rel1.md",
        "title": "container placement and node affinity",
        "content": (
            "kubernetes pod scheduling assigns workloads to nodes based on resource requests "
            "and co-location constraints. Affinity rules express preferences or hard "
            "requirements for placing containers near or away from each other. Headroom "
            "calculations ensure a node has spare capacity before accepting new work. "
            "Anti-affinity spreads replicas across failure domains for resilience."
        ),
        "relevant_to": "D",
    },
    {
        "path": "/docs/topicD_rel2.md",
        "title": "workload distribution across cluster nodes",
        "content": (
            "kubernetes scheduling places each pod using taints that mark nodes unsuitable "
            "for general workloads; tolerations allow specific pods to override that "
            "restriction. Topology spread constraints distribute replicas evenly across zones "
            "to limit blast radius. Bin-packing fills nodes to high utilisation before using "
            "new ones. Failure domain awareness prevents a zone outage from taking down all "
            "replicas."
        ),
        "relevant_to": "D",
    },
    {
        "path": "/docs/topicD_trap1.md",
        "title": "kubernetes pod scheduling configuration",
        "content": (
            "kubernetes pod scheduling uses nodeName to assign a pod directly to a node. "
            "nodeSelector in kubernetes pod scheduling matches label key-value pairs. Priority "
            "classes in kubernetes pod scheduling preempt lower-priority pods when resources "
            "are scarce. kube-scheduler plugins extend kubernetes pod scheduling with custom "
            "Filter and Score extension points."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicD_trap2.md",
        "title": "Docker Swarm container scheduling",
        "content": (
            "Docker Swarm scheduling distributes service tasks across manager and worker nodes. "
            "Placement constraints in Swarm scheduling restrict tasks to nodes matching label "
            "filters. Swarm scheduling uses a spread strategy by default to balance container "
            "count. Global services in Swarm scheduling run exactly one container per node."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicD_noise1.md",
        "title": "Python type hints and generics",
        "content": (
            "TypeVar declares a placeholder type for use in generic functions and classes. "
            "PEP 484 introduced the typing module with Optional, Union, List, and Dict aliases. "
            "Generic classes are parameterised with square bracket syntax. Protocol defines "
            "structural subtyping — a class satisfies a Protocol if it has the required methods."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicD_noise2.md",
        "title": "GraphQL schema design",
        "content": (
            "GraphQL schemas define types, queries, mutations, and subscriptions. Resolvers "
            "are functions that return data for each field. DataLoader batches and caches "
            "per-request database calls to solve the N+1 query problem. Schema stitching "
            "combines multiple subgraph schemas into a unified API gateway."
        ),
        "relevant_to": None,
    },

    # -------------------------------------------------------------------------
    # Topic E — query: "llm prompt engineering"
    # rel_para: all 3 query keywords once each; traps: full phrase repeated 3-4x + in title
    # -------------------------------------------------------------------------
    {
        "path": "/docs/topicE_rel1.md",
        "title": "instruction design for language model outputs",
        "content": (
            "llm prompt engineering starts with role assignment in the system message to "
            "shape behaviour before the first user turn. Few-shot examples demonstrate the "
            "expected output format and reasoning style. Chain-of-thought scaffolding elicits "
            "step-by-step reasoning by asking the model to think before answering. Temperature "
            "controls output diversity; lower values produce more deterministic responses."
        ),
        "relevant_to": "E",
    },
    {
        "path": "/docs/topicE_rel2.md",
        "title": "context window utilization and demonstration pairs",
        "content": (
            "llm prompt engineering uses in-context demonstration pairs to adapt model "
            "behaviour without gradient updates. Retrieval-augmented generation prepends "
            "retrieved passages to ground responses in external knowledge. System message "
            "framing sets behavioural constraints that persist across the conversation. "
            "Demonstration selection quality directly determines whether the model generalises."
        ),
        "relevant_to": "E",
    },
    {
        "path": "/docs/topicE_trap1.md",
        "title": "llm prompt engineering best practices guide",
        "content": (
            "llm prompt engineering best practices recommend iterating prompts systematically. "
            "LangChain provides prompt engineering templates for llm workflows. prompt "
            "engineering for llm outputs should specify the exact output format expected. "
            "engineering effective llm prompts requires version-controlling prompt strings "
            "alongside model configuration."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicE_trap2.md",
        "title": "software engineering prompts for code generation",
        "content": (
            "Code-generation models respond well to engineering prompts that include function "
            "signatures and docstrings. Software engineering prompt patterns ask the model to "
            "generate tests before implementation. Prompt engineering for software engineering "
            "tasks benefits from providing existing code context. Engineering prompts for "
            "code review ask the model to identify bugs and suggest fixes."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicE_noise1.md",
        "title": "B-tree index internals",
        "content": (
            "B-tree indexes maintain sorted key order across balanced tree pages. Page splits "
            "occur when an insert overflows a leaf page, propagating a separator key upward. "
            "The fill factor controls how full pages are kept, reserving space for future "
            "inserts to reduce splits. Sequential scans on B-tree indexes exploit physical "
            "page ordering for efficient range queries."
        ),
        "relevant_to": None,
    },
    {
        "path": "/docs/topicE_noise2.md",
        "title": "CQRS and event sourcing",
        "content": (
            "CQRS separates command handlers that mutate state from query handlers that read "
            "it. Event sourcing stores state as an immutable append-only log of domain events. "
            "Projections rebuild read models by replaying events. Eventual consistency between "
            "the write model and read projections is acceptable when reads can tolerate slight "
            "staleness."
        ),
        "relevant_to": None,
    },
]

# Topics list (used for query iteration)
TOPICS = [
    {"name": "A", "query": "asyncio concurrency patterns"},
    {"name": "B", "query": "pytest fixture design"},
    {"name": "C", "query": "sqlite fts5 ranking"},
    {"name": "D", "query": "kubernetes pod scheduling"},
    {"name": "E", "query": "llm prompt engineering"},
]

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


def mrr(results: list[SearchResult], relevant_paths: set[str]) -> float:
    """Mean Reciprocal Rank — reciprocal rank of first relevant result."""
    for i, r in enumerate(results):
        if r.path in relevant_paths:
            return 1.0 / (i + 1)
    return 0.0


def recall_at_k(results: list[SearchResult], relevant_paths: set[str], k: int) -> float:
    """Recall@k — fraction of relevant docs recovered in top-k."""
    if not relevant_paths:
        return 0.0
    return sum(1 for r in results[:k] if r.path in relevant_paths) / len(relevant_paths)


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def build_index(
    db_path: Path,
    *,
    backend: str = "fts5",
    reranker_model: str | None = None,
    ollama_host: str = "http://localhost:11434",
) -> KnowledgeIndex:
    """Create a fresh KnowledgeIndex with the full synthetic corpus loaded."""
    from co_cli.deps import CoConfig
    from co_cli.config import ModelConfig
    llm_reranker = (
        ModelConfig(provider="ollama-openai", model=reranker_model)
        if reranker_model else None
    )
    config = CoConfig(
        knowledge_db_path=db_path,
        knowledge_search_backend=backend,
        # Explicitly disable TEI cross-encoder so the correct reranker branch is
        # reached: None → LLM listwise (if set) or none. Without this, the
        # default "http://127.0.0.1:8282" always activates TEI regardless of
        # whether llm_reranker is set.
        knowledge_cross_encoder_reranker_url=None,
        knowledge_llm_reranker=llm_reranker,
        llm_host=ollama_host,
    )
    idx = KnowledgeIndex(config=config)
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


def run_benchmark(idx: KnowledgeIndex, label: str, notes: str = "", runs: int = 3) -> dict:
    """Run all 5 queries across multiple runs; return metrics and stable median latency.

    cold_ms = first query of first run (model init + first inference).
    warm_ms = median per-query latency across all queries in runs 2+ (steady-state).
    """
    # (run_idx, topic_idx, elapsed_ms, ndcg5, prec3, mrr_score, rec5)
    records: list[tuple[int, int, float, float, float, float, float]] = []

    for run_idx in range(runs):
        for topic_idx, topic in enumerate(TOPICS):
            query = topic["query"]
            relevant = RELEVANCE[query]

            start = time.perf_counter()
            results = idx.search(query, limit=5)
            elapsed_ms = (time.perf_counter() - start) * 1000

            records.append((
                run_idx,
                topic_idx,
                elapsed_ms,
                ndcg_at_k(results, relevant, k=5),
                precision_at_k(results, relevant, k=3),
                mrr(results, relevant),
                recall_at_k(results, relevant, k=5),
            ))

    cold_ms = records[0][2]  # first query, first run

    warm_latencies = [ms for (run_idx, _, ms, *_rest) in records if run_idx > 0]
    warm_ms = statistics.median(warm_latencies) if warm_latencies else cold_ms

    all_ndcg = [r[3] for r in records]
    all_prec = [r[4] for r in records]
    all_mrr = [r[5] for r in records]
    all_rec5 = [r[6] for r in records]

    return {
        "label": label,
        "notes": notes,
        "ndcg5": sum(all_ndcg) / len(all_ndcg),
        "mrr": sum(all_mrr) / len(all_mrr),
        "prec3": sum(all_prec) / len(all_prec),
        "rec5": sum(all_rec5) / len(all_rec5),
        "cold_ms": cold_ms,
        "warm_ms": warm_ms,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_table(rows: list[dict]) -> None:
    header = (
        f"{'Reranker':<25} | {'NDCG@5':>6} | {'MRR':>5} | {'Prec@3':>6} | "
        f"{'Rec@5':>5} | {'Warm(ms)':>8} | Notes"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        if "skip_reason" in row:
            print(
                f"{'  SKIP: ' + row['label']:<25} | {'':>6} | {'':>5} | {'':>6} | "
                f"{'':>5} | {'':>8} | {row['skip_reason']}"
            )
        else:
            print(
                f"{row['label']:<25} | {row['ndcg5']:>6.2f} | {row['mrr']:>5.2f} | "
                f"{row['prec3']:>6.2f} | {row['rec5']:>5.2f} | "
                f"{row['warm_ms']:>8.1f} | {row.get('notes', '')}"
            )
    print(sep)
    cold_times = [f"{r['label']}: {r['cold_ms']:.1f}ms" for r in rows if "cold_ms" in r]
    if cold_times:
        print(f"Cold (first-query init): {', '.join(cold_times)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Reranker benchmark eval")
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
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of benchmark runs per reranker (warm_ms = median of runs 2+)",
    )
    args = parser.parse_args()

    rows: list[dict] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Config 1: FTS5 baseline — always runs
        print("Running FTS5 baseline...")
        idx = build_index(tmp / "baseline" / "search.db", backend="fts5")
        rows.append(run_benchmark(idx, "FTS5 baseline", runs=args.runs))
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
                reranker_model=args.ollama_model,
                ollama_host=args.ollama_host,
            )
            rows.append(run_benchmark(
                idx, "LLM listwise",
                notes=f"{args.ollama_model} via Ollama",
                runs=args.runs,
            ))
            idx.close()
        else:
            rows.append({
                "label": "LLM listwise",
                "skip_reason": f"Ollama not reachable at {args.ollama_host}",
            })

    print()
    print_table(rows)


if __name__ == "__main__":
    main()
