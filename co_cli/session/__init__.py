"""Session domain — past conversation transcripts.

Stores append-only JSONL transcripts at ``~/.co-cli/sessions/`` and searches
them with file-based ripgrep (lexical, no index).
"""
