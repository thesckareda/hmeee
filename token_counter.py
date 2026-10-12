"""
Zero-API token counter. Calibrated per-language multipliers derived from
live Gemini inference metadata (prompt_token counts from response usage).
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Populated at runtime from live inference logs; defaults from empirical sampling.
_CALIBRATED_MULT: dict[str, float] = {
    "hme": 0.28,
    "hinglish": 0.28,
    "hi": 0.28,
    "marathi": 0.26,
    "mr": 0.26,
    "telugish": 0.27,
    "tinglish": 0.27,
    "telugu": 0.27,
    "english": 0.25,
    "en": 0.25,
    "en-in": 0.25,
    "mixed": 0.27,
}

_token_cache: dict[str, int] = {}
_CACHE_MAX = 8192
_cache_hits = 0
_cache_misses = 0


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def set_calibrated_multiplier(lang_preset: str, multiplier: float) -> None:
    """Call from telemetry ingestion to update calibration."""
    key = (lang_preset or "mixed").lower()
    _CALIBRATED_MULT[key] = multiplier
    logger.info("Token multiplier updated for %s: %.4f", key, multiplier)


def get_calibrated_multiplier(lang_preset: str = "hme") -> float:
    preset = (lang_preset or "mixed").lower()
    return _CALIBRATED_MULT.get(preset, _CALIBRATED_MULT["mixed"])


def count_tokens(text: str, lang_preset: str = "hme") -> int:
    """Single source of truth. Zero API calls. Pure local math + cache."""
    global _cache_hits, _cache_misses

    if not text:
        return 0

    key = _cache_key(text)
    if key in _token_cache:
        _cache_hits += 1
        return _token_cache[key]

    _cache_misses += 1
    mult = get_calibrated_multiplier(lang_preset)
    count = max(1, int(len(text) * mult))

    if len(_token_cache) >= _CACHE_MAX:
        _token_cache.pop(next(iter(_token_cache)))
    _token_cache[key] = count
    return count


def count_tokens_batch(texts: list[str], lang_preset: str = "hme") -> list[int]:
    return [count_tokens(t, lang_preset) for t in texts]


def invalidate(text: str) -> None:
    _token_cache.pop(_cache_key(text), None)


def get_stats() -> dict[str, Any]:
    total = _cache_hits + _cache_misses
    hit_rate = (_cache_hits / total) if total else 0.0
    return {
        "cache_size": len(_token_cache),
        "cache_max": _CACHE_MAX,
        "cache_hits": _cache_hits,
        "cache_misses": _cache_misses,
        "hit_rate": round(hit_rate, 4),
        "multipliers": dict(_CALIBRATED_MULT),
    }


def _usage_prompt_tokens(usage_metadata: Any) -> int | None:
    if usage_metadata is None:
        return None
    if isinstance(usage_metadata, dict):
        raw = usage_metadata.get("prompt_tokens") or usage_metadata.get("prompt_token_count")
        return int(raw) if raw else None
    for attr in ("prompt_tokens", "prompt_token_count"):
        raw = getattr(usage_metadata, attr, None)
        if raw:
            return int(raw)
    return None


def ingest_calibration(text: str, lang_preset: str, usage_metadata: Any) -> None:
    """Update multipliers from live inference usage (fire-and-forget safe)."""
    if not text or not usage_metadata:
        return
    actual_input_tokens = _usage_prompt_tokens(usage_metadata)
    if not actual_input_tokens or len(text) <= 20:
        return
    observed_mult = actual_input_tokens / len(text)
    current = get_calibrated_multiplier(lang_preset)
    updated = (current * 0.9) + (observed_mult * 0.1)
    set_calibrated_multiplier(lang_preset, updated)


def truncate_at_boundary(text: str, max_tokens: int, lang_preset: str = "hme") -> str:
    if not text:
        return text
    if count_tokens(text, lang_preset) <= max_tokens:
        return text

    parts = re.split(r"(?<=[.!?।])\s+", text)
    truncated = ""
    for part in parts:
        if not part.strip():
            continue
        candidate = (truncated + part + " ").strip() + " "
        if count_tokens(candidate, lang_preset) > max_tokens:
            break
        truncated = candidate

    if not truncated.strip():
        result_words: list[str] = []
        for word in text.split():
            candidate = " ".join(result_words + [word])
            if count_tokens(candidate, lang_preset) > max_tokens - 1:
                break
            result_words.append(word)
        truncated = " ".join(result_words)

    return truncated.strip()


# Backward-compatible aliases used across the codebase.
count_tokens_sync = count_tokens
truncate_at_boundary_sync = truncate_at_boundary
truncate_to_token_budget_sync = truncate_at_boundary
invalidate_cache_for = invalidate
get_cache_stats = get_stats


async def count_tokens_async(text: str, lang_preset: str = "hme", **_kwargs: Any) -> int:
    return count_tokens(text, lang_preset)


async def truncate_at_boundary_async(
    text: str,
    max_tokens: int,
    lang_preset: str = "hme",
) -> str:
    return truncate_at_boundary(text, max_tokens, lang_preset)


def count_tokens_batch_sync(texts: list[str], lang_preset: str = "hme") -> list[int]:
    return count_tokens_batch(texts, lang_preset)