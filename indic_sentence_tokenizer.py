"""
Indic-aware sentence tokenizer for streaming TTS.

Buffers LLM tokens until safe clause/sentence boundaries so Telugu, Hindi,
Tamil and other Indic scripts are never sent to TTS mid-grapheme.
Compatible with LiveKit's tokenize.SentenceTokenizer interface.
"""
from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass

import regex

from livekit.agents.tokenize import token_stream, tokenizer
from livekit.agents.utils import shortuuid

__all__ = [
    "IndicBufferedSentenceStream",
    "IndicSentenceTokenizer",
    "grapheme_count",
    "is_tts_ready_chunk",
    "is_tts_ready_eoi",
    "pop_speakable_tts_chunk",
    "prepare_tts_segment_flush",
    "split_indic_chunks",
    "MIN_CHUNK_GRAPHEMES",
    "MIN_CHUNK_GRAPHEMES_CLAUSE",
    "MAX_CHUNK_GRAPHEMES",
]

INDIC_SCRIPT = re.compile(r"[\u0900-\u0D7F]")

# Sentence and clause boundaries — optional whitespace; also split when punct
# is immediately followed by an Indic letter (common in Telugu/Hindi).
CLAUSE_BOUNDARY = regex.compile(
    r"(?<=[।.!?])\s*|(?<=[,;:—])\s+|(?<=[।.!?,;:—])(?=[\u0900-\u0D7F])"
)

MIN_CHUNK_GRAPHEMES = 12
_BASE_MIN_CHUNK_GRAPHEMES_CLAUSE = 20
_AGGRESSIVE_CHUNKING = os.environ.get("LIVEKIT_TTS_AGGRESSIVE_CHUNKING", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Phase 1: ~20% lower clause threshold; aggressive mode (env) → 12 for faster first audio on HME/Telugu.
MIN_CHUNK_GRAPHEMES_CLAUSE = 12 if _AGGRESSIVE_CHUNKING else 16
MIN_SENTENCE_GRAPHEMES = 3
MAX_CHUNK_GRAPHEMES = 150
STREAM_CONTEXT_GRAPHEMES = 4


def grapheme_count(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return len(regex.findall(r"\X", stripped))


def _chunk_threshold(text: str, min_sentence_len: int) -> int:
    stripped = text.rstrip()
    if regex.search(r"[।.!?]\s*$", stripped):
        return min_sentence_len
    if regex.search(r"[,;:—]\s*$", stripped):
        return MIN_CHUNK_GRAPHEMES_CLAUSE
    return MAX_CHUNK_GRAPHEMES


def _is_filler_sentence(stripped: str) -> bool:
    try:
        from tts_text_transform import is_filler_only
    except ImportError:
        return False
    return is_filler_only(stripped)


def is_tts_ready_chunk(text: str, *, end_of_input: bool = False) -> bool:
    """Return True when *text* is safe to send to Sarvam streaming TTS."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    gc = grapheme_count(stripped)
    if gc < MIN_SENTENCE_GRAPHEMES:
        return False
    if regex.search(r"[.!?।]\s*$", stripped):
        if _is_filler_sentence(stripped):
            return False
        return gc >= MIN_CHUNK_GRAPHEMES
    if regex.search(r"[,;:—]\s*$", stripped) and gc >= MIN_CHUNK_GRAPHEMES_CLAUSE:
        return True
    if end_of_input:
        if regex.search(r"[,;:—]\s*$", stripped) and gc < MIN_CHUNK_GRAPHEMES_CLAUSE:
            return False
        if regex.search(r"[.!?।]\s*$", stripped):
            if _is_filler_sentence(stripped):
                return False
            return gc >= MIN_CHUNK_GRAPHEMES
        return gc >= 40
    # Avoid WS segments like "Could you please" with no audio response.
    return gc >= 32


def is_tts_ready_eoi(text: str, *, padded_text: str | None = None) -> bool:
    """True when a stream-end remainder is safe to flush to Sarvam TTS."""
    if is_tts_ready_chunk(text, end_of_input=True):
        return True
    if padded_text and padded_text.strip() != (text or "").strip():
        padded = padded_text.strip()
        if grapheme_count(padded) >= max(MIN_SENTENCE_GRAPHEMES, 10):
            return True
        return is_tts_ready_chunk(padded, end_of_input=True)
    return False


def pop_speakable_tts_chunk(hold: str) -> tuple[str, str]:
    """Pop one mid-stream speakable prefix from *hold*, if any."""
    if not hold:
        return "", ""

    if is_tts_ready_chunk(hold):
        return hold, ""

    chunks = split_indic_chunks(hold)
    if not chunks:
        return "", hold

    first_text, _start, end = chunks[0]
    if not first_text or not is_tts_ready_chunk(first_text):
        return "", hold

    if end >= len(hold):
        return first_text, ""

    return first_text, hold[end:].lstrip()


def prepare_tts_segment_flush(
    hold: str,
    *,
    padded_text: str | None = None,
) -> tuple[str, str]:
    """At a TTS segment boundary, return speakable text and any leftover hold."""
    stripped = (hold or "").strip()
    if not stripped:
        return "", hold or ""

    if is_tts_ready_chunk(stripped):
        return stripped, ""

    if is_tts_ready_chunk(stripped, end_of_input=True):
        return stripped, ""

    if is_tts_ready_eoi(stripped, padded_text=padded_text):
        return stripped, ""

    return "", hold


def _is_complete_chunk(text: str) -> bool:
    """Return True if *text* is a complete utterance worth speaking.

    Drops trailing fragments that were cut mid-word by the LLM hitting
    its max_tokens budget — no audience should ever hear those.
    """
    stripped = text.strip()
    if not stripped:
        return False
    gc = grapheme_count(stripped)
    if regex.search(r"[.!?।]\s*$", stripped):
        return gc >= MIN_SENTENCE_GRAPHEMES
    if regex.search(r"[,;:—]\s*$", stripped):
        return gc >= MIN_CHUNK_GRAPHEMES_CLAUSE
    return False


def split_indic_chunks(
    text: str,
    min_sentence_len: int = MIN_CHUNK_GRAPHEMES,
    retain_format: bool = False,
) -> list[tuple[str, int, int]]:
    """Split *text* into TTS-safe chunks at sentence/clause boundaries."""
    if not text:
        return []

    results: list[tuple[str, int, int]] = []
    start = 0
    search_from = 0

    while search_from < len(text):
        match = CLAUSE_BOUNDARY.search(text, search_from)
        if not match:
            break

        end = match.end()
        chunk = text[start:end]
        display = chunk if retain_format else chunk.strip()
        if not display:
            search_from = end
            continue

        threshold = _chunk_threshold(display, min_sentence_len)
        if grapheme_count(display) >= threshold:
            results.append((display, start, end))
            start = end

        search_from = end

    if start < len(text):
        remainder = text[start:]
        display = remainder if retain_format else remainder.strip()
        if display and _is_complete_chunk(display):
            results.append((display, start, len(text)))

    return results


@dataclass
class _TokenizerOptions:
    min_sentence_len: int
    stream_context_len: int
    retain_format: bool


class IndicBufferedSentenceStream(token_stream.BufferedSentenceStream):
    """BufferedSentenceStream that never flushes sub-minimum fragments on segment flush."""

    def _combined_buffer(self) -> str:
        if self._out_buf and self._in_buf:
            return f"{self._out_buf} {self._in_buf}".strip()
        return (self._out_buf or self._in_buf).strip()

    def flush(self) -> None:
        self._check_not_closed()
        combined = self._combined_buffer()
        if combined and is_tts_ready_chunk(combined):
            self._event_ch.send_nowait(
                tokenizer.TokenData(token=combined, segment_id=self._current_segment_id)
            )
            self._in_buf = ""
            self._out_buf = ""
        elif combined:
            self._in_buf = combined
            self._out_buf = ""
        else:
            self._in_buf = ""
            self._out_buf = ""

        self._current_segment_id = shortuuid()

    def end_input(self) -> None:
        self.flush()
        self._event_ch.close()


class IndicSentenceTokenizer(tokenizer.SentenceTokenizer):
    """LiveKit-compatible tokenizer tuned for Indic streaming TTS."""

    def __init__(
        self,
        *,
        min_sentence_len: int = MIN_CHUNK_GRAPHEMES,
        stream_context_len: int = STREAM_CONTEXT_GRAPHEMES,
        retain_format: bool = False,
    ) -> None:
        self._config = _TokenizerOptions(
            min_sentence_len=min_sentence_len,
            stream_context_len=stream_context_len,
            retain_format=retain_format,
        )

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        return [
            tok[0]
            for tok in split_indic_chunks(
                text,
                min_sentence_len=self._config.min_sentence_len,
                retain_format=self._config.retain_format,
            )
        ]

    def stream(self, *, language: str | None = None) -> tokenizer.SentenceStream:
        return IndicBufferedSentenceStream(
            tokenizer=functools.partial(
                split_indic_chunks,
                min_sentence_len=self._config.min_sentence_len,
                retain_format=self._config.retain_format,
            ),
            min_token_len=self._config.min_sentence_len,
            min_ctx_len=self._config.stream_context_len,
        )
