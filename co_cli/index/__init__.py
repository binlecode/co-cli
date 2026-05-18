"""Index — infrastructure facade over SQLite (FTS5 + sqlite-vec).

Public surface is `IndexStore` (write/search facade) and `Chunk` (write contract).
Retrieval, embedding, and provider dispatch are private submodules.
"""
