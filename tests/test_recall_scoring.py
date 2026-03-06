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


def test_decay_old_memory_approaches_zero():
    """Very old memory returns a low multiplier."""
    created = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    result = _decay_multiplier(created, half_life_days=30)
    assert result < 0.05


def test_decay_future_dated_returns_one():
    """Future-dated timestamp returns 1.0 (clock skew guard)."""
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    result = _decay_multiplier(future, half_life_days=30)
    assert result == 1.0


def test_decay_clamped_to_zero_one():
    """Result is always in [0, 1]."""
    now_iso = datetime.now(timezone.utc).isoformat()
    assert 0.0 <= _decay_multiplier(now_iso, half_life_days=1) <= 1.0
    old = (datetime.now(timezone.utc) - timedelta(days=1000)).isoformat()
    assert 0.0 <= _decay_multiplier(old, half_life_days=1) <= 1.0


def test_decay_invalid_timestamp_returns_one():
    """Malformed timestamp falls back to 1.0 (no penalty)."""
    result = _decay_multiplier("not-a-timestamp", half_life_days=30)
    assert result == 1.0


def test_decay_z_suffix_iso_parsed():
    """ISO8601 with 'Z' suffix is parsed correctly."""
    created = "2020-01-01T00:00:00Z"
    result = _decay_multiplier(created, half_life_days=30)
    # Should be near 0 since 2020 is long ago
    assert result < 0.01


def test_decay_exponential_property():
    """Decay follows 2^(-age/half_life) exponential curve."""
    half_life = 14
    for age in [7, 14, 28]:
        created = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
        result = _decay_multiplier(created, half_life_days=half_life)
        expected = math.exp(-math.log(2) * age / half_life)
        assert abs(result - expected) < 0.02
