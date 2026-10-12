"""Tests for per-phone caller history."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


@pytest.fixture
def memory_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_MEMORY_DIR", str(tmp_path))
    import re_voice_memory.constants as constants

    monkeypatch.setattr(constants, "MEMORY_DIR", tmp_path)
    import re_voice_memory.caller_history as ch

    monkeypatch.setattr(ch, "MEMORY_DIR", tmp_path)
    return tmp_path


def test_append_and_load_history(memory_dir):
    from re_voice_memory.caller_history import (
        append_call_history,
        format_history_for_injection,
        load_caller_history,
    )

    phone = "+916300632510"
    append_call_history(
        phone,
        caller_name="Ravi",
        summary="Discussed 3BHK in Bandra, budget 2 Cr.",
        call_at="2026-07-02T12:00:00+05:30",
    )
    entries = load_caller_history(phone)
    assert len(entries) == 1
    assert "Bandra" in entries[0].summary

    block = format_history_for_injection(phone, "Ravi", entries)
    assert "PRIOR CALL HISTORY" in block
    assert "Bandra" in block


def test_invalid_phone_skipped(memory_dir):
    from re_voice_memory.caller_history import append_call_history, load_caller_history

    append_call_history(
        "unknown",
        caller_name="X",
        summary="Should not save",
        call_at="2026-07-02T12:00:00+05:30",
    )
    assert load_caller_history("unknown") == []
    assert load_caller_history("demo") == []


def test_history_cap(memory_dir):
    from re_voice_memory.caller_history import append_call_history, load_caller_history

    phone = "+919999999999"
    for i in range(15):
        append_call_history(
            phone,
            caller_name="Test",
            summary=f"Call number {i} summary text.",
            call_at=f"2026-07-02T12:{i:02d}:00+05:30",
        )
    entries = load_caller_history(phone, limit=3)
    assert len(entries) == 3