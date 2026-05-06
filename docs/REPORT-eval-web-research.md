# Eval Report: Web Research

## Run: 2026-05-03 14:12:56 UTC

**Model:** ollama / qwen3.5:35b-a3b-agentic  
**Total runtime:** 44201ms  
**Result:** 3/3 passed/skipped

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `web_fetch_executes` | PASS | 11435ms |
| `web_search_executes` | PASS | 18936ms |
| `web_fetch_second_domain` | PASS | 13830ms |

### Step Traces

#### `web_fetch_executes` — PASS
- **run_turn** (8582ms): outcome=continue
- **response_analysis** (0ms): tools_called=['web_fetch'] fetch_called=True preview='The page states that **example.com** is designated for use in documentation examples without needing permission, and it explicitly advises avoiding use in operations.\n\nThis is an IANA-reserved domain specifically created for illustrative purposes in '
- **judge** (2849ms): SKIP: w1 — judge call failed: 'AgentRunResult' object has no attribute 'data'

#### `web_search_executes` — PASS
- **run_turn** (18928ms): outcome=continue
- **response_analysis** (0ms): tools_called=['web_search'] search_called=True python_mentioned=True preview='**URL:** https://www.python.org/\n\n**Fact:** Python.org serves as the official home of the Python Programming Language and includes sections for beginners (getting started), the Python Software Foundation, and general about pages.'

#### `web_fetch_second_domain` — PASS
- **run_turn** (10810ms): outcome=continue
- **response_analysis** (0ms): tools_called=['web_fetch'] fetch_called=True preview='The page is managed by **IANA (Internet Assigned Numbers Authority)**, with the IANA functions coordinated by **Public Technical Identifiers (PTI)**, an affiliate of **ICANN (Internet Corporation for Assigned Names and Numbers)**.'
- **judge** (3014ms): SKIP: w3 — judge call failed: 'AgentRunResult' object has no attribute 'data'

---

## Run: 2026-05-03 14:11:38 UTC

**Model:** ollama / qwen3.5:35b-a3b-agentic  
**Total runtime:** 40533ms  
**Result:** 1/3 passed/skipped

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `web_fetch_executes` | ERROR | 0ms |
| `web_search_executes` | PASS | 9603ms |
| `web_fetch_second_domain` | ERROR | 0ms |

### Step Traces

#### `web_fetch_executes` — ERROR
- **Failure/Note:** UserError: Unknown keyword arguments: `result_type`

#### `web_search_executes` — PASS
- **run_turn** (9597ms): outcome=continue
- **response_analysis** (0ms): tools_called=['web_search'] search_called=True python_mentioned=True preview='URL: https://www.python.org/\n\nFact: The website shows Python 3.14 is currently available (noted as available for Android download).'

#### `web_fetch_second_domain` — ERROR
- **Failure/Note:** UserError: Unknown keyword arguments: `result_type`

---

## Run: 2026-05-03 02:59:22 UTC

**Model:** ollama / qwen3.5:35b-a3b-agentic  
**Total runtime:** 31078ms  
**Result:** 3/3 passed/skipped

### Summary

| Case | Verdict | Duration |
|------|---------|----------|
| `web_fetch_executes` | PASS | 8316ms |
| `web_search_executes` | PASS | 11716ms |
| `web_fetch_second_domain` | PASS | 11045ms |

### Step Traces

#### `web_fetch_executes` — PASS
- **run_turn** (8313ms): outcome=continue
- **response_analysis** (0ms): tools_called=['web_fetch'] fetch_called=True keyword_present=True preview='The page states: **This domain is for use in documentation examples without needing permission. Avoid use in operations.**\n\nIt links to IANA for more details on its reserved status as an example domain.'

#### `web_search_executes` — PASS
- **run_turn** (11711ms): outcome=continue
- **response_analysis** (0ms): tools_called=['web_search'] search_called=True python_mentioned=True preview='**URL:** https://www.python.org/\n\n**Fact:** Python 3.10 is the version referenced in the results (as of November 2023), and Python is a multi-paradigm programming language with release support lasting two years of full support followed by three years'

#### `web_fetch_second_domain` — PASS
- **run_turn** (11039ms): outcome=continue
- **response_analysis** (0ms): tools_called=['web_fetch'] fetch_called=True keyword_present=True preview='The page is managed by **ICANN (Internet Corporation for Assigned Names and Numbers)** through its affiliate **Public Technical Identifiers (PTI)**, which performs the IANA functions.\n\nThis is stated in the page footer: "The IANA functions coordinate'

---
