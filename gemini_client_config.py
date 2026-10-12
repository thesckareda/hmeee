"""Shared google-genai Automatic Function Calling (AFC) settings."""
from __future__ import annotations

import os

_DEFAULT_MAX_REMOTE_CALLS = 20


def gemini_afc_max_remote_calls() -> int:
    raw = os.environ.get("GEMINI_AFC_MAX_REMOTE_CALLS", str(_DEFAULT_MAX_REMOTE_CALLS))
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_REMOTE_CALLS
    return max(1, value)


def gemini_afc_for_tools():
    from google.genai import types

    return types.AutomaticFunctionCallingConfig(
        maximum_remote_calls=gemini_afc_max_remote_calls(),
    )


def gemini_afc_disabled():
    from google.genai import types

    return types.AutomaticFunctionCallingConfig(disable=True)