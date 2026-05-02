# REPORT: RRF Aggregation Eval — 2026-05-02

## Summary

- avg recall@2 (max): 0.667
- avg recall@2 (sum): 0.667
- improvement (sum vs max): +0.0%
- **decision: KEEP_MAX**

## Decision Rule

sum improves recall@2 ≥ 5% → adopt sum; otherwise keep max with comment.

## Per-Query Results

| query | relevant | max ranked | sum ranked | recall@2 max | recall@2 sum |
|-------|----------|-----------|-----------|-------------|-------------|
| gradient descent optimization | doc-broad, doc-narrow | doc-broad, doc-narrow | doc-broad, doc-narrow | 1.00 | 1.00 |
| learning rate parameter tuning | doc-broad, doc-partial |  |  | 0.00 | 0.00 |
| loss function minimization | doc-narrow, doc-broad | doc-narrow, doc-broad | doc-narrow, doc-broad | 1.00 | 1.00 |

## Chunk Counts per Query

**gradient descent optimization**: {'doc-broad': 1, 'doc-narrow': 1}

**learning rate parameter tuning**: {}

**loss function minimization**: {'doc-narrow': 1, 'doc-broad': 1}

## Code Action

Keep `max` in `_hybrid_merge`. Added inline comment explaining the choice.
