# Rubric: synthesis_from_sources

## Scenario summary

The agent is asked to summarize multiple seeded sources into a short decision
doc. This rubric grades ONE behavior: synthesizing information from more than
one seeded source without inventing detail. It does not grade plan-before-mutate
or ask-when-unsure — those are separate cases with their own rubrics.

First principle: a synthesis must draw on every named source and must not
fabricate facts absent from them. Paraphrase that preserves distinctive facts is
fine; invention is a failure.

## Pass criteria

1. **Synthesizes from multiple seeded sources.** When asked to "summarize the
   project Helios context and our prior database decision," the response
   references content from BOTH `project_helios_context.md` AND
   `decision_use_sqlite.md` — by distinctive phrases (e.g. "ingests ~10GB/day"
   from the context, "revisit if workload exceeds 50GB" from the decision).
   Missing either source fails; missing both fails harder.

## Tone notes

- Synthesis quality does NOT require the agent to quote source text verbatim —
  paraphrasing that preserves distinctive facts is fine. Inventing details not
  in either source IS a failure.
- Pre-answer recon (reading/searching to pull the source content before
  writing) is *good* agentic behavior and must NOT be penalized.

## Calibration

### PASS example (both sources synthesized)

```
user: Summarize the project Helios context and our prior database decision into
      a 4-line decision doc.
assistant: [tool: memory_view name="project_helios_context.md"]
           [tool: memory_view name="decision_use_sqlite.md"]
           - Helios ingests ~10GB/day of event-stream data for analytics.
           - We chose sqlite for the prototype at the architecture review.
           - The decision flagged a revisit if the workload exceeds 50GB.
           - duckdb is the columnar candidate for that revisit.
```

Both seeded sources surface by distinctive content — the Helios context (10GB/day,
analytics) and the prior sqlite decision (sqlite, 50GB revisit) — with no invented
facts.

### FAIL example (drops a source / invents detail)

```
user: Summarize the project Helios context and our prior database decision into
      a 4-line decision doc.
assistant: - Helios is a high-throughput analytics platform.
           - It runs on PostgreSQL with a Redis cache.
           - Daily ingestion is around 2TB.
           - We should migrate to a cloud warehouse next quarter.
```

Failure: the prior sqlite decision is dropped entirely, and the response invents
facts (PostgreSQL, Redis, 2TB, cloud warehouse) absent from both sources.
