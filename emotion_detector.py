"""
Real-time emotion detection from caller transcripts.

Keyword patterns inject temporary system annotations for tone adaptation.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("inbound-voice-agent")

EMOTION_PATTERNS: dict[str, list[str]] = {
    "frustrated": [
        "already told", "told you", "again", "not understanding",
        "baar baar", "phir se", "samjh nahi", "bore", "irritating",
        "why", "kyu", "again and again",
    ],
    "enthusiastic": [
        "amazing", "perfect", "love it", "exactly", "great", "wonderful",
        "bilkul", "perfect hai", "accha", "bahut accha", "yes yes",
    ],
    "hesitant": [
        "let me think", "sochna padega", "not sure", "maybe", "shayad",
        "need to discuss", "wife ko poochna hai", "husband ko", "later",
        "baad mein", "call karunga", "will think",
    ],
}

EMOTION_INJECTIONS: dict[str, str] = {
    "frustrated": (
        "[CALLER EMOTION: frustrated] Slow down. Acknowledge their frustration explicitly "
        "('Samajh gaya, aapko inconvenience hua'). Do NOT repeat what you just said. "
        "Ask ONE simple question. Keep response under 2 sentences."
    ),
    "enthusiastic": (
        "[CALLER EMOTION: enthusiastic] Match their energy. Move decisively toward "
        "the next concrete step (viewing / offer). Don't waste time on features they've "
        "already approved. Ask a closing question."
    ),
    "hesitant": (
        "[CALLER EMOTION: hesitant] Give space. Don't fill silence with more information. "
        "Offer a low-commitment next step ('Ek baar dekhne aiye, koi commitment nahi'). "
        "Keep it to 1 sentence."
    ),
}


def detect_emotion(text: str) -> str | None:
    if not text:
        return None
    text_lower = text.lower()
    for emotion, patterns in EMOTION_PATTERNS.items():
        if any(p in text_lower for p in patterns):
            return emotion
    return None


def inject_emotion_annotation(
    chat_ctx,
    emotion: str,
    *,
    remove_after_seconds: float = 2.0,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Append a temporary system message; schedule removal after this turn."""
    injection = EMOTION_INJECTIONS.get(emotion)
    if not injection:
        return
    try:
        chat_ctx.append(role="system", text=injection)
    except TypeError:
        chat_ctx.append(role="system", content=injection)

    def _remove_last_emotion_msg():
        try:
            messages = chat_ctx.messages
            if callable(messages):
                messages = messages()
            for i in range(len(messages) - 1, -1, -1):
                msg = messages[i]
                role = getattr(msg, "role", None)
                content = getattr(msg, "content", None) or getattr(msg, "text", "")
                if role == "system" and isinstance(content, str) and content.startswith("[CALLER EMOTION:"):
                    try:
                        chat_ctx.messages.remove(msg)
                    except Exception:
                        pass
                    break
        except Exception as e:
            logger.debug("[EMOTION] Failed to remove annotation: %s", e)

    event_loop = loop or asyncio.get_event_loop()
    event_loop.call_later(remove_after_seconds, _remove_last_emotion_msg)