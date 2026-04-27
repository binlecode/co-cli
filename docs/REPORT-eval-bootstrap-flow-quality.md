# Eval Report: Bootstrap Flow Quality

## Run: 2026-04-26 14:28:41 UTC

**Model:** ollama / qwen3.5:35b-a3b-think  
**Bootstrap timeout:** 30s for `create_deps()`  
**Workspace:** /Users/binle/workspace_genai/co-cli  
**Total runtime:** 1132ms  
**Result:** 4/4 passed  
**Knowledge degraded:** yes

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `create-deps` | PASS | 1108ms |
| `build-agent` | PASS | 4ms |
| `restore-session-index` | PASS | 2ms |
| `banner-boundary` | PASS | 10ms |

### Case Details

#### `create-deps` — PASS
- **create_deps** (1108ms): backend=fts5 tools=29 mcp_tools=2 skills=1 degradations=1
- **registry_state** (0ms): tool_registry=yes mcp_tools=2 model_skills=1 completer_words=18
- **mcp_state** (0ms): configured=1 connected=1 failed=0 tools=2

#### `build-agent` — PASS
- **build_agent** (4ms): agent_type=Agent tool_registry=yes

#### `restore-session-index` — PASS
- **restore_session** (1ms): path=/Users/binle/.co-cli/sessions/2026-04-16-T235238Z-b8445d2b.jsonl state=restored
- **init_memory_index** (1ms): memory_index=ready search_results=0

#### `banner-boundary` — PASS
- **display_welcome_banner** (10ms): chars=960 missing=0

### Startup Status Timeline

- `+1099ms` `  Reranker degraded — TEI cross-encoder unavailable; search results will be unranked`
- `+1099ms` `  Knowledge degraded — embedder unavailable (not reachable — [Errno 61] Connection refused); using fts5`
- `+1108ms` `  Knowledge synced — 0 item(s) (fts5)`
- `+1113ms` `  Session restored — b8445d2b...`

### Bootstrap Logs

- `INFO [co_cli.bootstrap.core] Ollama runtime num_ctx=131072 differs from config llm.num_ctx=0 — using runtime value`
- `WARNING [co_cli.bootstrap.core] TEI cross-encoder unavailable; degrading to none`
- `WARNING [co_cli.bootstrap.core] Hybrid skipped: embedder unavailable — not reachable — [Errno 61] Connection refused`
