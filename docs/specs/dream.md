# Co CLI — Dream

## Product Intent

**Goal:** Keep Co's reusable knowledge healthy over time by retrospectively mining sessions, consolidating duplicate artifacts, and archiving stale knowledge without hiding state from the user.

**Functional areas:**
- Session-end and manual dream-cycle entrypoints
- Retrospective transcript mining into reusable knowledge artifacts
- Similarity-based artifact consolidation
- Age and recall-based decay into a recoverable archive
- Dream state, stats, dry-run previews, and observability spans

**Non-goals:**
- Rewriting raw session transcripts
- Opaque provider-side memory
- Autonomous deletion of knowledge without recoverable archive
- Replacing turn-time recall or explicit knowledge search
- A long-running scheduler or daemon-owned dreaming loop

**Success criteria:** Dreaming strengthens Co's mission as a trusted local personal operator: raw memory stays intact, reusable knowledge becomes more coherent, stale low-use artifacts leave active recall, every mutation remains local and inspectable, and automatic work is bounded by config, timeouts, caps, and recoverability.

**Status:** Stable. The dream cycle is implemented in `co_cli/knowledge/dream.py`, available via `/knowledge dream`, and optionally run on session end when `knowledge.consolidation_enabled=true` and `knowledge.consolidation_trigger="session_end"`. It is off by default.

**Known gaps:** There is no wall-clock scheduler; the only automatic trigger is session shutdown. Mining only considers the configured recent transcript lookback and does not revisit already processed sessions after prompt or model changes. Similarity clustering is lexical token-Jaccard, so semantically similar but differently worded artifacts may not merge. `knowledge.max_artifact_count` is a soft setting and is not directly enforced by the dream cycle. A timeout can interrupt the cycle after earlier phase side effects have already happened; state persistence happens only on a completed non-dry run.

---

This spec owns the dream-cycle lifecycle. The broader persistent cognition model lives in [memory-knowledge.md](memory-knowledge.md). Startup and shutdown sequencing live in [bootstrap.md](bootstrap.md) and [system.md](system.md). Prompt injection and recall scoring live in [prompt-assembly.md](prompt-assembly.md). Model routing for dream miner and merge calls lives in [llm-models.md](llm-models.md).

## 1. What & How

Dreaming is Co's bounded, local batch-maintenance pass for the knowledge layer. It is named "dream" because it happens outside the immediate foreground turn and looks across prior experience for durable patterns, but it is not hidden autonomous memory. It reads local transcripts and active knowledge artifacts, writes local markdown artifacts, archives originals into a local subdirectory, and records local JSON state.

Dreaming serves the core product mission:

- **Trusted:** automatic mutations are gated by config or an explicit slash command, bounded by caps and timeouts, and recoverable through the archive.
- **Local:** transcripts, artifacts, archive files, state, and indexes live under the user-controlled Co home.
- **Personal:** retrospective mining looks for stable user preferences, feedback, rules, decisions, and references that were not obvious in a single turn.
- **Operator:** the system performs maintenance work on the user's behalf while preserving operator control through dry runs, stats, and restore commands.
- **Knowledge work:** the active corpus is kept coherent enough for future synthesis, planning, writing, and technical execution.

```mermaid
flowchart TD
    subgraph Entry["Entry Points"]
        Manual["/knowledge dream\n(manual trigger)"]
        Auto["session teardown\n(if consolidation_enabled=true)"]
    end

    subgraph Cycle["Dream Cycle — mine → merge → decay"]
        Mine["Phase 1: Transcript Mining\nrecent unprocessed sessions\n→ knowledge_save() via miner Agent\n→ new knowledge/*.md artifacts"]
        Merge["Phase 2: Merge\nactive same-kind similar clusters\n→ llm_call() for consolidated body\n→ write consolidated artifact, archive originals"]
        Decay["Phase 3: Decay\nold, unrecalled, non-protected artifacts\n→ archive to knowledge/_archive/"]
        State["Persist DreamState\n(processed_sessions, last_dream_at, cumulative stats)"]
    end

    Manual --> Mine
    Auto --> Mine
    Mine --> Merge
    Merge --> Decay
    Decay --> State
```

The cycle has three ordered phases. Each phase is independently try/except'd; one failure does not block the others. The whole cycle runs under an `asyncio.timeout()` bound.

Source-of-truth files:

| Path | Role |
| --- | --- |
| `knowledge/*.md` | Active reusable knowledge artifacts used by recall and search |
| `knowledge/_archive/*.md` | Recoverable archived artifacts, excluded from normal active loads |
| `knowledge/_dream_state.json` | Processed transcript names, last run timestamp, cumulative counters |
| `sessions/*.jsonl` | Raw append-only transcripts read by mining; never rewritten by dreaming |
| `co-cli-search.db` | Derived index updated when dream writes or archives artifacts |

## 2. Core Logic

### 2.1 Entry Points

Manual trigger:

```text
/knowledge dream
  -> run_dream_cycle(dry_run=false)
  -> print extracted, merged, decayed, and errors

/knowledge dream --dry
  -> run_dream_cycle(dry_run=true)
  -> report merge and decay counts only
  -> do not write files, archive artifacts, mine transcripts, or persist state
```

Automatic trigger:

```text
session teardown
  -> if knowledge.consolidation_enabled is true
  -> if knowledge.consolidation_trigger is "session_end"
  -> run dream cycle under the shutdown timeout
  -> log result
  -> never fail shutdown because dreaming failed
```

Automatic dreaming is deliberately behind `knowledge.consolidation_enabled=false` by default. This matches the mission boundary: Co may become more useful through long-term adaptation, but that adaptation must remain explicit, local, and inspectable.

### 2.2 State

`DreamState` persists at `knowledge/_dream_state.json`.

| Field | Meaning |
| --- | --- |
| `last_dream_at` | ISO timestamp for the last completed non-dry run |
| `processed_sessions` | Transcript filenames already mined successfully or intentionally skipped as empty |
| `stats.total_cycles` | Count of completed non-dry cycles |
| `stats.total_extracted` | Cumulative artifacts created by mining |
| `stats.total_merged` | Cumulative merge clusters completed |
| `stats.total_decayed` | Cumulative artifacts archived by decay |

Load behavior is forgiving: missing or corrupt state returns a fresh state object and logs corrupt state as a warning. Save behavior creates the knowledge directory if needed and writes indented JSON.

State is only the dream cursor and dashboard record. It is not the source of truth for knowledge content; markdown artifacts and archive files are.

### 2.3 Phase 1: Transcript Mining

Mining turns raw episodic memory into reusable knowledge by looking across prior experience for durable cross-turn signals.

Execution order:

```text
load dream state
list recent sessions by reverse filename order
limit to knowledge.consolidation_lookback_sessions
for each unprocessed session:
  load transcript
  if load fails:
    leave unprocessed for later retry
  if empty or no extractable window:
    mark processed
  build transcript window with wider text/tool caps
  split oversized window into overlapping chunks
  build dream miner agent (once per session)
  run agent.run(chunk, deps=deps) over each chunk
  stop after per-session save cap is reached
  count new active artifacts
  mark session processed
```

Mining uses the shared transcript-window builder (`_window.py`). It keeps user and assistant text plus selected tool calls/results, skips file-read style output, and drops large non-prose tool returns. Dream mining uses wider caps than agent-explicit knowledge saves because its purpose is cross-turn pattern discovery.

The dream miner is a **tool-using pydantic-ai Agent** equipped with the `knowledge_save()` tool. It is instructed to save only durable artifacts, especially:

- cross-turn patterns
- implicit preferences
- corrections whose meaning only becomes clear later
- stable decisions

It must avoid:

- ephemeral task state
- secrets and sensitive personal data
- codebase facts derivable by reading the repo
- unsupported speculation
- facts already obvious from a single recent slice

Mining marks a session processed only after the miner completes for that session. If the agent fails, the session remains unprocessed so a future cycle can retry.

### 2.4 Phase 2: Merge

Merge reduces duplication in active knowledge without editing transcripts or overwriting source artifacts.

Execution order:

```text
load active knowledge artifacts from top-level knowledge/*.md
discard decay_protected artifacts
group remaining artifacts by artifact_kind
within each kind, cluster by token-Jaccard threshold
cap clusters per cycle
cap artifacts per cluster
for each cluster:
  call llm_call(deps, prompt) with merge instructions for one consolidated body
  if body is too short or empty:
    skip cluster (no write, no archive)
  write a new consolidated artifact
  index the new artifact
  archive the original artifacts
```

Merge invariants:

- Only artifacts of the same `artifact_kind` can merge.
- `decay_protected=true` blocks merge participation.
- Original artifacts are archived only after the consolidated artifact is durably written.
- If archiving fails after the consolidated artifact is written, the consolidated artifact remains; the failure is logged and the cycle continues.
- The merge prompt may combine and deduplicate existing facts, but must not invent new facts.

The merge call is a **direct `llm_call`** (no tool access, body text only) — in contrast with the mining phase which uses a tool-equipped Agent that calls `knowledge_save()` to write artifacts.

The consolidated artifact uses `source_type: consolidated` and inherits the union of tags from the originals. The active index is updated for the consolidated artifact, and archived originals are removed from the index.

### 2.5 Phase 3: Decay

Decay removes stale, low-use knowledge from active recall while preserving it for restore.

Candidate selection:

```text
for each active artifact:
  if decay_protected:
    skip
  if created is missing, invalid, or newer than decay cutoff:
    skip
  if last_recalled exists and is newer than decay cutoff:
    skip
  include candidate
sort oldest created first
```

The cutoff is `now - knowledge.decay_after_days`. A candidate is archived only if it is old enough and either has never been recalled or was last recalled outside the same age window.

Decay archives at most 20 artifacts per cycle. Archive moves files into `knowledge/_archive/`, removes active index rows when a `KnowledgeStore` is available, and resolves filename collisions by suffixing rather than clobbering existing files.

### 2.6 Dry Run

Dry run is a preview mode for the destructive parts of dreaming.

Behavior:

- Mining is skipped because predicting extracted artifacts requires LLM writes.
- Merge reports the number of currently mergeable clusters (capped to per-cycle limit).
- Decay reports the number of currently decay-eligible artifacts, capped to the per-cycle archive limit.
- No files are written.
- No artifacts are archived.
- Dream state is not persisted.

Dry run is therefore a maintenance preview, not a full simulation of transcript mining.

### 2.7 Failure And Timeout Semantics

The cycle returns a `DreamResult` with extracted, merged, decayed, errors, and timeout status.

Each non-dry phase is isolated:

```text
try mining
  record "mine: ..." error on failure
try merge
  record "merge: ..." error on failure
try decay
  record "decay: ..." error on failure
persist completed-cycle state
```

The whole cycle runs under an `asyncio.timeout()` bound. On timeout, the result is marked `timed_out=true`, a timeout error string is appended, and partial result counts are returned. Timeout does not roll back any file writes that completed before cancellation.

Session shutdown wraps the dream call in its own timeout and catches all dream errors. Dreaming must never prevent terminal cleanup, shell cleanup, or async resource closure.

### 2.8 User Inspection And Recovery

Dreaming is inspectable through slash commands:

| Command | Purpose |
| --- | --- |
| `/knowledge dream --dry` | Preview merge and decay counts |
| `/knowledge dream` | Run the cycle now |
| `/knowledge stats` | Show active counts, archive count, last dream timestamp, cumulative dream stats, and decay candidates |
| `/knowledge restore [slug]` | List archived artifacts or restore one archived file by unambiguous filename prefix |
| `/knowledge decay-review --dry` | Preview decay candidates directly |
| `/knowledge decay-review` | Archive decay candidates after confirmation |

Archive restore moves an archived markdown file back to the active knowledge directory and reindexes the active directory when a store is available. Ambiguous restore slugs fail rather than guessing.

### 2.9 Observability

Dreaming emits OpenTelemetry spans under the `co.dream` tracer:

| Span | Purpose |
| --- | --- |
| `co.dream.cycle` | Whole-cycle envelope with dry-run, timeout, count, error, and timeout attributes |
| `co.dream.mine` | Mining phase count |
| `invoke_agent _dream_miner_agent` | Dream miner agent invocation with `agent.role=dream_miner` |
| `co.dream.merge` | Merge phase count |
| `co.dream.decay` | Decay phase count |

The session-end wrapper logs completion counts when changes occurred and logs timeout or failure warnings without surfacing them as foreground turn failures.

## 3. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge.consolidation_enabled` | `CO_KNOWLEDGE_CONSOLIDATION_ENABLED` | `false` | Enables dedup-on-write and dream-cycle maintenance |
| `knowledge.consolidation_trigger` | n/a | `session_end` | Automatic trigger mode: `session_end` or `manual` |
| `knowledge.consolidation_lookback_sessions` | n/a | `5` | Number of recent transcript files considered by mining |
| `knowledge.consolidation_similarity_threshold` | n/a | `0.75` | Token-Jaccard threshold for dedup and merge clusters |
| `knowledge.max_artifact_count` | n/a | `300` | Soft corpus-size setting; not directly enforced by the current dream cycle |
| `knowledge.decay_after_days` | `CO_KNOWLEDGE_DECAY_AFTER_DAYS` | `90` | Age and last-recall cutoff for decay candidacy |
| `knowledge.chunk_size` | `CO_KNOWLEDGE_CHUNK_SIZE` | `600` | Chunk size used when indexing consolidated artifacts |
| `knowledge.chunk_overlap` | `CO_KNOWLEDGE_CHUNK_OVERLAP` | `80` | Chunk overlap used when indexing consolidated artifacts |

Internal caps:

| Constant | Current Value | Purpose |
| --- | --- | --- |
| dream window text cap | `50` | Maximum text lines included in a mining window |
| dream window tool cap | `50` | Maximum tool lines included in a mining window |
| soft mining window limit | `16000` chars | Threshold before transcript windows are chunked |
| mining chunk size | `12000` chars | Chunk length for oversized windows |
| mining chunk overlap | `2000` chars | Overlap between oversized-window chunks |
| max mining saves per session | `5` | Per-session cap on new artifacts from mining |
| max merge clusters per cycle | `10` | Per-cycle merge cap |
| max artifacts per merge cluster | `5` | Cluster size cap |
| minimum merged body length | `20` chars | Guard against empty or unusable merge outputs |
| max decay archives per cycle | `20` | Per-cycle decay archive cap |
| default cycle timeout | `60` seconds | Timeout used by `run_dream_cycle()` |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/knowledge/dream.py` | Dream state, mining, merge, decay, dry-run, timeout, and orchestration |
| `co_cli/knowledge/prompts/dream_miner.md` | Instructions for retrospective transcript mining |
| `co_cli/knowledge/prompts/dream_merge.md` | Instructions for same-kind artifact consolidation |
| `co_cli/knowledge/_window.py` | Shared transcript-window builder used by dream mining |
| `co_cli/knowledge/similarity.py` | Token-Jaccard similarity and clustering helpers |
| `co_cli/knowledge/decay.py` | Decay candidate selection |
| `co_cli/knowledge/archive.py` | Archive and restore mechanics |
| `co_cli/knowledge/artifact.py` | Knowledge artifact schema and active top-level artifact loading |
| `co_cli/knowledge/frontmatter.py` | Knowledge markdown rendering and frontmatter validation |
| `co_cli/knowledge/store.py` | Derived index updates for consolidated and archived artifacts |
| `co_cli/tools/knowledge/write.py` | `knowledge_save()` write path used by dream mining |
| `co_cli/main.py` | Session-end dream trigger (`_maybe_run_dream_cycle`) |
| `co_cli/commands/knowledge.py` | `/knowledge dream`, `/knowledge restore`, `/knowledge decay-review`, and `/knowledge stats` |

## 5. Test Gates

| Property | Test file |
| --- | --- |
| Dream state load/save and forgiving corrupt-state recovery | `tests/knowledge/test_knowledge_dream.py` |
| Dream cycle orchestration: mine → merge → decay ordering, phase isolation | `tests/knowledge/test_knowledge_dream_cycle.py` |
| Dry-run counts, no-write guarantee, and state non-persistence | `tests/knowledge/test_knowledge_dream_cycle.py` |
| Cycle timeout: partial result, `timed_out=True`, errors list | `tests/knowledge/test_knowledge_dream_cycle.py` |
| Live full-cycle coverage (local, LLM) | `tests/knowledge/test_knowledge_dream_cycle.py` |
| Decay candidate selection: cutoff, `decay_protected`, `last_recalled` | `tests/knowledge/test_knowledge_decay.py` |
| Archive move and filename collision resolution | `tests/knowledge/test_knowledge_archive.py` |
| Restore: unambiguous slug succeeds; ambiguous or missing slug returns False | `tests/knowledge/test_knowledge_archive.py` |
| Token-Jaccard similarity and union-find clustering | `tests/knowledge/test_knowledge_similarity.py` |
