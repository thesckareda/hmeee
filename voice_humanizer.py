"""
Voice humanizer — micro-pauses and optional breath markers for natural TTS pacing.

Inserts lightweight pause markers between long consecutive sentences so synthesis
sounds less rushed. Cartesia accepts ``<break time="Nms"/>``; Sarvam/Gemini paths
receive ellipsis spacing that still reads naturally.
"""
from __future__ import annotations

import random
import re
import regex

_PAUSE_MARKER = "[[pause]]"
_BREAK_TAG_RE = re.compile(r"<break\s+time=\"\d+ms\"\s*/>", re.I)
_SENTENCE_SPLIT = regex.compile(r"(?<=[.!?।])\s+", regex.UNICODE)

# Words in a single sentence before we consider inserting a micro-pause after it.
_LONG_SENTENCE_WORDS = 14
# Combined word count across consecutive sentences before a mandatory pause.
_COMBINED_WORDS_BEFORE_PAUSE = 22

_BREATH_PROBABILITY = 0.28


def pause_marker_for_provider(tts_provider: str) -> str:
    """Provider-specific pause token sent to the TTS engine."""
    provider = (tts_provider or "").strip().lower()
    if provider == "cartesia":
        return '<break time="280ms"/>'
    return "… "


def strip_pause_markers(text: str) -> str:
    """Remove humanizer markers — useful for logs and chat context."""
    cleaned = (text or "").replace(_PAUSE_MARKER, " ")
    cleaned = _BREAK_TAG_RE.sub(" ", cleaned)
    cleaned = regex.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _word_count(sentence: str) -> int:
    return len((sentence or "").split())


def insert_micro_pauses(
    text: str,
    *,
    min_words_between_pauses: int = _COMBINED_WORDS_BEFORE_PAUSE,
    long_sentence_words: int = _LONG_SENTENCE_WORDS,
    tts_provider: str = "cartesia",
) -> str:
    """
    Insert micro-pause markers between long sentences.

    Rules:
    - After any sentence longer than ``long_sentence_words``, add a pause.
    - After a run of sentences whose combined word count exceeds
      ``min_words_between_pauses``, add a pause.
    """
    stripped = (text or "").strip()
    if not stripped:
        return ""

    marker = pause_marker_for_provider(tts_provider)
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(stripped) if s.strip()]
    if len(sentences) <= 1:
        return stripped

    out: list[str] = []
    run_words = 0
    for idx, sentence in enumerate(sentences):
        wc = _word_count(sentence)
        run_words += wc
        out.append(sentence)

        is_last = idx == len(sentences) - 1
        if is_last:
            continue

        needs_pause = wc >= long_sentence_words or run_words >= min_words_between_pauses
        if needs_pause:
            out.append(marker)
            run_words = 0

    return " ".join(out)


def inject_breath_pause(
    text: str,
    *,
    probability: float = _BREATH_PROBABILITY,
    tts_provider: str = "cartesia",
    rng: random.Random | None = None,
) -> str:
    """Optionally prepend a short breath pause before a long reply."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    if _word_count(stripped) < 20:
        return stripped

    roll = (rng or random).random()
    if roll >= probability:
        return stripped

    marker = pause_marker_for_provider(tts_provider)
    if tts_provider.strip().lower() == "cartesia":
        return f"{marker} {stripped}"
    return f"{marker}{stripped}"


def humanize_for_tts(
    text: str,
    *,
    enable_pauses: bool = True,
    enable_breath: bool = False,
    tts_provider: str = "cartesia",
    rng: random.Random | None = None,
) -> str:
    """Main entry — apply pause/breath humanization before synthesis."""
    result = (text or "").strip()
    if not result:
        return ""

    if enable_pauses:
        result = insert_micro_pauses(result, tts_provider=tts_provider)
    if enable_breath:
        result = inject_breath_pause(result, tts_provider=tts_provider, rng=rng)
    return result


def humanize_enabled_for_config(live_config: dict | None) -> tuple[bool, bool]:
    """Read pause/breath toggles from agent config (defaults: pauses on, breath off)."""
    cfg = live_config or {}
    pauses = bool(cfg.get("tts_micro_pauses", True))
    breath = bool(cfg.get("tts_breath_pauses", False))
    return pauses, breath