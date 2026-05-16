"""UAT eval — Workflow 3: Memory recall and curation.

Covers `knowledge_search`, `session_search`, `knowledge_view`, `knowledge_manage`
via agent calls, plus the `/memory list|count|stats|forget|dream|decay-review|
restore` user surface. Validates BM25/hybrid recall, write-time indexing, and
dream-cycle merge/decay/archive.

Specs: docs/specs/memory.md, knowledge.md, dream.md
"""
