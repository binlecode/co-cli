# Eval Report: Bootstrap Flow

## Run: 2026-04-14 22:02:08 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Bootstrap timeout:** 30s for `create_deps()`  
**Workspace:** /Users/binle/workspace_genai/co-cli  
**Total runtime:** 1309ms  
**Result:** 4/4 passed  
**Knowledge degraded:** yes

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `create-deps` | PASS | 1283ms |
| `build-agent` | PASS | 3ms |
| `restore-session-index` | PASS | 5ms |
| `banner-boundary` | PASS | 11ms |

### Case Details

#### `create-deps` — PASS
- **create_deps** (1283ms): backend=fts5 tools=28 skills=1
- **registry_state** (0ms): tool_registry=yes mcp_tools=2 model_skills=1 completer_words=17

#### `build-agent` — PASS
- **build_agent** (3ms): agent_type=Agent tool_registry=yes

#### `restore-session-index` — PASS
- **restore_session** (0ms): path=/Users/binle/workspace_genai/co-cli/.co-cli/sessions/2026-04-14-T220208Z-cd139072.jsonl state=new
- **init_session_index** (4ms): session_index=ready search_results=0

#### `banner-boundary` — PASS
- **display_welcome_banner** (11ms): chars=960 missing=0

### Startup Status Timeline

- `  Reranker degraded — TEI cross-encoder unavailable; search results will be unranked`
- `  Knowledge degraded — embedder unavailable (not reachable — [Errno 61] Connection refused); using fts5`
- `  Knowledge synced — 0 item(s) (fts5)`
- `  Session new — cd139072...`

### Bootstrap Logs

- `INFO [co_cli.bootstrap.core] Ollama runtime num_ctx=131072 differs from config llm.num_ctx=262144 — using runtime value`
- `WARNING [co_cli.bootstrap.core] TEI cross-encoder unavailable; degrading to none`
- `WARNING [co_cli.bootstrap.core] Hybrid skipped: embedder unavailable — not reachable — [Errno 61] Connection refused`

---

## Run: 2026-04-14 22:01:42 UTC

**Model:** ollama-openai / qwen3.5:35b-a3b-think  
**Bootstrap timeout:** 30s for `create_deps()`  
**Workspace:** /Users/binle/workspace_genai/co-cli  
**Total runtime:** 1202ms  
**Result:** 4/4 passed  
**Knowledge degraded:** yes

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `create-deps` | PASS | 1171ms |
| `build-agent` | PASS | 3ms |
| `restore-session-index` | PASS | 12ms |
| `banner-boundary` | PASS | 10ms |

### Case Details

#### `create-deps` — PASS
- **create_deps** (1171ms): backend=fts5 tools=28 skills=1
- **registry_state** (0ms): tool_registry=yes mcp_tools=2 model_skills=1 completer_words=17

#### `build-agent` — PASS
- **build_agent** (3ms): system_prompts=0

#### `restore-session-index` — PASS
- **restore_session** (0ms): path=/Users/binle/workspace_genai/co-cli/.co-cli/sessions/2026-04-14-T220142Z-90901cfa.jsonl state=new
- **init_session_index** (12ms): session_index=ready search_results=0

#### `banner-boundary` — PASS
- **display_welcome_banner** (10ms): chars=960 missing=0

### Startup Status Timeline

- `  Reranker degraded — TEI cross-encoder unavailable; search results will be unranked`
- `  Knowledge degraded — embedder unavailable (not reachable — [Errno 61] Connection refused); using fts5`
- `  Knowledge synced — 0 item(s) (fts5)`
- `  Session new — 90901cfa...`

### Bootstrap Logs

- `INFO [co_cli.bootstrap.core] Ollama runtime num_ctx=131072 differs from config llm.num_ctx=262144 — using runtime value`
- `WARNING [co_cli.bootstrap.core] TEI cross-encoder unavailable; degrading to none`
- `WARNING [co_cli.bootstrap.core] Hybrid skipped: embedder unavailable — not reachable — [Errno 61] Connection refused`
