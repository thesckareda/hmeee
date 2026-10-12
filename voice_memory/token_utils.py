"""Legacy token helpers — delegate to zero-API token_counter."""
from __future__ import annotations

from token_counter import count_tokens


def estimate_tokens(text: str, lang_preset: str = "hme") -> int:
    return count_tokens(text, lang_preset)


def estimate_message_tokens(messages: list[str], lang_preset: str = "hme") -> int:
    return sum(estimate_tokens(m, lang_preset) for m in messages)