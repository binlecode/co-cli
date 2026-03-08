"""Functional tests for temporal decay scoring in memory recall."""

import math
from datetime import datetime, timezone, timedelta

import pytest

from co_cli.tools.memory import _decay_multiplier


# -- _decay_multiplier unit cases ------------------------------------------


def test_decay_age_zero_returns_one():
    """Memory created right now has multiplier == 1.0."""
    now_iso = datetime.now(timezone.utc).isoformat()
    result = _decay_multiplier(now_iso, half_life_days=30)
    assert abs(result - 1.0) < 0.01


def test_decay_half_life_returns_half():
    """Memory aged exactly half_life_days returns approximately 0.5."""
    created = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    result = _decay_multiplier(created, half_life_days=30)
    assert abs(result - 0.5) < 0.01


def test_decay_future_dated_returns_one():
    """Future-dated timestamp returns 1.0 (clock skew guard)."""
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    result = _decay_multiplier(future, half_life_days=30)
    assert result == 1.0


def test_decay_invalid_timestamp_returns_one():
    """Malformed timestamp falls back to 1.0 (no penalty)."""
    result = _decay_multiplier("not-a-timestamp", half_life_days=30)
    assert result == 1.0


