# Eval Report: Bootstrap Flow Quality

## Run: 2026-05-02 18:49:08 UTC

**Model:** ollama / qwen3.5:35b-a3b-agentic  
**Bootstrap timeout:** 30s for `create_deps()`  
**Workspace:** /Users/binle/workspace_genai/co-cli  
**Total runtime:** 2312ms  
**Result:** 4/4 passed  
**Knowledge degraded:** no

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `create-deps` | PASS | 1188ms |
| `degradation-signals` | PASS | 0ms |
| `restore-session-index` | PASS | 1095ms |
| `banner-boundary` | PASS | 19ms |

### Case Details

#### `create-deps` — PASS
- **create_deps** (1188ms): backend=hybrid tools=25 mcp_tools=2 skills=1 degradations=0
- **registry_state** (0ms): tool_registry=yes mcp_tools=2 model_skills=1 completer_words=1
- **mcp_state** (0ms): configured=1 connected=1 failed=0 tools=2

#### `degradation-signals` — PASS
- **knowledge_signal** (0ms): degraded=False entry='none'
- **mcp_signals** (0ms): mcp_failures=0 servers=none
- **degradation_count** (0ms): total=0 keys=none

#### `restore-session-index` — PASS
- **restore_session** (0ms): path=/Users/binle/.co-cli/sessions/2026-04-16-T235238Z-b8445d2b.jsonl state=restored
- **init_session_index** (1095ms): memory_store=ready search_results=5

#### `banner-boundary` — PASS
- **display_welcome_banner** (19ms): chars=795 missing=0

### Startup Status Timeline

- `+1188ms` `  Knowledge synced — 0 item(s) (hybrid)`
- `+1188ms` `  Session restored — b8445d2b...`

### Bootstrap Logs

- `INFO [co_cli.bootstrap.core] Ollama runtime num_ctx=65536 differs from config llm.num_ctx=0 — using runtime value`
