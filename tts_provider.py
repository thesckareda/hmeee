"""
TTS provider factory — switch with one config key: tts_provider

  cartesia → Cartesia Sonic TTS (LiveKit plugin, Sarvam fallback)
  gemini   → Gemini 3.1 Flash TTS (chunked stream via native .stream())
  sarvam   → Sarvam Bulbul v3 (fallback / manual override)

Gemini streams completed generation chunks (per sentence), not sub-sentence
real-time audio. Plugins with streaming=True skip StreamAdapter; non-streaming
plugins are wrapped automatically for the LiveKit generate_reply path.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from livekit.agents import tts as lk_tts
from livekit.plugins import sarvam

logger = logging.getLogger("inbound-voice-agent")


def _wrap_with_stream_adapter(base_tts: Any) -> Any:
    """Non-streaming TTS plugins need StreamAdapter for LiveKit generate_reply path."""
    if getattr(getattr(base_tts, "capabilities", None), "streaming", False):
        return base_tts
    try:
        from indic_sentence_tokenizer import IndicSentenceTokenizer

        sentence_tokenizer = IndicSentenceTokenizer(retain_format=True)
    except ImportError:
        from livekit.agents import tokenize

        sentence_tokenizer = tokenize.basic.SentenceTokenizer(retain_format=True)
    return lk_tts.StreamAdapter(tts=base_tts, sentence_tokenizer=sentence_tokenizer)

DEFAULT_TTS_PROVIDER = "gemini"


def resolve_tts_provider(live_config: dict) -> str:
    raw = (
        live_config.get("tts_provider")
        or os.environ.get("TTS_PROVIDER")
        or DEFAULT_TTS_PROVIDER
    )
    return str(raw).strip().lower()


def _build_sarvam_tts(
    *,
    live_config: dict,
    tts_voice: str,
    tts_language: str,
    configure_timeouts: Callable,
) -> sarvam.TTS:
    from language_router import normalize_sarvam_voice, to_sarvam_tts_lang

    sarvam_lang = to_sarvam_tts_lang(
        live_config.get("tts_language_sarvam") or tts_language
    )
    voice = normalize_sarvam_voice(tts_voice)
    instance = sarvam.TTS(
        target_language_code=sarvam_lang,
        model="bulbul:v3",
        speaker=voice,
        speech_sample_rate=24000,
        enable_preprocessing=True,
    )
    return configure_timeouts(instance)


def build_agent_tts(
    *,
    live_config: dict,
    tts_voice: str,
    tts_language: str,
    get_secret: Callable[..., str],
    configure_sarvam_timeouts: Callable,
) -> tuple[Any, str]:
    """
    Build TTS for the call. Returns (tts_instance, effective_provider).

    Set tts_provider to "sarvam" in config.json to force Sarvam.
    """
    requested = resolve_tts_provider(live_config)

    match requested:
        case "cartesia":
            try:
                from cartesia_tts import build_cartesia_tts, resolve_cartesia_voice, to_cartesia_lang

                voice = resolve_cartesia_voice(tts_voice)
                lang = to_cartesia_lang(tts_language)
                base = build_cartesia_tts(
                    live_config=live_config,
                    tts_voice=voice,
                    tts_language=tts_language,
                    get_secret=get_secret,
                )
                instance = _wrap_with_stream_adapter(base)
                model = (
                    live_config.get("cartesia_tts_model")
                    or os.environ.get("CARTESIA_TTS_MODEL")
                    or "sonic-3.5"
                )
                mode = (
                    "native stream"
                    if getattr(getattr(base, "capabilities", None), "streaming", False)
                    else "StreamAdapter"
                )
                logger.info(
                    "[TTS] Using Cartesia %s — voice=%s lang=%s (%s)",
                    model,
                    voice,
                    lang,
                    mode,
                )
                return instance, "cartesia"
            except Exception as exc:
                logger.warning(
                    "[TTS] Cartesia TTS unavailable (%s) — falling back to Sarvam",
                    exc,
                )

        case "gemini":
            try:
                from gemini_tts import (
                    DEFAULT_GEMINI_TTS_MODEL,
                    TTS as GeminiTTS,
                    normalize_gemini_voice,
                    resolve_gemini_tts_api_keys_for_call,
                )

                gemini_model = (
                    live_config.get("gemini_tts_model")
                    or os.environ.get("GEMINI_TTS_MODEL")
                    or DEFAULT_GEMINI_TTS_MODEL
                )
                voice = normalize_gemini_voice(tts_voice)
                api_keys = resolve_gemini_tts_api_keys_for_call(live_config, get_secret)
                base = GeminiTTS(
                    voice_name=voice,
                    model=gemini_model,
                    api_keys=api_keys,
                )
                instance = _wrap_with_stream_adapter(base)
                mode = (
                    "native stream"
                    if getattr(getattr(base, "capabilities", None), "streaming", False)
                    else "StreamAdapter"
                )
                logger.info(
                    "[TTS] Using Gemini Flash TTS — model=%s voice=%s (%s)",
                    gemini_model,
                    voice,
                    mode,
                )
                return instance, "gemini"
            except Exception as exc:
                from gemini_tts import tts_fallbacks_enabled

                if not tts_fallbacks_enabled():
                    raise
                logger.warning(
                    "[TTS] Gemini TTS unavailable (%s) — falling back to Sarvam",
                    exc,
                )

        case "elevenlabs":
            try:
                from livekit.plugins import elevenlabs

                voice_id = live_config.get("elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM")
                instance = elevenlabs.TTS(model="eleven_turbo_v2_5", voice_id=voice_id)
                logger.info("[TTS] Using ElevenLabs Turbo — voice=%s", voice_id)
                return instance, "elevenlabs"
            except ImportError:
                logger.warning("[TTS] elevenlabs plugin missing — falling back to Sarvam")

        case "sarvam":
            pass

        case _:
            logger.warning("[TTS] Unknown tts_provider '%s' — using Sarvam", requested)

    instance = _build_sarvam_tts(
        live_config=live_config,
        tts_voice=tts_voice,
        tts_language=tts_language,
        configure_timeouts=configure_sarvam_timeouts,
    )
    from language_router import to_sarvam_tts_lang, normalize_sarvam_voice

    logger.info(
        "[TTS] Using Sarvam Bulbul v3 — voice=%s lang=%s",
        normalize_sarvam_voice(tts_voice),
        to_sarvam_tts_lang(live_config.get("tts_language_sarvam") or tts_language),
    )
    return instance, "sarvam"