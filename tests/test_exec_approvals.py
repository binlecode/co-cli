"""Functional tests for the exec approvals persistence module."""

import json
import stat
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from co_cli._exec_approvals import (
    load_approvals,
    save_approvals,
    derive_pattern,
    find_approved,
    add_approval,
    update_last_used,
    prune_stale,
)


# -- load / save ----------------------------------------------------------------


def test_load_missing_returns_empty(tmp_path):
    """load_approvals returns [] when file does not exist."""
    result = load_approvals(tmp_path / "nonexistent.json")
    assert result == []


def test_save_creates_file(tmp_path):
    """save_approvals creates the file with correct content."""
    path = tmp_path / "approvals.json"
    entries = [{"id": "abc123", "pattern": "ls *"}]
    save_approvals(path, entries)
    assert path.exists()
    loaded = json.loads(path.read_text())
    assert loaded == entries
    assert (tmp_path / "approvals.json").stat().st_mode & 0o777 == 0o600


# -- derive_pattern -------------------------------------------------------------


def test_derive_pattern_single_word():
    """Single-word command: 'ls' → 'ls *'."""
    assert derive_pattern("ls") == "ls *"


def test_derive_pattern_stops_at_flag():
    """Stops collecting at first flag token."""
    assert derive_pattern("grep -r foo") == "grep *"


def test_derive_pattern_two_subcommands():
    """Two consecutive non-flag tokens collected."""
    assert derive_pattern("git status --short") == "git status *"


def test_derive_pattern_three_token_limit():
    """At most 3 non-flag tokens collected."""
    result = derive_pattern("git diff HEAD~1 -- src/")
    # git, diff, HEAD~1 (3 tokens) → stops
    assert result == "git diff HEAD~1 *"


# -- find_approved -------------------------------------------------------------


def test_find_approved_matches():
    """find_approved returns entry when pattern matches."""
    entries = [{"id": "abc", "pattern": "ls *", "tool_name": "run_shell_command"}]
    result = find_approved("ls -la", entries)
    assert result is not None
    assert result["id"] == "abc"


def test_find_approved_no_match():
    """find_approved returns None when no pattern matches."""
    entries = [{"id": "abc", "pattern": "grep *", "tool_name": "run_shell_command"}]
    result = find_approved("ls -la", entries)
    assert result is None


def test_find_approved_skips_bare_wildcard():
    """find_approved skips entries with pattern == '*'."""
    entries = [{"id": "abc", "pattern": "*", "tool_name": "run_shell_command"}]
    result = find_approved("anything here", entries)
    assert result is None


# -- add_approval round-trip ---------------------------------------------------


def test_add_approval_round_trip(tmp_path):
    """add_approval persists entry; find_approved retrieves it."""
    path = tmp_path / ".co-cli" / "exec-approvals.json"
    add_approval(path, "ls -la", "run_shell_command")

    entries = load_approvals(path)
    assert len(entries) == 1
    assert entries[0]["pattern"] == "ls *"
    assert entries[0]["tool_name"] == "run_shell_command"

    # Simulate restart — find_approved works from persisted data
    found = find_approved("ls -la /tmp", entries)
    assert found is not None


def test_add_approval_sets_mode_600(tmp_path):
    """add_approval writes file with mode 0o600."""
    path = tmp_path / "exec-approvals.json"
    add_approval(path, "cat file.txt", "run_shell_command")
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


# -- update_last_used ----------------------------------------------------------


def test_update_last_used(tmp_path):
    """update_last_used updates the timestamp of the matching entry."""
    path = tmp_path / "approvals.json"
    old_time = "2020-01-01T00:00:00+00:00"
    entries = [{"id": "abc", "pattern": "ls *", "last_used_at": old_time}]
    save_approvals(path, entries)

    update_last_used(path, "abc")

    loaded = load_approvals(path)
    assert loaded[0]["last_used_at"] != old_time
    # Should be close to now
    ts = datetime.fromisoformat(loaded[0]["last_used_at"].replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - ts).total_seconds()) < 5


# -- prune_stale ---------------------------------------------------------------


def test_prune_stale_removes_old(tmp_path):
    """prune_stale removes entries older than max_age_days."""
    path = tmp_path / "approvals.json"
    old_time = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    fresh_time = datetime.now(timezone.utc).isoformat()
    entries = [
        {"id": "old", "pattern": "grep *", "last_used_at": old_time},
        {"id": "fresh", "pattern": "ls *", "last_used_at": fresh_time},
    ]
    save_approvals(path, entries)

    prune_stale(path, max_age_days=90)

    remaining = load_approvals(path)
    assert len(remaining) == 1
    assert remaining[0]["id"] == "fresh"


