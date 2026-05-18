"""Session domain — past conversation transcripts.

Stores append-only JSONL transcripts at ``~/.co-cli/sessions/`` and indexes
them under ``source='session'`` in the shared IndexStore.
"""
