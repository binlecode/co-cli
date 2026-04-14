"""Prompt assembly for the Co CLI agent.

Static instructions: soul seed, character memories, mindsets, rules, examples,
and critique — assembled once at startup via build_static_instructions().
Runtime-only context layers are added per request via @agent.instructions.
"""
