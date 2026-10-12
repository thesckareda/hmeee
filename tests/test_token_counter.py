"""Tests for zero-API token_counter module."""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from token_counter import (
    count_tokens,
    get_calibrated_multiplier,
    get_stats,
    ingest_calibration,
    invalidate,
    set_calibrated_multiplier,
    truncate_at_boundary,
)


def test_count_tokens_reasonable_range():
    text = "Hello world" * 100
    count = count_tokens(text, "english")
    assert 200 < count < 350


def test_cache_hit():
    text = "Cached text for testing"
    count_tokens(text, "hme")
    before = get_stats()["cache_hits"]
    count_tokens(text, "hme")
    after = get_stats()["cache_hits"]
    assert after > before


def test_empty_string():
    assert count_tokens("") == 0


def test_hinglish_code_switching():
    text = "Budget ₹2 crore hai, but location Bandra ya Andheri dono chalega."
    est = count_tokens(text, "hme")
    assert est > 10


def test_truncate_at_boundary():
    long_text = "Short sentence. " * 200
    truncated = truncate_at_boundary(long_text, 50, "hme")
    assert count_tokens(truncated, "hme") <= 50


def test_invalidate_cache():
    text = "Invalidate me"
    count_tokens(text, "hme")
    invalidate(text)
    before = get_stats()["cache_misses"]
    count_tokens(text, "hme")
    after = get_stats()["cache_misses"]
    assert after >= before


def test_ingest_calibration_ema():
    before = get_calibrated_multiplier("hme")
    ingest_calibration(
        "A" * 100,
        "hme",
        {"prompt_tokens": 50},
    )
    after = get_calibrated_multiplier("hme")
    assert after != before
    assert 0.1 < after < 1.0


def test_ingest_calibration_skips_short_text():
    before = get_calibrated_multiplier("english")
    ingest_calibration("short", "english", {"prompt_tokens": 100})
    after = get_calibrated_multiplier("english")
    assert after == before


def test_set_calibrated_multiplier():
    set_calibrated_multiplier("test_preset", 0.31)
    assert get_calibrated_multiplier("test_preset") == 0.31