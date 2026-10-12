"""
TTS text transforms that strip incomplete trailing fragments before audio synthesis.

Pipecat-inspired safety net — the LLM may hit its max_tokens cap mid-sentence.
These ensure no garbled half-word reaches the caller's ear.
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterable, Callable

import regex

_SENTENCE_END = regex.compile(r"[।.!?]", regex.UNICODE)
_TRAILING_WORD_CHARS = regex.compile(r"[^\s।.!?,;:—]+$", regex.UNICODE)


async def strip_trailing_incomplete(text: AsyncIterable[str]) -> AsyncIterable[str]:
    """
    Strip any trailing text that doesn't end at a sentence/clause boundary.

    Example: "Welcome to VK Realty. We have beauti" → "Welcome to VK Realty."
    The trailing fragment "We have beauti" is dropped entirely.
    """
    async for chunk in text:
        trimmed = _trim_to_last_boundary(chunk)
        if trimmed:
            yield trimmed


import logging

logger = logging.getLogger("inbound-voice-agent")

_MIN_SINGLE_WORD_CHARS = 8
_MIN_GRAPHEMES = 12
_MIN_SPEAKABLE_WORDS = 4
_PUNCT_ONLY = re.compile(r"^[\s,.\!?;:\-–—…]+$")
_SENTENCE_END_RE = regex.compile(r"[.!?।]\s*$", regex.UNICODE)
_FILLER_PREFIX_RE = regex.compile(
    r"^(?:okay|ok|sure|right|yes|yeah|hmm|hm|uh|um|ah|oh|fine|good)\.\s+",
    regex.IGNORECASE,
)

_FILLER_WORDS = frozenset({
    "okay", "ok", "uh", "hmm", "hm", "yeah", "yes", "no", "um", "ah", "oh",
    "right", "sure", "fine", "good", "haan", "han", "theek", "accha", "ji", "ha",
})

_ALLOWED_BRIEF_ACKS = frozenset({
    "avunu andi", "sare andi", "haan ji", "haan andi", "theek hai", "accha ji",
    "okay andi", "right andi", "sure andi", "avunu", "sare", "haan ji andi",
})


def _normalize_filler_token(text: str) -> str:
    return (text or "").strip().lower().rstrip(".,!?;:")


def is_allowed_brief_ack(text: str) -> bool:
    """Indic/phone ack phrases that should reach TTS after interrupts."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    normalized = _normalize_filler_token(stripped.rstrip(".!?।, "))
    return normalized in _ALLOWED_BRIEF_ACKS


def is_filler_only(text: str) -> bool:
    """True for standalone acknowledgements like 'Right', 'Sure', 'Okay'."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if is_allowed_brief_ack(stripped):
        return False
    bare = stripped.rstrip(".!?।, ")
    if _normalize_filler_token(bare) in _FILLER_WORDS:
        return True
    return _normalize_filler_token(stripped) in _FILLER_WORDS


def is_incomplete_speakable_clause(text: str) -> bool:
    """True for stubs like 'Okay. So' that must stay buffered."""
    stripped = (text or "").strip()
    if not stripped:
        return True

    if is_allowed_brief_ack(stripped):
        return False

    words = stripped.split()
    if _FILLER_PREFIX_RE.match(stripped) and len(words) < _MIN_SPEAKABLE_WORDS:
        return True

    if is_filler_only(stripped):
        return True

    try:
        from indic_sentence_tokenizer import grapheme_count
    except ImportError:
        grapheme_count = lambda s: len(s.strip())  # noqa: E731

    gc = grapheme_count(stripped)
    if not _SENTENCE_END_RE.search(stripped):
        return gc < 40 or len(words) < _MIN_SPEAKABLE_WORDS

    if len(words) < 2 and gc < _MIN_GRAPHEMES:
        return True

    return False


# IQ #26 — prosodic question differentiation (Cartesia has no SSML: prosody is
# text shape). Rhetorical confirmation tags must render FLAT — ', kada.' — while
# genuine questions keep '?' for the rise. Bare 'na' is excluded on purpose:
# 'Saturday na, Sunday na?' is a real either/or question.
_CONFIRMATION_TAG = re.compile(
    r"[,\s]+(kada|kadha|sare\s+na|theek\s+hai\s+na|haina|hai\s+na|clear\s+na|right)\s*\?+\s*$",
    re.I,
)


def flatten_confirmation_tag(text: str) -> str:
    """Rewrite trailing rhetorical tags from '… kada?' to '…, kada.'."""
    return _CONFIRMATION_TAG.sub(
        lambda m: f", {' '.join(m.group(1).split())}.", text or ""
    )


def filter_tts_fragment(text: str) -> str:
    """Return speakable text, or \"\" to skip Sarvam synthesis for micro-fragments."""
    raw = text or ""
    stripped = raw.strip()
    if not stripped:
        if raw and raw.isspace():
            logger.warning("[TTS-SAFE] Suppressed whitespace-only fragment: %r", raw)
        return ""

    if _PUNCT_ONLY.fullmatch(stripped):
        logger.warning("[TTS-SAFE] Suppressed punctuation-only fragment: %r", raw)
        return ""

    stripped = flatten_confirmation_tag(stripped)

    if is_allowed_brief_ack(stripped):
        return stripped

    if is_filler_only(stripped):
        logger.warning("[TTS-SAFE] Suppressed filler acknowledgement: %r", raw)
        return ""

    if is_incomplete_speakable_clause(stripped):
        logger.warning("[TTS-SAFE] Suppressed incomplete clause: %r", raw)
        return ""

    words = stripped.split()
    if len(words) == 1 and len(stripped) < _MIN_SINGLE_WORD_CHARS:
        logger.warning("[TTS-SAFE] Suppressed short single-word fragment: %r", raw)
        return ""

    return stripped


def before_tts_cb(_agent, text: str) -> str:
    """LiveKit-compatible TTS pre-filter — return \"\" to skip synthesis."""
    return filter_tts_fragment(text)


def prepare_tts_reply_text(
    text: str,
    *,
    apply_marker: Callable[[str, str, str], str],
    tts_lang: str,
    tts_provider: str = "sarvam",
) -> str | None:
    """Return speakable full-reply text for Sarvam, or None to skip synthesis."""
    cleaned = apply_marker((text or "").strip(), tts_lang, tts_provider)
    if not cleaned:
        return None

    safe = filter_tts_fragment(cleaned)
    if not safe:
        return None

    if is_allowed_brief_ack(safe):
        return safe

    try:
        from indic_sentence_tokenizer import grapheme_count
    except ImportError:
        grapheme_count = lambda s: len(s.strip())  # noqa: E731

    if grapheme_count(safe) < _MIN_GRAPHEMES:
        logger.warning("[TTS-SAFE] Suppressed reply below minimum grapheme count: %r", safe)
        return None

    return safe


_GREETING_OPENERS = ("Hello!", "Hi!", "Namaste", "నమస్కారం", "नमस्ते")


def sanitize_tts_stutter(text: str) -> str:
    """Collapse streaming stutter like 'Hello! IHello!'."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    fixed = regex.sub(
        r"^(Hello!|Hi!|Namaste)\s*I\1\s*",
        r"\1 ",
        stripped,
        flags=regex.IGNORECASE,
    )
    fixed = regex.sub(
        r"^(Hello!|Hi!|Namaste)\s*I(?=\1)",
        r"\1",
        fixed,
        flags=regex.IGNORECASE,
    )
    return regex.sub(r"(.{4,}?)\1+", r"\1", fixed)


def merge_tts_hold_segment(hold: str, segment: str) -> str:
    """Merge LLM segments without duplicating repeated greeting openers."""
    segment = sanitize_tts_stutter(segment)
    if not hold:
        return segment
    if not segment:
        return hold

    for opener in _GREETING_OPENERS:
        if segment.startswith(opener) and opener in hold[: len(opener) + 8]:
            return segment

    max_k = min(len(hold), len(segment))
    for k in range(max_k, 0, -1):
        if hold[-k:] == segment[:k]:
            return sanitize_tts_stutter(hold + segment[k:])

    return sanitize_tts_stutter(hold + segment)


def gate_assistant_speech_text(
    text: str,
    *,
    apply_marker: Callable[[str, str, str], str],
    tts_lang: str,
    tts_provider: str = "sarvam",
    lang_preset: str | None = None,
) -> str | None:
    """Single-entry TTS gate — all assistant audio must pass this."""
    cleaned = sanitize_tts_stutter((text or "").strip())
    if not cleaned:
        return None
    try:
        from tts_normalizer import normalize_for_tts

        cleaned = normalize_for_tts(cleaned, lang_preset=lang_preset)
    except Exception:
        pass
    return prepare_tts_reply_text(
        cleaned,
        apply_marker=apply_marker,
        tts_lang=tts_lang,
        tts_provider=tts_provider,
    )


def drain_speakable_tts_chunks(hold: str) -> tuple[list[str], str]:
    """
    Drain clause/sentence-sized chunks from a cross-segment hold buffer.

    LiveKit may invoke tts_node once per FlushSentinel; this keeps micro-fragments
    like \"Right,\" buffered until a stable boundary is reached.
    """
    from indic_sentence_tokenizer import pop_speakable_tts_chunk, prepare_tts_segment_flush

    remaining = hold or ""
    ready: list[str] = []
    while remaining:
        chunk, remaining = pop_speakable_tts_chunk(remaining)
        if not chunk:
            break
        ready.append(chunk)

    flushed, leftover = prepare_tts_segment_flush(remaining)
    if flushed and not is_incomplete_speakable_clause(flushed):
        ready.append(flushed)
        leftover = leftover if leftover != remaining else ""
    elif flushed:
        logger.debug("[TTS-SAFE] Held back segment-end fragment: %r", flushed[:80])

    speakable: list[str] = []
    for chunk in ready:
        if is_incomplete_speakable_clause(chunk):
            leftover = f"{chunk} {leftover}".strip()
            logger.debug("[TTS-SAFE] Held back drained chunk: %r", chunk[:80])
        else:
            speakable.append(chunk)

    return speakable, leftover


def _trim_to_last_boundary(text: str) -> str:
    """Return text up to the last sentence/clause boundary, stripping trailing incomplete words."""
    if not text:
        return ""
    matches = list(_SENTENCE_END.finditer(text))
    if not matches:
        return ""
    last = matches[-1]
    return text[: last.end()].strip()
