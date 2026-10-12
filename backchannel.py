"""
Backchannel — subtle acknowledgements during long caller monologues.

Fires a short pre-cached filler when the caller speaks continuously (>threshold)
without agent audio. Low latency: uses greeting_cache PCM when available.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

DEFAULT_MONOLOGUE_MS = 2800
_COOLDOWN_MS = 4500

_BACKCHANNEL_TEXT: dict[str, list[str]] = {
    "tinglish": ["Avunu andi.", "Haan andi.", "Sare.", "Hmm."],
    "hte": ["Haan ji.", "Ji.", "Accha.", "Hmm."],
    "english": ["Mm-hmm.", "Right.", "Okay.", "I see."],
}


class BackchannelController:
    """Track partial STT duration and inject brief acks when appropriate."""

    def __init__(
        self,
        *,
        agent: Any = None,
        live_config: dict | None = None,
        monologue_threshold_ms: float = DEFAULT_MONOLOGUE_MS,
    ):
        self._agent = agent
        self._live_config = live_config or {}
        self._threshold_ms = monologue_threshold_ms
        self._partial_start: float | None = None
        self._last_backchannel_ts: float = 0.0
        self._user_speaking = False
        self._count = 0

    def enabled(self) -> bool:
        return bool(self._live_config.get("backchannel_enabled", False))

    def on_partial_transcript(self, transcript: str) -> None:
        if not self.enabled() or not (transcript or "").strip():
            return
        now = time.monotonic()
        if self._partial_start is None:
            self._partial_start = now
        self._user_speaking = True

        elapsed_ms = (now - self._partial_start) * 1000
        if elapsed_ms < self._threshold_ms:
            return
        if now - self._last_backchannel_ts < _COOLDOWN_MS / 1000:
            return

        self._last_backchannel_ts = now
        self._partial_start = now
        self._count += 1

        import asyncio

        asyncio.ensure_future(self._fire_backchannel())

    def on_user_final(self) -> None:
        self._partial_start = None
        self._user_speaking = False

    def on_agent_speaking(self) -> None:
        self._partial_start = None
        self._user_speaking = False

    async def _fire_backchannel(self) -> None:
        if not self._agent:
            return
        from language_router import resolve_lang_preset
        from knowledge_pack import resolve_preset_pack

        preset = resolve_lang_preset(self._live_config.get("lang_preset"), self._live_config)
        pack = resolve_preset_pack(preset)
        pool = _BACKCHANNEL_TEXT.get(pack) or _BACKCHANNEL_TEXT.get("english", ["Mm-hmm."])

        # Prefer a pre-baked PCM phrase (speak_gated then plays cache, no live TTS
        # — ~100ms faster); fall back to any phrase via live TTS.
        candidates = random.sample(pool, len(pool))
        phrase = candidates[0]
        try:
            from greeting_cache import get_reply_pcm

            for cand in candidates:
                if get_reply_pcm(cand, lang_preset=preset):
                    phrase = cand
                    break
        except Exception:
            pass

        speak = getattr(self._agent, "speak_gated", None)
        if not callable(speak):
            return
        try:
            await speak(phrase, add_to_chat_ctx=False)
            logger.debug("[BACKCHANNEL] %r", phrase)
        except Exception as exc:
            logger.debug("[BACKCHANNEL] Failed: %s", exc)

    def stats(self) -> dict[str, int]:
        return {"backchannel_count": self._count}